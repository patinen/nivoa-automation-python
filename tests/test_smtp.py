import smtplib
from dataclasses import replace

import pytest

from app.logic import Processor, notification_content
from app.models import Analysis, LeadInput
from app.services import FixtureAnalyzer, MockSheet, SMTPEmail


class FakeSMTP:
    def __init__(self, send_error=None, login_error=None, refused=None):
        self.send_error = send_error
        self.login_error = login_error
        self.refused = refused or {}
        self.ehlo_calls = 0
        self.starttls_context = None
        self.login_args = None
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self, context=None):
        self.starttls_context = context

    def login(self, username, password):
        self.login_args = (username, password)
        if self.login_error:
            raise self.login_error

    def send_message(self, message):
        self.messages.append(message)
        if self.send_error:
            raise self.send_error
        return self.refused


def smtp_settings(settings, security="starttls"):
    return replace(settings, email_mode="smtp", smtp_host="smtp.gmail.com",
                   smtp_port=587 if security == "starttls" else 465,
                   smtp_security=security, smtp_timeout_seconds=45,
                   smtp_username="sender@example.test", smtp_password="app-password",
                   email_from="sender@example.test", owner_email="owner@example.test")


def notifier(settings, server, security="starttls", calls=None):
    configured = smtp_settings(settings, security)

    def factory(host, port, **kwargs):
        if calls is not None:
            calls.append((host, port, kwargs))
        return server

    return SMTPEmail(configured, smtp_factory=factory,
                     ssl_context_factory=lambda: "secure-context")


def test_smtp_starttls_success_and_plain_text_template(settings):
    server = FakeSMTP()
    calls = []
    mailer = notifier(settings, server, calls=calls)
    row = {"category": "tarjouspyyntö", "name": "Matti Esimerkki",
           "email": "matti@esimerkki.fi", "phone": "+358 40 123 4567",
           "summary": "Yritys pyytää tarjousta kirjanpidosta.",
           "message": "Tarvitsemme kirjanpitäjän.",
           "received_at": "2026-10-29T10:00:00+00:00", "id": "message-1"}
    subject, body = notification_content(row)
    mailer.send("owner@example.test", subject, body, "lead-message-1")
    assert calls == [("smtp.gmail.com", 587, {"timeout": 45})]
    assert server.ehlo_calls == 2 and server.starttls_context == "secure-context"
    assert server.login_args == ("sender@example.test", "app-password")
    message = server.messages[0]
    assert message["To"] == "owner@example.test"
    assert "Uusi tarjouspyyntö" in message["Subject"]
    content = message.get_content()
    for expected in ["Matti Esimerkki", "matti@esimerkki.fi / +358 40 123 4567",
                     row["summary"], row["message"], row["received_at"], row["id"]]:
        assert expected in content


def test_smtp_ssl_success_does_not_starttls(settings):
    server = FakeSMTP()
    calls = []
    mailer = notifier(settings, server, security="ssl", calls=calls)
    mailer.send("owner@example.test", "Testi", "Sisältö", "test-ssl")
    assert calls == [("smtp.gmail.com", 465, {"timeout": 45, "context": "secure-context"})]
    assert server.starttls_context is None


@pytest.mark.parametrize("error", [
    smtplib.SMTPAuthenticationError(535, b"Authentication failed"),
    TimeoutError("SMTP timed out"),
    smtplib.SMTPServerDisconnected("Temporary SMTP disconnect"),
])
def test_smtp_failures_propagate(settings, error):
    server = FakeSMTP(login_error=error if isinstance(error, smtplib.SMTPAuthenticationError) else None,
                      send_error=None if isinstance(error, smtplib.SMTPAuthenticationError) else error)
    with pytest.raises(type(error)):
        notifier(settings, server).send("owner@example.test", "Testi", "Sisältö", "test-error")


def test_human_review_template_is_clearly_distinct():
    row = {"category": "ihmiselle", "name": "Kalle Esimerkki",
           "email": "kalle@esimerkki.fi", "phone": None,
           "summary": "Viesti vaatii ihmisen arvion.", "message": "Mitä suosittelette?",
           "received_at": "2026-10-29T10:00:00+00:00", "id": "message-human"}
    subject, body = notification_content(row)
    assert "Manuaalinen käsittely" in subject
    assert "MANUAALINEN KÄSITTELY TARVITAAN" in body
    assert "ihmisen arviota" in body


def test_no_email_for_non_notification_category(settings, store):
    class OtherAnalyzer:
        def analyze(self, lead):
            return Analysis(category="muu", summary="Lähettäjä kiittää tapaamisesta.",
                            ambiguous=False, confidence=0.99)

    class MustNotSend:
        def send(self, *args, **kwargs):
            raise AssertionError("notification must not be sent for category muu")

    lead = LeadInput(name="Hanna Malli", email="hanna@esimerkki.fi", message="Kiitos tapaamisesta!")
    id, _ = store.accept(lead, "no-email-muu")
    Processor(store, settings, OtherAnalyzer(), MockSheet(settings), MustNotSend()).process_one()
    row = store.get(id)
    assert row["status"] == "completed" and row["email_done"] == 0


def test_smtp_retry_preserves_sheet_checkpoint_and_tracks_attempts(settings, store):
    configured = smtp_settings(settings)
    state = {"fail": True, "servers": []}

    def factory(host, port, **kwargs):
        server = FakeSMTP(send_error=ConnectionError("SMTP offline") if state["fail"] else None)
        state["servers"].append(server)
        return server

    mailer = SMTPEmail(configured, smtp_factory=factory,
                       ssl_context_factory=lambda: "secure-context")
    lead = LeadInput(name="Matti Esimerkki", email="matti@esimerkki.fi",
                     message="Tarvitsisimme tarjouksen kirjanpidosta.")
    id, _ = store.accept(lead, "smtp-retry")
    processor = Processor(store, configured, FixtureAnalyzer(), MockSheet(configured), mailer)
    processor.process_one()
    failed = store.get(id)
    assert failed["status"] == "retry" and failed["sheet_done"] == 1 and failed["email_done"] == 0
    state["fail"] = False
    store.reset(id)
    processor.process_one()
    completed = store.get(id)
    assert completed["status"] == "completed" and completed["sheet_done"] == 1 and completed["email_done"] == 1
    events = [event["event"] for event in store.list_events(id)]
    assert events.count("sheet_done") == 1
    assert events.count("email_attempt") == 2
    assert events.count("email_smtp_accepted") == 1
