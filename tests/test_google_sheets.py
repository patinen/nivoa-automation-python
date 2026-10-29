from dataclasses import replace

import pytest

from app.logic import Processor
from app.models import LeadInput
from app.services import (FixtureAnalyzer, GoogleSheet, MockEmail,
                          RecoverableGoogleAuthError)


ROW = ["2026-10-29T10:00:00+00:00", "Testihenkilö", "testi@esimerkki.fi",
       "tarjouspyyntö", "=UNTRUSTED()"]
HEADERS = [["Aika", "Nimi", "Yhteystieto", "Luokka", "Tiivistelmä", "Message ID"]]


class Response:
    def __init__(self, values=None, error=None):
        self.values = values or []
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return {"values": self.values}


class Session:
    def __init__(self, gets=(), posts=()):
        self.gets = list(gets)
        self.posts = list(posts)
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        result = self.gets.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        result = self.posts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def sheet(settings, session, credentials_factory=None):
    configured = replace(settings, sheet_mode="google", sheet_id="sheet-id",
                         sheet_tab="Nivoa Leads", google_auth_mode="service_account",
                         google_credentials_file="secret.json")
    credentials_factory = credentials_factory or (lambda path, scopes: object())
    return GoogleSheet(configured, credentials_factory=credentials_factory,
                       session_factory=lambda credentials: session)


def test_google_sheet_initializes_headers_and_appends_raw(settings):
    session = Session(gets=[Response([]), Response([])], posts=[Response(), Response()])
    sheet(settings, session).append_once("message-1", ROW)
    assert len(session.post_calls) == 2
    header_call, row_call = session.post_calls
    assert header_call[1]["json"]["values"] == HEADERS
    assert row_call[1]["params"]["valueInputOption"] == "RAW"
    assert row_call[1]["json"]["values"] == [[*ROW, "message-1"]]
    assert row_call[1]["json"]["values"][0][4] == "=UNTRUSTED()"


def test_google_sheet_detects_duplicate_id(settings):
    session = Session(gets=[Response(HEADERS), Response([["message-1"]])])
    sheet(settings, session).append_once("message-1", ROW)
    assert session.post_calls == []


def test_google_sheet_authentication_error_propagates(settings):
    def fail_auth(path, scopes):
        raise ValueError("invalid service account credentials")

    with pytest.raises(ValueError, match="invalid service account"):
        sheet(settings, Session(), credentials_factory=fail_auth).append_once("message-1", ROW)


def test_google_sheet_unavailable_propagates(settings):
    session = Session(gets=[ConnectionError("Sheets unavailable")])
    with pytest.raises(ConnectionError, match="unavailable"):
        sheet(settings, session).append_once("message-1", ROW)


def test_google_sheet_ambiguous_append_reconciles_committed_row(settings):
    session = Session(
        gets=[Response(HEADERS), Response([]), Response([["message-1"]])],
        posts=[TimeoutError("response lost after commit")],
    )
    sheet(settings, session).append_once("message-1", ROW)
    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 3


def test_google_sheet_ambiguous_append_without_row_propagates(settings):
    session = Session(
        gets=[Response(HEADERS), Response([]), Response([])],
        posts=[TimeoutError("append outcome unknown")],
    )
    with pytest.raises(TimeoutError, match="outcome unknown"):
        sheet(settings, session).append_once("message-1", ROW)


class OAuthCredentials:
    def __init__(self, valid=True, expired=False, refresh_token="refresh-token",
                 refresh_error=None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_error = refresh_error
        self.refreshed_with = None

    def has_scopes(self, scopes):
        return scopes == [GoogleSheet.SCOPE]

    def refresh(self, request):
        self.refreshed_with = request
        if self.refresh_error:
            raise self.refresh_error
        self.valid = True
        self.expired = False

    def to_json(self):
        return '{"token":"refreshed-test-token"}'


def oauth_sheet(settings, tmp_path, credentials, session=None):
    token_path = tmp_path / "google-token.json"
    token_path.write_text("{}", encoding="utf-8")
    configured = replace(settings, sheet_mode="google", sheet_id="sheet-id",
                         google_auth_mode="oauth", google_credentials_file="",
                         google_oauth_client_file=str(tmp_path / "google-oauth-client.json"),
                         google_token_file=str(token_path))
    captured = {}

    def load(path, scopes):
        captured["path"] = path
        captured["scopes"] = scopes
        return credentials

    adapter = GoogleSheet(configured, oauth_credentials_factory=load,
                          request_factory=lambda: "refresh-request",
                          session_factory=(lambda value: session or ("session", value)))
    return adapter, token_path, captured


def test_google_oauth_loads_existing_authorized_token(settings, tmp_path):
    credentials = OAuthCredentials(valid=True)
    adapter, token_path, captured = oauth_sheet(settings, tmp_path, credentials)
    created_session = adapter._session()
    assert created_session == ("session", credentials)
    assert captured == {"path": str(token_path), "scopes": [GoogleSheet.SCOPE]}
    assert token_path.read_text(encoding="utf-8") == "{}"


def test_google_oauth_refreshes_and_persists_expired_token(settings, tmp_path):
    credentials = OAuthCredentials(valid=False, expired=True)
    adapter, token_path, _ = oauth_sheet(settings, tmp_path, credentials)
    assert adapter._credentials() is credentials
    assert credentials.refreshed_with == "refresh-request"
    assert token_path.read_text(encoding="utf-8") == credentials.to_json()


def test_google_oauth_revoked_refresh_is_recoverable(settings, tmp_path):
    credentials = OAuthCredentials(valid=False, expired=True,
                                   refresh_error=ValueError("invalid_grant"))
    adapter, token_path, _ = oauth_sheet(settings, tmp_path, credentials)
    with pytest.raises(RecoverableGoogleAuthError, match="refresh failed"):
        adapter._credentials()
    assert token_path.read_text(encoding="utf-8") == "{}"


def test_google_oauth_missing_token_is_recoverable(settings, tmp_path):
    configured = replace(settings, sheet_mode="google", sheet_id="sheet-id",
                         google_auth_mode="oauth", google_credentials_file="",
                         google_token_file=str(tmp_path / "missing-token.json"))
    with pytest.raises(RecoverableGoogleAuthError, match="token missing"):
        GoogleSheet(configured)._credentials()


def test_revoked_oauth_token_preserves_message_for_retry(settings, store, tmp_path):
    credentials = OAuthCredentials(valid=False, expired=True,
                                   refresh_error=ValueError("invalid_grant"))
    adapter, _, _ = oauth_sheet(settings, tmp_path, credentials)
    lead = LeadInput(name="Testihenkilö", email="testi@esimerkki.fi",
                     message="Haluaisin tarjouksen kirjanpidosta.")
    id, _ = store.accept(lead, "revoked-oauth")
    Processor(store, settings, FixtureAnalyzer(), adapter, MockEmail(settings)).process_one()
    row = store.get(id)
    assert row["status"] == "needs_attention"
    assert row["sheet_done"] == 0
    assert row["message"] == lead.message
    assert "RecoverableGoogleAuthError" in row["last_error"]
