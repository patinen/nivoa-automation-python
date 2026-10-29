"""Start separately from the HTTP process: python -m app.worker [--once]."""
import argparse
import logging
import time
from app.config import Settings
from app.db import Store
from app.logic import Processor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process all currently ready messages and exit")
    parser.add_argument("--retry-id", help="Requeue a stopped message (preserves stage checkpoints)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    store = Store(settings)
    if args.retry_id:
        if not store.reset(args.retry_id):
            parser.error("Message ID not found")
        print("Requeued", args.retry_id)
        return
    processor = Processor(store, settings)
    if args.once:
        count = 0
        while processor.process_one():
            count += 1
        print(f"Processed attempts: {count}")
        return
    print("Worker running. Ctrl+C to stop.", flush=True)
    while True:
        if not processor.process_one():
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
