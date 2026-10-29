"""Explicit end-to-end live verifier using HTTP, SQLite and all real integrations."""
import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import Settings, setting_source
from app.db import Store
from app.google_diagnostics import sanitize_text
from app.logic import NEEDS_EMAIL, Processor
from app.services import GoogleSheet


EXPECTED = ["tarjouspyyntö", "asiakkaan kysymys", "roskaposti", "asiakkaan kysymys",
            "tarjouspyyntö", "ihmiselle", "ihmiselle", "tarjouspyyntö", "muu", "ihmiselle"]


def live_preflight(settings: Settings) -> list[str]:
    """Return missing local configuration without contacting external services."""
    missing = []
    if (settings.analyzer_mode, settings.sheet_mode, settings.email_mode) != ("gemini", "google", "smtp"):
        missing.append("ANALYZER_MODE=gemini, SHEET_MODE=google and EMAIL_MODE=smtp")
    if setting_source("DATABASE_PATH") == "application default":
        missing.append("an explicit DATABASE_PATH")
    if not settings.webhook_token:
        missing.append("WEBHOOK_TOKEN")
    if not settings.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if not settings.sheet_id:
        missing.append("GOOGLE_SHEET_ID")
    if not settings.sheet_tab:
        missing.append("GOOGLE_SHEET_TAB")
    if settings.google_auth_mode == "oauth" and not Path(settings.google_token_file).is_file():
        missing.append("an existing GOOGLE_TOKEN_FILE (run app.google_auth first)")
    if settings.google_auth_mode == "service_account" and not Path(settings.google_credentials_file).is_file():
        missing.append("an existing GOOGLE_CREDENTIALS_FILE")
    for name, value in (
        ("SMTP_HOST", settings.smtp_host),
        ("SMTP_USERNAME", settings.smtp_username),
        ("SMTP_PASSWORD", settings.smtp_password),
        ("EMAIL_FROM", settings.email_from),
        ("OWNER_EMAIL", settings.owner_email),
    ):
        if not value:
            missing.append(name)
    if settings.smtp_security not in {"starttls", "ssl"}:
        missing.append("SMTP_SECURITY=starttls or ssl")
    return missing


