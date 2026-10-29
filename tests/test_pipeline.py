import json
from collections import Counter
from pathlib import Path
from app.db import Store
from app.logic import Processor
from app.models import LeadInput, Analysis
from app.services import MockEmail, MockSheet, FixtureAnalyzer

SAMPLES = json.loads((Path(__file__).parent.parent / "examples" / "test_messages.json").read_text(encoding="utf-8"))
EXPECTED = ["tarjouspyyntö", "asiakkaan kysymys", "roskaposti", "asiakkaan kysymys",
            "tarjouspyyntö", "ihmiselle", "ihmiselle", "tarjouspyyntö", "muu", "ihmiselle"]


def test_all_ten_with_fixture_and_notification_rules(settings, store):
    for i, item in enumerate(SAMPLES):
        store.accept(LeadInput(**item), f"example-{i}")
    p = Processor(store, settings)
    count = 0
    while p.process_one():
        count += 1
    assert count == 10
    rows = store.list_messages()
    # Ordering is based on id for messages sharing the same second, hence match via email.
    result = {r["email"]:r for r in rows}
    for item, category in zip(SAMPLES, EXPECTED):
        row = result[item["email"]]
        assert row["category"] == category
        assert row["status"] == "completed"
        assert row["sheet_done"] == 1
        assert row["email_done"] == int(category in {"tarjouspyyntö", "ihmiselle"})
    csv = (Path(settings.mock_dir)/"leads.csv").read_text(encoding="utf-8")
    assert len(csv.splitlines()) == 11
    emails = [json.loads(line) for line in (Path(settings.mock_dir)/"outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(emails) == 6


class FlakySheet:
    def __init__(self, target, fail_after_write=False):
        self.target = target
        self.calls = 0
        self.fail_after_write = fail_after_write

    def append_once(self, id, row):
        self.calls += 1
        if self.calls == 1 and not self.fail_after_write:
            raise ConnectionError("Simulated Sheets unavailable")
        self.target.append_once(id, row)
        if self.calls == 1 and self.fail_after_write:
            raise TimeoutError("Simulated lost HTTP response after remote commit")


def test_sheet_error_retains_message_and_can_retry(settings, store):
    id, _ = store.accept(LeadInput(**SAMPLES[0]), "retry-test")
    flaky = FlakySheet(MockSheet(settings))
    p = Processor(store, settings, FixtureAnalyzer(), flaky, MockEmail(settings))
    assert p.process_one()
    failed = store.get(id)
    assert failed["status"] == "retry" and failed["category"] == "tarjouspyyntö"
    assert failed["sheet_done"] == 0
    assert failed["error_alert_done"] == 0
    assert any(e["event"] == "retry_scheduled" for e in store.list_events(id))
    assert any(e["event"] == "failed" for e in store.list_events(id))
    store.reset(id)
    assert p.process_one()
    done = store.get(id)
    assert done["status"] == "completed" and done["sheet_done"] == 1
    assert flaky.calls == 2


def test_uncertain_sheet_commit_does_not_duplicate(settings, store):
    id, _ = store.accept(LeadInput(**SAMPLES[0]), "uncertain-commit")
    p = Processor(store, settings, FixtureAnalyzer(), FlakySheet(MockSheet(settings), True), MockEmail(settings))
    p.process_one()
    store.reset(id)
    p.process_one()
    import csv
    with (Path(settings.mock_dir) / "leads.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file))
    assert len(rows) == 2 and rows[1][5] == id


class FlakyEmail:
    def __init__(self):
        self.sent = []
        self.fail = True

    def send(self, recipient, subject, body, notification_id):
        if self.fail:
            raise ConnectionError("SMTP offline")
        self.sent.append(notification_id)


def test_email_failure_is_recorded_and_recovered(settings, store):
    id, _ = store.accept(LeadInput(**SAMPLES[0]), "email-test")
    email = FlakyEmail()
    p = Processor(store, settings, FixtureAnalyzer(), MockSheet(settings), email)
    p.process_one()
    row = store.get(id)
    assert row["status"] == "retry" and row["sheet_done"] == 1
    assert row["email_done"] == 0 and row["error_alert_done"] == 0
    email.fail = False
    store.reset(id)
    p.process_one()
    assert store.get(id)["status"] == "completed"
    assert email.sent == ["lead-" + id]


def test_uncertain_model_is_sent_to_human(settings, store):
    id, _ = store.accept(LeadInput(**SAMPLES[9]), "uncertain-model")

    class Confused:
        def analyze(self, lead):
            return Analysis(category="tarjouspyyntö", summary="Tiedustelu vaatii tarkistusta.",
                            ambiguous=True, confidence=0.9)

    p = Processor(store, settings, Confused(), MockSheet(settings), MockEmail(settings))
    p.process_one()
    assert store.get(id)["category"] == "ihmiselle"
    assert store.get(id)["email_done"] == 1

class AlwaysFailsAnalyzer:
    def analyze(self, lead):
        raise ConnectionError("Gemini mock outage")


def test_llm_failure_keeps_original_and_stops_after_limit(settings, store):
    from dataclasses import replace
    settings = replace(settings, retry_limit=2)
    store = Store(settings)
    id, _ = store.accept(LeadInput(**SAMPLES[0]), "ai-outage")
    p = Processor(store, settings, AlwaysFailsAnalyzer(), MockSheet(settings), MockEmail(settings))
    p.process_one()
    assert store.get(id)["status"] == "retry"
    assert store.get(id)["message"] == SAMPLES[0]["message"]
    store.reset(id)  # operator requeue resets attempts, unlike automatic retry
    p.process_one()
    assert store.get(id)["status"] == "retry"
    # Force a second due attempt without resetting retry counter:
    with store.connection() as db:
        db.execute("UPDATE messages SET next_attempt_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", id))
    p.process_one()
    assert store.get(id)["status"] == "needs_attention"
    assert store.get(id)["message"] == SAMPLES[0]["message"]


def test_expired_worker_lease_reclaims_message(settings, store):
    id, _ = store.accept(LeadInput(**SAMPLES[1]), "worker-crash")
    claimed = store.claim()
    assert claimed["id"] == id and store.get(id)["status"] == "processing"
    with store.connection() as db:
        db.execute("UPDATE messages SET lease_until=? WHERE id=?", ("2000-01-01T00:00:00+00:00", id))
    p = Processor(store, settings)
    p.process_one()
    assert store.get(id)["status"] == "completed"


def test_model_summary_is_one_sentence():
    from app.models import normalize_analysis
    category, summary = normalize_analysis(Analysis(category="muu", ambiguous=False, confidence=0.95,
        summary="Ensimmäinen lause. Toinen lause."))
    assert category == "muu" and summary == "Ensimmäinen lause."


def test_message_listing_has_no_limit_and_stable_sorting(store):
    for number in range(35):
        lead = LeadInput(name=f"Testi {number:02}", email=f"testi{number:02}@esimerkki.fi",
                         message=f"Viesti {number}")
        store.accept(lead, f"listing-{number:02}")
    rows = store.list_messages()
    assert len(rows) == 35
    assert rows == sorted(rows, key=lambda row: (row["received_at"], row["id"]))
