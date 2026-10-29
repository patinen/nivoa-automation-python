import json
from dataclasses import replace
from pathlib import Path
import pytest

from app.config import PROJECT_ROOT, Settings
from app.logic import Processor
from app.models import LeadInput
from app.services import FixtureAnalyzer, MockEmail, MockSheet


class GeminiUnavailable(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Gemini transient HTTP {code}")


class TransientAnalyzer:
    def __init__(self, error):
        self.error = error

    def analyze(self, lead):
        raise self.error


class PermanentConfigurationFailure:
    def analyze(self, lead):
        raise FileNotFoundError("OAuth token configuration is missing")


def test_explicit_environment_auth_mode_takes_precedence(monkeypatch):
    monkeypatch.setenv("GOOGLE_AUTH_MODE", "service_account")
    monkeypatch.setenv("DATABASE_PATH", "data/config-selection.sqlite3")
    settings = Settings.from_env()
    assert settings.google_auth_mode == "service_account"
    assert Path(settings.database_path) == PROJECT_ROOT / "data" / "config-selection.sqlite3"


def test_oauth_is_safe_default_when_not_explicitly_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_AUTH_MODE", raising=False)
    # from_env may still see the project .env; the dataclass default itself must remain OAuth.
    assert Settings().google_auth_mode == "oauth"


@pytest.mark.parametrize("error", [GeminiUnavailable(429), GeminiUnavailable(503),
                                    GeminiUnavailable(504), TimeoutError("Gemini timeout")])
def test_transient_gemini_has_persistent_jittered_retry_without_email_noise(settings, store, error):
    ids = []
    for number in range(2):
        lead = LeadInput(name=f"Testi {number}", email=f"testi{number}@esimerkki.fi",
                         message="Tarvitsisimme tarjouksen.")
        id, _ = store.accept(lead, f"transient-{number}")
        ids.append(id)
    notifier = MockEmail(settings)
    processor = Processor(store, settings, TransientAnalyzer(error), MockSheet(settings), notifier)
    processor.process_one()
    processor.process_one()

    for id in ids:
        row = store.get(id)
        assert row["status"] == "retry"
        assert row["message"] == "Tarvitsisimme tarjouksen."
        assert row["next_attempt_at"] > row["received_at"]
        events = store.list_events(id)
        scheduled = [event for event in events if event["event"] == "retry_scheduled"]
        assert len(scheduled) == 1
        assert "delay_seconds=" in scheduled[0]["detail"]
        assert not any(event["event"] == "failure_alert_sent" for event in events)
    assert not (Path(settings.mock_dir) / "outbox.jsonl").exists()


def test_permanent_shared_failure_sends_one_grouped_terminal_alert(settings, store):
    ids = []
    for number in range(2):
        lead = LeadInput(name=f"Testi {number}", email=f"pysyva{number}@esimerkki.fi",
                         message="Tarvitsisimme tarjouksen.")
        id, _ = store.accept(lead, f"permanent-{number}")
        ids.append(id)
    notifier = MockEmail(settings)
    processor = Processor(store, settings, PermanentConfigurationFailure(),
                          MockSheet(settings), notifier)
    processor.process_one()
    processor.process_one()

    rows = [store.get(id) for id in ids]
    assert all(row["status"] == "needs_attention" for row in rows)
    assert all(row["error_alert_done"] == 1 for row in rows)
    events = {id: [event["event"] for event in store.list_events(id)] for id in ids}
    assert sum("failure_alert_sent" in kinds for kinds in events.values()) == 1
    assert sum("failure_alert_suppressed_duplicate" in kinds for kinds in events.values()) == 1
    outbox = [json.loads(line) for line in
              (Path(settings.mock_dir) / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(outbox) == 1


def test_repaired_permanent_failure_resumes_from_unfinished_stage(settings, store):
    lead = LeadInput(name="Matti Esimerkki", email="matti@esimerkki.fi",
                     message="Tarvitsisimme tarjouksen kirjanpidosta.")
    id, _ = store.accept(lead, "repair-config")
    broken = Processor(store, settings, PermanentConfigurationFailure(),
                       MockSheet(settings), MockEmail(settings))
    broken.process_one()
    assert store.get(id)["status"] == "needs_attention"
    assert store.get(id)["category"] is None

    store.reset(id)
    repaired = Processor(store, settings, FixtureAnalyzer(),
                         MockSheet(settings), MockEmail(settings))
    repaired.process_one()
    row = store.get(id)
    assert row["status"] == "completed"
    assert row["category"] == "tarjouspyyntö"
    assert row["sheet_done"] == 1 and row["email_done"] == 1
