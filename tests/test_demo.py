import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.demo import print_results, stable_key, submit_payloads
from app.logic import Processor


PAYLOADS = json.loads((Path(__file__).parent.parent / "examples" / "test_messages.json").read_text(encoding="utf-8"))


def test_default_demo_reuses_stable_keys_and_does_not_reprocess(settings, capsys):
    client = TestClient(create_app(settings))
    first = submit_payloads(client, "/webhook", "secret-token", PAYLOADS)
    assert all(result["created"] for _, _, result in first)
    assert [key for _, key, _ in first] == [stable_key(payload, number)
                                            for number, payload in enumerate(PAYLOADS, 1)]

    processor = Processor(client.app.state.store, settings)
    while processor.process_one():
        pass
    ids = [result["id"] for _, _, result in first]
    event_counts = {id: len(client.app.state.store.list_events(id)) for id in ids}
    sheet_before = (Path(settings.mock_dir) / "leads.csv").read_text(encoding="utf-8")
    outbox_before = (Path(settings.mock_dir) / "outbox.jsonl").read_text(encoding="utf-8")

    second = submit_payloads(client, "/webhook", "secret-token", PAYLOADS)
    assert all(not result["created"] for _, _, result in second)
    assert [result["id"] for _, _, result in second] == ids
    assert processor.process_one() is False
    assert {id: len(client.app.state.store.list_events(id)) for id in ids} == event_counts
    assert (Path(settings.mock_dir) / "leads.csv").read_text(encoding="utf-8") == sheet_before
    assert (Path(settings.mock_dir) / "outbox.jsonl").read_text(encoding="utf-8") == outbox_before

    print_results(second, False, None)
    assert "WARNING: This stable demo batch was already submitted" in capsys.readouterr().out


def test_new_demo_run_uses_distinct_explicit_batch_keys(settings):
    client = TestClient(create_app(settings))
    default = submit_payloads(client, "/webhook", "secret-token", PAYLOADS)
    fresh = submit_payloads(client, "/webhook", "secret-token", PAYLOADS, "intentional-run-1")
    assert all(result["created"] for _, _, result in fresh)
    assert {result["id"] for _, _, result in default}.isdisjoint(
        {result["id"] for _, _, result in fresh})
    assert all(key.startswith("nivoa-demo-run-intentional-run-1-") for _, key, _ in fresh)
