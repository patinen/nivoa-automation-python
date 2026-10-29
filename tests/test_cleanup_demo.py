import json
from pathlib import Path
import sqlite3

import pytest

from app.cleanup_demo import CleanupRefused, apply_plan, build_plan
from app.config import PROJECT_ROOT, Settings
from app.db import Store


FIXTURES = json.loads((PROJECT_ROOT / "examples" / "test_messages.json").read_text(encoding="utf-8"))


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "cleanup.sqlite3"
    Store(Settings(database_path=str(path), mock_dir=str(tmp_path / "mock")))
    return path


def _insert_run(path: Path, run: str, received: str, complete: bool = True) -> list[str]:
    ids = []
    connection = sqlite3.connect(path)
    try:
        for index, item in enumerate(FIXTURES, 1):
            message_id = f"{run}-message-{index}"
            category = "tarjouspyyntö" if index <= 3 else "ihmiselle" if index <= 6 else "muu"
            status = "completed" if complete else "needs_attention"
            sheet_done = 1 if complete else 0
            email_done = 1 if complete and category in {"tarjouspyyntö", "ihmiselle"} else 0
            connection.execute(
                """INSERT INTO messages
                   (id,idempotency_key,received_at,name,email,phone,message,category,summary,
                    status,sheet_done,email_done,next_attempt_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, f"nivoa-demo-run-{run}-{index}", received, item["name"],
                 item["email"], item["phone"], item["message"], category, "Lyhyt yhteenveto.",
                 status, sheet_done, email_done, received),
            )
            connection.execute(
                "INSERT INTO events(message_id,created_at,event,detail) VALUES (?,?,?,?)",
                (message_id, received, "completed" if complete else "failed", "test"),
            )
            ids.append(message_id)
        connection.commit()
    finally:
        connection.close()
    return ids


def test_plan_prefers_latest_complete_encoded_run(tmp_path):
    path = _database(tmp_path)
    old_ids = _insert_run(path, "old", "2026-01-01T00:00:00+00:00")
    new_ids = _insert_run(path, "new", "2026-02-01T00:00:00+00:00")

    plan = build_plan(path)

    assert plan.selection == "single complete encoded run"
    assert plan.run_identifier == "nivoa-demo-run-new"
    assert {row["id"] for row in plan.keep} == set(new_ids)
    assert {row["id"] for row in plan.remove} == set(old_ids)
    assert plan.dependent_events == 10


def test_refuses_fewer_than_ten_successful_contacts(tmp_path):
    path = _database(tmp_path)
    _insert_run(path, "incomplete", "2026-01-01T00:00:00+00:00", complete=False)

    with pytest.raises(CleanupRefused, match="Fewer than 10"):
        build_plan(path)


def test_apply_backs_up_then_removes_only_redundant_demo_records(tmp_path):
    path = _database(tmp_path)
    old_ids = _insert_run(path, "old", "2026-01-01T00:00:00+00:00")
    new_ids = _insert_run(path, "new", "2026-02-01T00:00:00+00:00")
    connection = sqlite3.connect(path)
    connection.execute(
        """INSERT INTO messages
           (id,idempotency_key,received_at,name,email,message,status,next_attempt_at)
           VALUES ('unrelated','unrelated-key','2026-03-01T00:00:00+00:00',
                   'Real Customer','real@example.test','Hello','received','2026-03-01T00:00:00+00:00')"""
    )
    connection.execute(
        "INSERT INTO incident_alerts VALUES ('old-incident', ?, '2026-01-01T00:00:00+00:00')",
        (old_ids[0],),
    )
    connection.commit()
    connection.close()

    plan = build_plan(path)
    backup = apply_plan(plan)

    assert backup.is_file()
    with sqlite3.connect(path) as connection:
        ids = {row[0] for row in connection.execute("SELECT id FROM messages")}
        assert ids == set(new_ids) | {"unrelated"}
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM incident_alerts").fetchone()[0] == 0
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 21
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_build_plan_is_read_only(tmp_path):
    path = _database(tmp_path)
    _insert_run(path, "one", "2026-01-01T00:00:00+00:00")
    before = path.read_bytes()

    plan = build_plan(path)

    assert len(plan.keep) == 10
    assert not plan.remove
    assert path.read_bytes() == before
