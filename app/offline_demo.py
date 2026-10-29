"""Self-validating, credential-free HTTP-to-worker demonstration."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings
from app.logic import NEEDS_EMAIL, Processor
from app.services import FIXTURE_RESULTS


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def run_offline_demo(work_dir: Path) -> dict:
    """Run and assert one isolated demo, returning its observed measurements."""
    work_dir.mkdir(parents=True, exist_ok=True)
    database = work_dir / "offline-demo.sqlite3"
    mock_dir = work_dir / "mock"
    if database.exists() or mock_dir.exists():
        raise RuntimeError("Offline demo work directory must be empty")

    settings = Settings(
        database_path=str(database),
        mock_dir=str(mock_dir),
        webhook_token="offline-demo-token",
        analyzer_mode="fixture",
        sheet_mode="mock",
        email_mode="mock",
        owner_email="owner@example.test",
    )

    # app.api exposes a conventional module-level ASGI app. On first import, make
    # that harmless app use the same isolated offline paths rather than a user's .env.
    overrides = {
        "DATABASE_PATH": str(database),
        "MOCK_DIR": str(mock_dir),
        "WEBHOOK_TOKEN": settings.webhook_token,
        "ANALYZER_MODE": "fixture",
        "SHEET_MODE": "mock",
        "EMAIL_MODE": "mock",
        "OWNER_EMAIL": settings.owner_email,
        "APP_ENV": "development",
    }
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        from app.api import create_app
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    app = create_app(settings)
    store = app.state.store
    payloads = json.loads(
        (PROJECT_ROOT / "examples" / "test_messages.json").read_text(encoding="utf-8")
    )
    ids: list[str] = []

    with TestClient(app) as client:
        for number, payload in enumerate(payloads, 1):
            response = client.post(
                "/webhook",
                json=payload,
                headers={
                    "X-Webhook-Token": settings.webhook_token,
                    "Idempotency-Key": f"nivoa-offline-demo-v1-{number}",
                },
            )
            if response.status_code != 202:
                raise AssertionError(f"Webhook message {number} returned HTTP {response.status_code}")
            accepted = response.json()
            if not accepted["created"]:
                raise AssertionError(f"Initial webhook message {number} was not newly created")
            ids.append(accepted["id"])
            if not store.get(accepted["id"]):
                raise AssertionError(f"Webhook message {number} was not persisted")

        processor = Processor(store, settings)
        processed = 0
        while processor.process_one():
            processed += 1
            if processed > 10:
                raise AssertionError("Worker processed more than the ten submitted messages")

        rows = [store.get(message_id) for message_id in ids]
        expected = [FIXTURE_RESULTS[payload["email"]][0] for payload in payloads]
        observed = [row["category"] if row else None for row in rows]
        if processed != 10 or any(not row or row["status"] != "completed" for row in rows):
            raise AssertionError("Exactly ten completed SQLite messages were not observed")
        if observed != expected:
            raise AssertionError(f"Fixture categories differ: observed={observed!r}")

        sheet_path = mock_dir / "leads.csv"
        with sheet_path.open(encoding="utf-8", newline="") as source:
            sheet_rows = list(csv.reader(source))
        outbox_path = mock_dir / "outbox.jsonl"
        notifications = [
            json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines()
        ]
        if len(sheet_rows) != 11 or len({row[5] for row in sheet_rows[1:]}) != 10:
            raise AssertionError("Mock Sheets did not contain exactly ten unique message rows")
        if len(notifications) != 6:
            raise AssertionError("Mock outbox did not contain exactly six notifications")
        if any(item["recipient"] != settings.owner_email for item in notifications):
            raise AssertionError("A mock notification was addressed outside OWNER_EMAIL")
        if sum(category in NEEDS_EMAIL for category in observed) != 6:
            raise AssertionError("Fixture notification rule did not select exactly six messages")

        before = (len(store.list_messages()), len(sheet_rows), _line_count(outbox_path))
        replay_created: list[bool] = []
        for number, payload in enumerate(payloads, 1):
            response = client.post(
                "/webhook",
                json=payload,
                headers={
                    "X-Webhook-Token": settings.webhook_token,
                    "Idempotency-Key": f"nivoa-offline-demo-v1-{number}",
                },
            )
            if response.status_code != 202 or response.json()["id"] != ids[number - 1]:
                raise AssertionError(f"Replay message {number} did not return its original ID")
            replay_created.append(response.json()["created"])

        replay_processed = processor.process_one()
        with sheet_path.open(encoding="utf-8", newline="") as source:
            replay_sheet_rows = list(csv.reader(source))
        after = (len(store.list_messages()), len(replay_sheet_rows), _line_count(outbox_path))
        if any(replay_created) or replay_processed or after != before:
            raise AssertionError("Idempotent replay changed SQLite or mock side effects")

    return {
        "database": str(database),
        "completed": 10,
        "categories": observed,
        "sheet_rows": 10,
        "notifications": 6,
        "replay_created": sum(replay_created),
        "replay_processed": bool(replay_processed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="empty directory to retain demo artifacts; default uses a temporary directory",
    )
    args = parser.parse_args()
    try:
        if args.work_dir:
            result = run_offline_demo(args.work_dir.resolve())
            retained = True
        else:
            with TemporaryDirectory(prefix="nivoa-offline-demo-") as temporary:
                result = run_offline_demo(Path(temporary))
                retained = False
        print("Nivoa offline end-to-end demo: PASSED")
        print("HTTP webhook persisted: 10/10")
        print("Worker completed: 10/10")
        print("Fixture categories matched: 10/10")
        print("Mock Sheets rows: 10")
        print("Mock owner notifications: 6")
        print("Idempotent replay: 0 created, 0 processed, 0 additional side effects")
        print(f"Artifacts: {result['database'] if retained else 'temporary (removed after verification)'}")
        print("This verifies the offline pipeline, not Gemini, Google Sheets, SMTP, or inbox delivery.")
        return 0
    except Exception as exc:
        print(f"Nivoa offline end-to-end demo: FAILED ({type(exc).__name__}: {exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