def write_report(path: Path, status: str, run_id: str, started: str,
                 observations: list[str], table: list[str]):
    lines = ["# Nivoa end-to-end live test", "",
             "**LIVE E2E TEST — REAL GEMINI, GOOGLE SHEETS AND SMTP.**", "",
             f"Status: **{status}**  ", f"Run ID: `{sanitize_text(run_id)}`  ",
             f"Started (UTC): {started}  ",
             f"Reported (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
             *observations, "", *table, "",
             "SMTP acceptance confirms that the configured SMTP server accepted a message; "
             "it does not prove final Inbox delivery.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def event_count(store: Store, ids: list[str], kind: str) -> int:
    return sum(event["event"] == kind for id in ids for event in store.list_events(id))


def accepted_recipients(store: Store, ids: list[str]) -> list[str]:
    recipients = []
    for id in ids:
        for event in store.list_events(id):
            if event["event"] != "email_smtp_accepted":
                continue
            try:
                recipients.append(json.loads(event["detail"])["recipient"])
            except (TypeError, ValueError, KeyError):
                recipients.append("<unverifiable>")
    return recipients


def sheet_counts(sheet: GoogleSheet, ids: list[str]) -> dict[str, int]:
    rows = sheet.rows()
    counts = Counter(row[5] for row in rows[1:] if len(row) >= 6)
    return {id: counts[id] for id in ids}


def wait_for_completion(store: Store, processor: Processor, ids: list[str], timeout: int):
    deadline = time.monotonic() + timeout
    while True:
        progressed = processor.process_one()
        rows = [store.get(id) for id in ids]
        if all(row and row["status"] == "completed" for row in rows):
            return rows
        if any(row and row["status"] == "needs_attention" for row in rows):
            return rows
        if time.monotonic() >= deadline:
            return rows
        if not progressed:
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Nivoa's complete live workflow and idempotent replay")
    parser.add_argument("--confirm-live", action="store_true",
                        help="required acknowledgement of real API calls, Sheet writes and email sends")
    parser.add_argument("--run-id", help="reuse this value to intentionally replay the same run")
    parser.add_argument("--url", default=os.getenv("DEMO_URL", "http://127.0.0.1:8000/webhook"))
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--output", default="evidence/e2e-live-test.md")
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required; this command uses real external integrations")

    settings = Settings.from_env()
    missing = live_preflight(settings)
    if missing:
        parser.error("Live configuration is incomplete: " + "; ".join(missing))
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = Path(args.output)
    payloads = json.loads((Path(__file__).parent.parent / "examples" / "test_messages.json").read_text(encoding="utf-8"))
    store = Store(settings)
    unrelated_active = [row for row in store.list_messages()
                        if row["status"] in {"received", "processing", "retry"}
                        and not str(row.get("idempotency_key") or "").startswith(f"nivoa-e2e-{run_id}-")]
    if unrelated_active:
        parser.error("DATABASE_PATH contains unrelated active messages; use an isolated E2E database")
    ids = []
    observations = []
    table = ["| # | Fictional sender | Expected | Analysis | DB status | sheet_done / rows | email_done / SMTP accepted |",
             "|---:|---|---|---|---|---|---|"]

    try:
        with httpx.Client(timeout=20) as client:
            created_flags = []
            for number, payload in enumerate(payloads, 1):
                response = client.post(args.url, json=payload,
                    headers={"X-Webhook-Token": settings.webhook_token,
                             "Idempotency-Key": f"nivoa-e2e-{run_id}-{number}"})
                response.raise_for_status()
                accepted = response.json()
                ids.append(accepted["id"])
                created_flags.append(bool(accepted["created"]))
                stored = store.get(accepted["id"])
                if not stored or stored["message"] != payload["message"]:
                    raise RuntimeError("Webhook acknowledgement was not visible in the configured SQLite database")

            processor = Processor(store, settings)
            rows = wait_for_completion(store, processor, ids, args.timeout_seconds)
            sheet = GoogleSheet(settings)
            first_sheet_counts = sheet_counts(sheet, ids)
            first_smtp_count = event_count(store, ids, "email_smtp_accepted")
            recipients = accepted_recipients(store, ids)

            failures = []
            for number, (payload, expected, row) in enumerate(zip(payloads, EXPECTED, rows), 1):
                observed = row["category"] if row else None
                sheet_count = first_sheet_counts[ids[number - 1]]
                smtp_count = event_count(store, [ids[number - 1]], "email_smtp_accepted")
                if not row or row["status"] != "completed":
                    failures.append(f"message {number} did not complete")
                if observed != expected:
                    failures.append(f"message {number} category mismatch")
                if sheet_count != 1:
                    failures.append(f"message {number} has {sheet_count} Sheet rows")
                if smtp_count != int(expected in NEEDS_EMAIL):
                    failures.append(f"message {number} SMTP acceptance count is {smtp_count}")
                table.append(f"| {number} | {payload['name']} | {expected} | {observed or '-'} | "
                             f"{row['status'] if row else 'missing'} | "
                             f"{row['sheet_done'] if row else '-'} / {sheet_count} | "
                             f"{row['email_done'] if row else '-'} / {smtp_count} |")

            if first_smtp_count != 6:
                failures.append(f"expected 6 SMTP acceptances, observed {first_smtp_count}")
            if Counter(row["category"] for row in rows if row)["tarjouspyyntö"] != 3:
                failures.append("quotation-request count was not 3")
            if Counter(row["category"] for row in rows if row)["ihmiselle"] != 3:
                failures.append("human-review count was not 3")
            if any(recipient != settings.owner_email for recipient in recipients):
                failures.append("an SMTP acceptance recipient was not OWNER_EMAIL")

            # Replay the exact same HTTP requests and keys. Nothing should be reprocessed.
            before_replay_smtp = first_smtp_count
            before_replay_sheet = dict(first_sheet_counts)
            replay_created = []
            for number, payload in enumerate(payloads, 1):
                response = client.post(args.url, json=payload,
                    headers={"X-Webhook-Token": settings.webhook_token,
                             "Idempotency-Key": f"nivoa-e2e-{run_id}-{number}"})
                response.raise_for_status()
                replay = response.json()
                replay_created.append(bool(replay["created"]))
                if replay["id"] != ids[number - 1]:
                    failures.append(f"message {number} replay returned a different ID")
            while processor.process_one():
                pass
            after_replay_sheet = sheet_counts(sheet, ids)
            after_replay_smtp = event_count(store, ids, "email_smtp_accepted")
            if any(replay_created):
                failures.append("replay falsely created new webhook messages")
            if after_replay_sheet != before_replay_sheet:
                failures.append("replay changed Sheets row counts")
            if after_replay_smtp != before_replay_smtp:
                failures.append("replay created additional SMTP acceptances")

            observations = [
                f"- Webhook persistence: **10/10 verified** (`created=true`: {sum(created_flags)}/10).",
                f"- Completed SQLite messages: **{sum(row and row['status'] == 'completed' for row in rows)}/10**.",
                f"- Expected categories matched: **{10 - sum('category mismatch' in item for item in failures)}/10**.",
                f"- Unique Google Sheets rows: **{sum(count == 1 for count in first_sheet_counts.values())}/10**.",
                f"- SMTP acceptances: **{first_smtp_count}/6**; quotation requests: 3 expected; human review: 3 expected.",
                f"- Recipient restriction: **{'passed' if recipients and all(r == settings.owner_email for r in recipients) else 'failed'}**.",
                f"- Idempotent replay: **{'passed' if not any(replay_created) and after_replay_sheet == before_replay_sheet and after_replay_smtp == before_replay_smtp else 'failed'}**.",
            ]
            status = "EXECUTED — PASSED" if not failures else "EXECUTED — FAILED"
            if failures:
                observations.extend(["", "Failures:", *[f"- {sanitize_text(item)}" for item in failures]])
            write_report(output, status, run_id, started, observations, table)
            print(f"E2E live test {status.lower()}; report written to {output}")
            return 0 if not failures else 1
    except Exception as exc:
        observations = ["The live run did not complete and remains unverified.", "",
                        f"- Failure type: `{type(exc).__name__}`",
                        f"- Safe message: {sanitize_text(exc)}",
                        "- Inspect SQLite events; accepted webhook messages remain durable."]
        write_report(output, "UNVERIFIED", run_id, started, observations, table)
        print(f"E2E live test unverified ({type(exc).__name__}); see {output}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
