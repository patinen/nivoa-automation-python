"""Read-only audit of duplicate fictional submissions and processing events."""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from app.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit existing Nivoa demo duplicates without modifying data")
    parser.parse_args()
    settings = Settings.from_env()
    database = Path(settings.database_path).resolve()
    if not database.is_file():
        parser.error(f"Database not found: {database}")
    payloads = json.loads((Path(__file__).parent.parent / "examples" / "test_messages.json").read_text(encoding="utf-8"))
    fictional_emails = {payload["email"] for payload in payloads}

    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        messages = [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY received_at,id")]
        events = [dict(row) for row in db.execute(
            "SELECT message_id,event FROM events ORDER BY event_id")]

    events_by_id = defaultdict(Counter)
    for event in events:
        events_by_id[event["message_id"]][event["event"]] += 1
    statuses = Counter(row["status"] for row in messages)
    fictional = [row for row in messages if row["email"] in fictional_emails]
    contacts = Counter(row["email"] for row in fictional)
    repeated = {email: count for email, count in contacts.items() if count > 1}
    exact_groups = defaultdict(list)
    for row in fictional:
        exact_groups[(row["name"], row["email"], row["phone"], row["message"])].append(row)
    exact_duplicates = [rows for rows in exact_groups.values() if len(rows) > 1]

    print("Nivoa duplicate audit (read-only)")
    print(f"Database: {database}")
    print(f"Total stored messages: {len(messages)}")
    print(f"Unique message IDs: {len({row['id'] for row in messages})}")
    print(f"Status counts: {dict(statuses)}")
    print(f"Fictional assignment records: {len(fictional)}")
    print(f"Repeated fictional contacts: {len(repeated)}")
    print(f"Exact fictional payload groups with multiple message IDs: {len(exact_duplicates)}")
    print("")

    for email, count in sorted(repeated.items()):
        print(f"Contact {email}: {count} records")
        for row in [item for item in fictional if item["email"] == email]:
            kinds = events_by_id[row["id"]]
            print(f"  id={row['id']} idem={row['idempotency_key'] or '-'} "
                  f"status={row['status']} attempts={row['attempts']} "
                  f"sheet_done={row['sheet_done']} email_done={row['email_done']} "
                  f"events=accepted:{kinds['accepted']},claimed:{kinds['claimed']},"
                  f"analyzed:{kinds['analyzed']},sheet:{kinds['sheet_done']},"
                  f"email_attempt:{kinds['email_attempt']},smtp_accepted:{kinds['email_smtp_accepted']},"
                  f"completed:{kinds['completed']},failed:{kinds['failed']}")

    same_id_reprocessed = [row for row in fictional if events_by_id[row["id"]]["completed"] > 1]
    same_id_repeated_side_effects = [row for row in fictional
                                     if events_by_id[row["id"]]["sheet_done"] > 1
                                     or events_by_id[row["id"]]["email_smtp_accepted"] > 1]
    print("")
    print(f"Same message ID completed more than once: {len(same_id_reprocessed)}")
    print(f"Same message ID has repeated Sheet/SMTP success events: {len(same_id_repeated_side_effects)}")
    if exact_duplicates:
        print("Conclusion: apparent duplicates use different message IDs/idempotency keys unless listed above.")
    else:
        print("Conclusion: no exact duplicate fictional payloads were found in this database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
