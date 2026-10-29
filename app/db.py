"""SQLite durable inbox and event history, with per-stage checkpoints."""
import sqlite3
import random
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.config import Settings
from app.models import LeadInput


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_paths()
        self.init_db()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.settings.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init_db(self):
        with self.connection() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                received_at TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                message TEXT NOT NULL,
                category TEXT,
                summary TEXT,
                status TEXT NOT NULL DEFAULT 'received',
                sheet_done INTEGER NOT NULL DEFAULT 0,
                email_done INTEGER NOT NULL DEFAULT 0,
                error_alert_done INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                lease_until TEXT,
                last_error TEXT
              );
              CREATE INDEX IF NOT EXISTS idx_messages_ready ON messages(status,next_attempt_at);
              CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL REFERENCES messages(id),
                created_at TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT
              );
              CREATE TABLE IF NOT EXISTS incident_alerts (
                fingerprint TEXT PRIMARY KEY,
                first_message_id TEXT NOT NULL,
                created_at TEXT NOT NULL
              );
            """)

    def add_event(self, db, id: str, kind: str, detail: str = ""):
        db.execute("INSERT INTO events(message_id,created_at,event,detail) VALUES (?,?,?,?)",
                   (id, utcnow(), kind, detail[:1000]))

    def accept(self, lead: LeadInput, idem_key: str | None) -> tuple[str, bool]:
        created = utcnow()
        id = str(uuid4())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if idem_key:
                old = db.execute("SELECT id, name, email, phone, message FROM messages WHERE idempotency_key=?", (idem_key,)).fetchone()
                if old:
                    # The same key cannot silently overwrite a different request.
                    if (old["name"], old["email"], old["phone"], old["message"]) != (lead.name, str(lead.email), lead.phone, lead.message):
                        raise ValueError("Idempotency key reused with different payload")
                    return old["id"], False
            db.execute("""INSERT INTO messages
                (id,idempotency_key,received_at,name,email,phone,message,next_attempt_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (id, idem_key, created, lead.name, str(lead.email), lead.phone, lead.message, created))
            self.add_event(db, id, "accepted")
        return id, True

    def get(self, id: str) -> dict | None:
        with self.connection() as db:
            r = db.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
            return dict(r) if r else None

    def list_messages(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY received_at,id")]

    def list_events(self, id: str) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE message_id=? ORDER BY event_id", (id,))]

    def record_event(self, id: str, kind: str, detail: str = ""):
        with self.connection() as db:
            self.add_event(db, id, kind, detail)

    def incident_alert_exists(self, fingerprint: str, cooldown_seconds: int = 86400) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT created_at FROM incident_alerts WHERE fingerprint=?",
                             (fingerprint,)).fetchone()
            if not row:
                return False
            created = datetime.fromisoformat(row["created_at"])
            return created >= datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)

    def record_incident_alert(self, fingerprint: str, id: str):
        with self.connection() as db:
            db.execute("""INSERT INTO incident_alerts(fingerprint,first_message_id,created_at)
                          VALUES (?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
                          first_message_id=excluded.first_message_id, created_at=excluded.created_at""",
                       (fingerprint, id, utcnow()))

    def mark_error_alert_handled(self, id: str, event: str):
        with self.connection() as db:
            db.execute("UPDATE messages SET error_alert_done=1 WHERE id=?", (id,))
            self.add_event(db, id, event)

    def claim(self) -> dict | None:
        current = utcnow()
        lease = (datetime.now(timezone.utc) + timedelta(seconds=self.settings.lease_seconds)).isoformat(timespec="seconds")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM messages WHERE
                (status IN ('received', 'retry') AND next_attempt_at <= ?)
                OR (status='processing' AND lease_until <= ?)
                ORDER BY received_at,id LIMIT 1""", (current, current)).fetchone()
            if not row:
                return None
            db.execute("UPDATE messages SET status='processing', attempts=attempts+1, lease_until=? WHERE id=?",
                       (lease, row["id"]))
            self.add_event(db, row["id"], "claimed", f"attempt {row['attempts'] + 1}")
            return dict(db.execute("SELECT * FROM messages WHERE id=?", (row["id"],)).fetchone())

    def analyzed(self, id: str, category: str, summary: str):
        with self.connection() as db:
            db.execute("UPDATE messages SET category=?, summary=? WHERE id=?", (category, summary, id))
            self.add_event(db, id, "analyzed", category)

    def stage_done(self, id: str, stage: str):
        if stage not in {"sheet_done", "email_done", "error_alert_done"}:
            raise ValueError("Invalid stage")
        with self.connection() as db:
            db.execute(f"UPDATE messages SET {stage}=1 WHERE id=?", (id,))
            self.add_event(db, id, stage)

    def complete(self, id: str):
        with self.connection() as db:
            db.execute("UPDATE messages SET status='completed', lease_until=NULL, last_error=NULL WHERE id=?", (id,))
            self.add_event(db, id, "completed")

    def fail(self, id: str, attempts: int, step: str, reason: str,
             retryable: bool = True) -> tuple[str, str, float]:
        limit = self.settings.retry_limit
        status = "needs_attention" if not retryable or attempts >= limit else "retry"
        base_delay = min(1800, 2 ** min(attempts, 10))
        delay = round(min(1800, base_delay * random.uniform(0.75, 1.25)), 3) if status == "retry" else 0
        next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        with self.connection() as db:
            db.execute("""UPDATE messages SET status=?, next_attempt_at=?, lease_until=NULL,
                           last_error=?, error_alert_done=0 WHERE id=?""",
                       (status, next_attempt, f"{step}: {reason}"[:1000], id))
            self.add_event(db, id, "failed", f"{step}: {reason}")
            if status == "retry":
                self.add_event(db, id, "retry_scheduled",
                               f"next_attempt_at={next_attempt}; delay_seconds={delay}; attempt={attempts}/{limit}")
            else:
                self.add_event(db, id, "needs_attention",
                               f"retryable={retryable}; attempts={attempts}/{limit}; step={step}")
        return status, next_attempt, delay

    def reset(self, id: str) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM messages WHERE id=?", (id,)).fetchone()
            if not row:
                return False
            db.execute("""UPDATE messages SET status='retry', attempts=0, next_attempt_at=?,
                           lease_until=NULL, error_alert_done=0 WHERE id=?""",
                       (utcnow(), id))
            self.add_event(db, id, "manually_requeued")
            return True
