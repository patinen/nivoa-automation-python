"""Explicit live Google Sheets verification using only fictional assignment data."""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.google_diagnostics import diagnose_google_exception
from app.models import LeadInput, normalize_analysis
from app.services import FixtureAnalyzer, GoogleSheet


def message_id(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "nivoa-sheets-live-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def write_report(path: Path, status: str, settings: Settings, details: list[str]):
    lines = ["# Google Sheets live integration test", "",
             "**LIVE GOOGLE SHEETS TEST — NOT AN OFFLINE UNIT TEST.**", "",
             f"Status: **{status}**  ",
             f"Run (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ",
             f"Worksheet: `{settings.sheet_tab}`", "", *details, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write and verify the 10 fictional messages in Google Sheets")
    parser.add_argument("--confirm-write", action="store_true",
                        help="required acknowledgement that this command writes to the configured spreadsheet")
    parser.add_argument("--output", default="evidence/google-sheets-live-test.md")
    args = parser.parse_args()
    if not args.confirm_write:
        parser.error("--confirm-write is required; this command modifies the configured spreadsheet")

    settings = Settings.from_env()
    if settings.sheet_mode != "google":
        parser.error("Set SHEET_MODE=google for the live Sheets test")
    output = Path(args.output)
    root = Path(__file__).parent.parent
    payloads = json.loads((root / "examples" / "test_messages.json").read_text(encoding="utf-8"))
    analyzer = FixtureAnalyzer()  # deterministic; this test never calls Gemini
    ids = [message_id(payload) for payload in payloads]

    try:
        sheet = GoogleSheet(settings)
        for payload, id in zip(payloads, ids):
            lead = LeadInput(**payload)
            category, summary = normalize_analysis(analyzer.analyze(lead))
            contact = str(lead.email) + (f" / {lead.phone}" if lead.phone else "")
            row = [datetime.now(timezone.utc).isoformat(timespec="seconds"), lead.name,
                   contact, category, summary]
            sheet.append_once(id, row)
        # Deliberately repeat every append to verify retry/idempotency behavior live.
        for payload, id in zip(payloads, ids):
            lead = LeadInput(**payload)
            category, summary = normalize_analysis(analyzer.analyze(lead))
            contact = str(lead.email) + (f" / {lead.phone}" if lead.phone else "")
            row = [datetime.now(timezone.utc).isoformat(timespec="seconds"), lead.name,
                   contact, category, summary]
            sheet.append_once(id, row)

        rows = sheet.rows()
        counts = Counter(row[5] for row in rows[1:] if len(row) >= 6)
        bad = {id: counts[id] for id in ids if counts[id] != 1}
        header_ok = bool(rows) and rows[0] == GoogleSheet.HEADERS
        if bad or not header_ok:
            details = ["The API calls executed, but verification failed.", "",
                       f"- Header valid: `{header_ok}`",
                       f"- Unique fictional message rows: `{sum(counts[id] == 1 for id in ids)}/10`",
                       f"- Incorrect ID counts: `{bad}`"]
            write_report(output, "EXECUTED — FAILED", settings, details)
            print(f"Live Sheets verification failed; see {output}")
            return 1
        details = ["The API test executed successfully.", "",
                   "- 10/10 deterministic message IDs occur exactly once.",
                   "- A second append attempt for every message created no duplicates.",
                   "- The six-column header matches the required schema.",
                   "- Gemini was not called; fixture classifications were used."]
        write_report(output, "EXECUTED — PASSED", settings, details)
        print(f"Live Sheets verification passed; report written to {output}")
        return 0
    except Exception as exc:
        diagnostic = diagnose_google_exception(exc, "live Sheets test")
        details = ["The live result is unverified because setup or an API request failed.", "",
                   *diagnostic.evidence_lines(), "",
                   "OAuth tokens, secrets, authorization headers, and request URLs were not recorded."]
        write_report(output, "UNVERIFIED", settings, details)
        status = diagnostic.status_code if diagnostic.status_code is not None else "no HTTP status"
        print(f"Live Sheets test unverified ({status}, {diagnostic.reason}); see {output}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
