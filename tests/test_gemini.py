import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models import LeadInput, normalize_analysis
from app.services import GeminiAnalyzer


LEAD = LeadInput(name="Testihenkilö", email="testi@esimerkki.fi", message="Haluaisin tarjouksen kirjanpidosta.")


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.closed = False
        self.models = self
        self.call = None

    def generate_content(self, **kwargs):
        self.call = kwargs
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


def analyzer(settings, client):
    configured = replace(settings, gemini_api_key="test-key", analyzer_mode="gemini")
    return GeminiAnalyzer(configured, client_factory=lambda **kwargs: client)


def test_gemini_valid_structured_output(settings):
    client = FakeClient(SimpleNamespace(parsed={"category": "tarjouspyyntö",
        "summary": "Lähettäjä pyytää tarjousta kirjanpidosta.", "ambiguous": False, "confidence": 0.96}, text=None))
    result = analyzer(settings, client).analyze(LEAD)
    assert result.category == "tarjouspyyntö" and result.confidence == 0.96
    assert client.call["model"] == "gemini-3.5-flash-lite"
    assert client.call["config"].response_schema.model_fields == result.__class__.model_fields
    assert client.closed


def test_gemini_invalid_structured_output_propagates(settings):
    client = FakeClient(SimpleNamespace(parsed=None, text=json.dumps({"category": "not-supported",
        "summary": "Virheellinen luokka.", "ambiguous": False, "confidence": 0.9})))
    with pytest.raises(ValidationError):
        analyzer(settings, client).analyze(LEAD)
    assert client.closed


def test_gemini_timeout_propagates(settings):
    client = FakeClient(error=TimeoutError("request timed out"))
    with pytest.raises(TimeoutError):
        analyzer(settings, client).analyze(LEAD)
    assert client.closed


def test_gemini_uncertain_output_routes_to_human(settings):
    client = FakeClient(SimpleNamespace(parsed={"category": "tarjouspyyntö",
        "summary": "Viesti sisältää ristiriitaisia tarkoituksia.", "ambiguous": True, "confidence": 0.91}, text=None))
    result = analyzer(settings, client).analyze(LEAD)
    assert normalize_analysis(result)[0] == "ihmiselle"


def test_gemini_empty_response_propagates(settings):
    client = FakeClient(SimpleNamespace(parsed=None, text=""))
    with pytest.raises(ValueError, match="empty response"):
        analyzer(settings, client).analyze(LEAD)
