"""Send Nivoa's 10 supplied test payloads to the actual FastAPI handler."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
import httpx
from dotenv import load_dotenv

load_dotenv()


def stable_key(payload: dict, number: int) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"nivoa-demo-v1-{number}-{digest}"


def submit_payloads(client, url: str, token: str, payloads: list[dict], batch_id: str | None = None):
    results = []
    for number, payload in enumerate(payloads, 1):
        key = (f"nivoa-demo-run-{batch_id}-{number}" if batch_id
               else stable_key(payload, number))
        response = client.post(url, json=payload,
                               headers={"X-Webhook-Token": token, "Idempotency-Key": key})
        response.raise_for_status()
        results.append((payload, key, response.json()))
    return results


def print_results(results, new_run: bool, batch_id: str | None):
    for number, (payload, key, result) in enumerate(results, 1):
        state = "CREATED" if result["created"] else "ALREADY ACCEPTED"
        print(f"{number:2}. {payload['name']:<18} -> {state} id={result['id']} key={key}")
    created = sum(result["created"] for _, _, result in results)
    if created == 0:
        print("WARNING: This stable demo batch was already submitted; no new messages were created.")
    elif created < len(results):
        print(f"WARNING: Demo batch was partially present; created {created}/{len(results)} messages.")
    elif new_run:
        print(f"Created intentional fresh demo batch: {batch_id}")
    else:
        print("Created the stable default demo batch. Re-running this command will reuse it safely.")


def main():
    parser = argparse.ArgumentParser(description="Submit Nivoa's 10 fictional demo messages")
    parser.add_argument("--new-run", action="store_true",
                        help="deliberately create a fresh batch with new idempotency keys")
    parser.add_argument("--run-id", help="stable ID for an intentional fresh batch; requires --new-run")
    args = parser.parse_args()
    if args.run_id and not args.new_run:
        parser.error("--run-id requires --new-run")
    url = os.getenv("DEMO_URL", "http://127.0.0.1:8000/webhook")
    token = os.getenv("WEBHOOK_TOKEN", "")
    batch_id = (args.run_id or uuid4().hex) if args.new_run else None
    payloads = json.loads((Path(__file__).parent.parent / "examples" / "test_messages.json").read_text(encoding="utf-8"))
    with httpx.Client(timeout=15) as client:
        results = submit_payloads(client, url, token, payloads, batch_id)
    print_results(results, args.new_run, batch_id)


if __name__ == "__main__":
    main()
