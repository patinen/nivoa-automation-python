"""Safely remove redundant copies of the ten fictional demo messages."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from app.config import PROJECT_ROOT, Settings, setting_source


FIXTURE_PATH = PROJECT_ROOT / "examples" / "test_messages.json"
NOTIFICATION_CATEGORIES = {"tarjouspyyntö", "ihmiselle"}
REQUIRED_MESSAGE_COLUMNS = {
    "id", "idempotency_key", "received_at", "name", "email", "phone",
    "message", "category", "summary", "status", "sheet_done", "email_done",
}


class CleanupRefused(RuntimeError):
    """Raised when cleanup cannot be proven safe."""


@dataclass(frozen=True)
class CleanupPlan:
    database_path: Path
    selection: str
    run_identifier: str | None
    keep: tuple[sqlite3.Row, ...]
    remove: tuple[sqlite3.Row, ...]
    dependent_events: int
    dependent_incidents: int


def _identity(row: sqlite3.Row | dict) -> tuple[str, str, str, str]:
    return (
        row["name"], row["email"], row["phone"] or "", row["message"],
    )


def _fixtures() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if len(data) != 10 or len({_identity(item) for item in data}) != 10:
        raise CleanupRefused("The assignment fixture must contain exactly 10 distinct contacts.")
    return data


def _is_successful(row: sqlite3.Row) -> bool:
    return (
        row["status"] == "completed"
        and row["sheet_done"] == 1
        and bool(row["category"])
        and bool(row["summary"])
        and (row["category"] not in NOTIFICATION_CATEGORIES or row["email_done"] == 1)
    )


def _run_identifier(key: str) -> str | None:
    """Return only run identifiers encoded by known demo/e2e key formats."""
    if re.fullmatch(r"nivoa-demo-v1-\d+-[0-9a-f]{16}", key):
        return "nivoa-demo-v1-stable"
    match = re.fullmatch(r"(nivoa-(?:example|demo-run|e2e)-.+)-(\d+)", key)
    return match.group(1) if match else None


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not {"messages", "events", "incident_alerts"}.issubset(tables):
        raise CleanupRefused("Database does not have the expected Nivoa tables.")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
    if not REQUIRED_MESSAGE_COLUMNS.issubset(columns):
        raise CleanupRefused("Database messages schema is not recognized.")


def build_plan(database_path: str | Path) -> CleanupPlan:
    path = Path(database_path).resolve()
    if not path.is_file():
        raise CleanupRefused(f"Active database does not exist: {path}")

    fixtures = _fixtures()
    expected = {_identity(item) for item in fixtures}
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        _validate_schema(connection)
        rows = tuple(connection.execute("SELECT * FROM messages ORDER BY received_at, id"))
        matching = tuple(row for row in rows if _identity(row) in expected)
        successful = tuple(row for row in matching if _is_successful(row))
        successful_identities = {_identity(row) for row in successful}
        missing = expected - successful_identities
        if missing:
            names = ", ".join(sorted(identity[0] for identity in missing))
            raise CleanupRefused(
                "Fewer than 10 distinct completed test contacts are available; "
                f"missing successful records for: {names}."
            )
        if any(row["status"] in {"received", "retry", "processing"} for row in matching):
            raise CleanupRefused(
                "A matching demo message is still active (received/retry/processing); stop the worker "
                "and resolve it before cleanup."
            )

        complete_runs: list[tuple[str, tuple[sqlite3.Row, ...]]] = []
        run_ids = {_run_identifier(row["idempotency_key"]) for row in successful}
        for run_id in sorted(item for item in run_ids if item):
            group = tuple(
                row for row in successful if _run_identifier(row["idempotency_key"]) == run_id
            )
            if len(group) == 10 and {_identity(row) for row in group} == expected:
                complete_runs.append((run_id, group))

        if complete_runs:
            run_id, keep = max(
                complete_runs,
                key=lambda item: max((row["received_at"], row["id"]) for row in item[1]),
            )
            selection = "single complete encoded run"
        else:
            run_id = None
            keep = tuple(
                max(
                    (row for row in successful if _identity(row) == identity),
                    key=lambda row: (row["received_at"], row["id"]),
                )
                for identity in sorted(expected)
            )
            selection = "newest successful record per contact (not asserted to be one run)"

        keep_ids = {row["id"] for row in keep}
        remove = tuple(row for row in matching if row["id"] not in keep_ids)
        remove_ids = [row["id"] for row in remove]
        events = incidents = 0
        if remove_ids:
            marks = _placeholders(len(remove_ids))
            events = connection.execute(
                f"SELECT COUNT(*) FROM events WHERE message_id IN ({marks})", remove_ids
            ).fetchone()[0]
            incidents = connection.execute(
                f"SELECT COUNT(*) FROM incident_alerts WHERE first_message_id IN ({marks})",
                remove_ids,
            ).fetchone()[0]
        return CleanupPlan(path, selection, run_id, keep, remove, events, incidents)
    finally:
        connection.close()


def _backup_path(database_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return database_path.parent / "backups" / f"{database_path.stem}-before-demo-cleanup-{stamp}.sqlite3"


def apply_plan(plan: CleanupPlan) -> Path:
    """Back up and atomically apply an unchanged cleanup plan."""
    current = build_plan(plan.database_path)
    if ({row["id"] for row in current.keep}, {row["id"] for row in current.remove}) != (
        {row["id"] for row in plan.keep}, {row["id"] for row in plan.remove}
    ):
        raise CleanupRefused("Database changed after preview; run --dry-run again.")

    backup = _backup_path(plan.database_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(plan.database_path)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    remove_ids = [row["id"] for row in plan.remove]
    connection = sqlite3.connect(plan.database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        if remove_ids:
            marks = _placeholders(len(remove_ids))
            connection.execute(
                f"DELETE FROM events WHERE message_id IN ({marks})", remove_ids
            )
            connection.execute(
                f"DELETE FROM incident_alerts WHERE first_message_id IN ({marks})", remove_ids
            )
            connection.execute(f"DELETE FROM messages WHERE id IN ({marks})", remove_ids)

        expected = {_identity(item) for item in _fixtures()}
        remaining = tuple(
            row for row in connection.execute("SELECT * FROM messages")
            if _identity(row) in expected
        )
        if len(remaining) != 10 or len({row["id"] for row in remaining}) != 10:
            raise CleanupRefused("Post-cleanup validation did not find exactly 10 unique messages.")
        if not all(_is_successful(row) for row in remaining):
            raise CleanupRefused("Post-cleanup validation found an incomplete checkpoint.")
        if {row["id"] for row in remaining} != {row["id"] for row in plan.keep}:
            raise CleanupRefused("Post-cleanup retained IDs differ from the preview.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return backup


def _print_rows(label: str, rows: Iterable[sqlite3.Row]) -> None:
    for row in rows:
        print(
            f"{label} {row['name']} | id={row['id']} | received={row['received_at']} | "
            f"status={row['status']} | category={row['category']} | "
            f"sheet={row['sheet_done']} | email={row['email_done']} | "
            f"key={row['idempotency_key']}"
        )


def print_plan(plan: CleanupPlan) -> None:
    print(f"Database: {plan.database_path}")
    print(f"Selection: {plan.selection}")
    print(f"Encoded run identifier: {plan.run_identifier or '(none; per-contact fallback)'}")
    print(f"Retain: {len(plan.keep)} messages")
    print(f"Remove: {len(plan.remove)} messages")
    print(f"Dependent events to remove: {plan.dependent_events}")
    print(f"Dependent incident alerts to remove: {plan.dependent_incidents}")
    print(f"Backup on apply: {plan.database_path.parent / 'backups' / (plan.database_path.stem + '-before-demo-cleanup-<UTC timestamp>.sqlite3')}")
    _print_rows("KEEP", plan.keep)
    _print_rows("REMOVE", plan.remove)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview without changing the database")
    mode.add_argument("--apply", action="store_true", help="Back up and apply the displayed cleanup")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        source = setting_source("DATABASE_PATH")
        if source == "application default":
            raise CleanupRefused(
                "DATABASE_PATH is not explicitly configured; refusing to infer the active database."
            )
        plan = build_plan(settings.database_path)
        print(f"DATABASE_PATH source: {source}")
        print_plan(plan)
        if args.dry_run:
            print("DRY RUN ONLY: no database records or files were changed.")
            return 0
        backup = apply_plan(plan)
        print(f"Cleanup complete. SQLite backup: {backup}")
        return 0
    except CleanupRefused as exc:
        print(f"Cleanup refused: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
