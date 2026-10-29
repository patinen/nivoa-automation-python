from app.db import Store
from app.logic import Processor
from app.models import Analysis, LeadInput
from app.services import FixtureAnalyzer, MockEmail, MockSheet


class RecoveringAnalyzer:
    def __init__(self):
        self.calls = 0

    def analyze(self, lead):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("simulated Gemini outage")
        return Analysis(category="tarjouspyyntö", summary="Yritys pyytää tarjousta kirjanpidosta.",
                        ambiguous=False, confidence=0.97)


def test_gemini_failure_retries_successfully_without_losing_message(settings, store):
    lead = LeadInput(name="Matti Esimerkki", email="matti@esimerkki.fi",
                     message="Tarvitsisimme tarjouksen kirjanpidosta.")
    id, _ = store.accept(lead, "gemini-recovers")
    analyzer = RecoveringAnalyzer()
    processor = Processor(store, settings, analyzer, MockSheet(settings), MockEmail(settings))
    processor.process_one()
    failed = store.get(id)
    assert failed["status"] == "retry"
    assert failed["message"] == lead.message
    assert failed["category"] is None and failed["sheet_done"] == 0 and failed["email_done"] == 0
    assert any(event["event"] == "failed" for event in store.list_events(id))

    store.reset(id)
    processor.process_one()
    completed = store.get(id)
    assert completed["status"] == "completed"
    assert completed["category"] == "tarjouspyyntö"
    assert completed["sheet_done"] == 1 and completed["email_done"] == 1
    assert analyzer.calls == 2


def test_new_worker_recovers_message_persisted_before_interruption(settings):
    lead = LeadInput(name="Liisa Testinen", email="liisa@esimerkki.fi",
                     message="Milloin ALV-ilmoitus pitää jättää?")
    first_store = Store(settings)
    id, created = first_store.accept(lead, "persisted-before-worker")
    assert created and first_store.get(id)["status"] == "received"

    # Simulate the API process ending and a separate worker opening the same durable database.
    restarted_store = Store(settings)
    Processor(restarted_store, settings, FixtureAnalyzer(), MockSheet(settings),
              MockEmail(settings)).process_one()
    row = restarted_store.get(id)
    assert row["status"] == "completed"
    assert row["message"] == lead.message
    assert any(event["event"] == "accepted" for event in restarted_store.list_events(id))
