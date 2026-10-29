"""Explicit live SMTP test; sends only fictional messages to OWNER_EMAIL."""
import argparse
import json
import smtplib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.logic import notification_content
from app.models import LeadInput, normalize_analysis
from app.services import FixtureAnalyzer, SMTPEmail


def write_report(path: Path, status: str, settings: Settings, details: list[str]):
    lines = ["# SMTP live integration test", "",
             "**LIVE SMTP TEST — NOT AN OFFLINE UNIT TEST.**", "",
             f"Status: **{status}**  ",
             f"Run (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ",
             f"Security mode: `{settings.smtp_security}`", "", *details, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send two fictional Nivoa notifications to OWNER_EMAIL")
    parser.add_argument("--confirm-send", action="store_true",
                        help="required acknowledgement that this command sends real email")
    parser.add_argument("--output", default="evidence/smtp-live-test.md")
    args = parser.parse_args()
    if not args.confirm_send:
        parser.error("--confirm-send is required; this command sends real email")

    settings = Settings.from_env()
    if settings.email_mode != "smtp":
        parser.error("Set EMAIL_MODE=smtp for the live SMTP test")
    output = Path(args.output)
    root = Path(__file__).parent.parent
    payloads = json.loads((root / "examples" / "test_messages.json").read_text(encoding="utf-8"))
    # One quotation request and one ambiguous/human-review case; both are fictional.
    selected = [payloads[0], payloads[9]]
    analyzer = FixtureAnalyzer()

    try:
        mailer = SMTPEmail(settings)
        for payload in selected:
            lead = LeadInput(**payload)
            category, summary = normalize_analysis(analyzer.analyze(lead))
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            row = {"category": category, "name": lead.name, "email": str(lead.email),
                   "phone": lead.phone, "summary": summary, "message": lead.message,
                   "received_at": now, "id": "smtp-live-" + uuid4().hex}
            subject, body = notification_content(row)
            # Never use the fictional sender address as a recipient.
            mailer.send(settings.owner_email, subject, body, "live-" + row["id"])
        write_report(output, "EXECUTED — SMTP ACCEPTED", settings, [
            "The SMTP server accepted two messages for the configured owner/test recipient.", "",
            "- One fictional quotation-request notification was submitted.",
            "- One fictional human-review notification was submitted.",
            "- No fictional contact address was used as a recipient.",
            "- SMTP acceptance does not prove final inbox delivery.",
        ])
        print(f"Live SMTP test accepted; report written to {output}")
        return 0
    except Exception as exc:
        code = getattr(exc, "smtp_code", None)
        details = ["The live SMTP result is unverified because sending failed.", "",
                   f"- Failure type: `{type(exc).__name__}`",
                   f"- SMTP status: `{code if code is not None else 'not available'}`",
                   "- No password, SMTP response body, or credential value was recorded."]
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            details.append("- Suggested action: verify the Gmail address, App Password, and 2-Step Verification.")
        write_report(output, "UNVERIFIED", settings, details)
        print(f"Live SMTP test unverified ({type(exc).__name__}); see {output}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
