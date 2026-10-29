"""Operator-readable status report for demo evidence (no admin HTTP endpoint)."""
import argparse
from collections import Counter
from app.config import Settings
from app.db import Store


def main():
    parser = argparse.ArgumentParser(description="Inspect local durable inbox")
    parser.add_argument("--id", help="Print event history for message ID")
    parser.add_argument("--recoverable", action="store_true",
                        help="List retry/needs_attention messages with IDs and checkpoints")
    args = parser.parse_args()
    settings = Settings.from_env()
    print(f"Database: {settings.database_path}")
    store = Store(settings)
    if args.recoverable:
        rows = [row for row in store.list_messages() if row["status"] in {"retry", "needs_attention"}]
        print(f"Recoverable messages: {len(rows)}")
        for row in rows:
            print(f"{row['id']}  status={row['status']} attempts={row['attempts']} "
                  f"category={row['category'] or '-'} sheet_done={row['sheet_done']} "
                  f"email_done={row['email_done']} next={row['next_attempt_at']} "
                  f"error={row['last_error']}")
        return
    if args.id:
        row = store.get(args.id)
        if not row:
            parser.error("Unknown ID")
        print("Message:")
        for k in ["id", "received_at", "name", "email", "category", "summary", "status",
                  "attempts", "sheet_done", "email_done", "last_error"]:
            print(f"  {k}: {row[k]}")
        print("Events:")
        for e in store.list_events(args.id):
            print(f"  {e['created_at']}  {e['event']}  {e['detail'] or ''}")
        return
    rows = store.list_messages()
    print(f"Messages: {len(rows)} | Status: {dict(Counter(r['status'] for r in rows))}")
    print("Name                    Category            Status             Sheet  Email")
    print("-" * 79)
    for r in rows:
        print(f"{r['name'][:23]:<23} {str(r['category'] or '-'):19} {r['status']:18} "
              f"{r['sheet_done']!s:5}  {r['email_done']}")


if __name__ == "__main__":
    main()
