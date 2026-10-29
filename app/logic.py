"""Incremental and recoverable message processing."""
import json
import hashlib
import logging
import re

from app.config import Settings
from app.db import Store
from app.models import LeadInput, normalize_analysis
from app.services import build_adapters, is_retryable_failure

log = logging.getLogger(__name__)
NEEDS_EMAIL = {"tarjouspyyntö", "ihmiselle"}


def notification_content(row: dict) -> tuple[str, str]:
    if row["category"] == "ihmiselle":
        heading = "MANUAALINEN KÄSITTELY TARVITAAN"
        subject = f"[Nivoa] Manuaalinen käsittely: {row['name']}"
        guidance = "Luokitus on epävarma tai viesti vaatii ihmisen arviota."
    else:
        heading = "UUSI TARJOUSPYYNTÖ"
        subject = f"[Nivoa] Uusi tarjouspyyntö: {row['name']}"
        guidance = "Viesti on luokiteltu tarjouspyynnöksi."
    contact = row["email"] + (f" / {row['phone']}" if row["phone"] else "")
    body = (f"{heading}\n{'=' * len(heading)}\n\n"
            f"{guidance}\n\n"
            f"Luokka: {row['category']}\n"
            f"Vastaanotettu (UTC): {row['received_at']}\n"
            f"Lähettäjä: {row['name']}\n"
            f"Yhteystiedot: {contact}\n"
            f"Tiivistelmä: {row['summary']}\n\n"
            f"Alkuperäinen viesti:\n{row['message'] or '(tyhjä viesti)'}\n\n"
            f"Sisäinen viesti-ID: {row['id']}\n")
    return subject, body


class Processor:
    def __init__(self, store: Store, settings: Settings, analyzer=None, sheet=None, notifier=None):
        self.store = store
        self.settings = settings
        defaults = None
        if analyzer is None or sheet is None or notifier is None:
            defaults = build_adapters(settings)
        self.analyzer = analyzer or defaults[0]
        self.sheet = sheet or defaults[1]
        self.notifier = notifier or defaults[2]

    def process_one(self) -> bool:
        row = self.store.claim()
        if not row:
            return False
        step = "analysis"
        try:
            if row["category"] is None:
                lead = LeadInput(name=row["name"], email=row["email"],
                                 phone=row["phone"], message=row["message"])
                category, summary = normalize_analysis(self.analyzer.analyze(lead))
                if not summary:
                    raise ValueError("Empty AI summary")
                self.store.analyzed(row["id"], category, summary)
                row = self.store.get(row["id"])
            step = "sheet"
            if not row["sheet_done"]:
                contact = row["email"] + (f" / {row['phone']}" if row["phone"] else "")
                sheet_row = [row["received_at"], row["name"], contact,
                             row["category"], row["summary"]]
                self.sheet.append_once(row["id"], sheet_row)
                self.store.stage_done(row["id"], "sheet_done")
            step = "email"
            if row["category"] in NEEDS_EMAIL and not row["email_done"]:
                to = self.settings.owner_email or "owner@example.test"
                subject, body = notification_content(row)
                notification_id = "lead-" + row["id"]
                event_detail = json.dumps({"notification_id": notification_id, "recipient": to})
                self.store.record_event(row["id"], "email_attempt", event_detail)
                self.notifier.send(to, subject, body, notification_id)
                self.store.record_event(row["id"], "email_smtp_accepted", event_detail)
                self.store.stage_done(row["id"], "email_done")
            self.store.complete(row["id"])
            log.info("Processed %s -> %s", row["id"], row["category"])
        except Exception as exc:
            # Persist first. Alerting must never destroy or overwrite the original message.
            retryable = is_retryable_failure(exc)
            status, _, _ = self.store.fail(
                row["id"], row["attempts"], step, f"{type(exc).__name__}: {exc}", retryable)
            log.exception("Processing failed for message %s at %s", row["id"], step)
            if status == "needs_attention":
                self.report_failure(row["id"], step)
        return True

    def report_failure(self, id: str, step: str):
        row = self.store.get(id)
        if row["error_alert_done"] or row["status"] != "needs_attention":
            return
        normalized = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", row["last_error"], flags=re.I)
        fingerprint = hashlib.sha256(f"{step}:{normalized}".encode("utf-8")).hexdigest()
        if self.store.incident_alert_exists(fingerprint):
            self.store.mark_error_alert_handled(id, "failure_alert_suppressed_duplicate")
            return
        to = self.settings.error_email or self.settings.owner_email or "owner@example.test"
        try:
            self.notifier.send(
                to, f"[Tilitoimisto] Automaatio epäonnistui ({step})",
                f"Viesti-ID: {id}\nVaihe: {step}\nVirhe: {row['last_error']}\n"
                f"Tila: {row['status']}\nViesti on tallella SQLite-tietokannassa.\n",
                "error-" + id + "-" + str(row["attempts"]),
            )
            self.store.record_incident_alert(fingerprint, id)
            self.store.mark_error_alert_handled(id, "failure_alert_sent")
        except Exception:
            # If SMTP itself is down, the DB event log + application logs remain.
            # A separate alert channel would be required for delivery guarantees.
            log.exception("Could not deliver failure alert for %s; inspect DB events", id)
