import json

import pytest
import requests
from google.auth.exceptions import RefreshError

from app.google_diagnostics import (GoogleSheetsOperationError,
                                    diagnose_google_exception, sanitize_text)


CASES = [
    (400, "INVALID_ARGUMENT", "badRequest", "Invalid value at 'range'", "GOOGLE_SHEET_TAB"),
    (401, "UNAUTHENTICATED", "authError", "Request had invalid authentication credentials", "app.google_auth"),
    (403, "PERMISSION_DENIED", "forbidden", "The caller does not have permission", "Enable the Sheets API"),
    (404, "NOT_FOUND", "notFound", "Requested entity was not found", "GOOGLE_SHEET_ID"),
    (429, "RESOURCE_EXHAUSTED", "rateLimitExceeded", "Quota exceeded", "quota"),
]


def http_error(status, api_status, reason, message):
    response = requests.Response()
    response.status_code = status
    response.reason = api_status
    response.url = "https://sheets.googleapis.com/private?id=spreadsheet-secret&access_token=token-secret"
    response._content = json.dumps({"error": {"code": status, "status": api_status,
                                               "message": message,
                                               "errors": [{"reason": reason, "message": message}]}}).encode()
    return requests.HTTPError(
        f"{status} failure for url: {response.url} Authorization: Bearer token-secret",
        response=response,
    )


@pytest.mark.parametrize("status,api_status,reason,message,suggestion", CASES)
def test_google_http_diagnostics_are_actionable_and_sanitized(
        status, api_status, reason, message, suggestion):
    diagnostic = diagnose_google_exception(
        http_error(status, api_status, reason, message), "spreadsheet metadata lookup")
    assert diagnostic.status_code == status
    assert diagnostic.operation == "spreadsheet metadata lookup"
    assert diagnostic.reason == reason
    assert diagnostic.message == message
    assert suggestion.lower() in diagnostic.suggestion.lower()
    rendered = "\n".join(diagnostic.evidence_lines())
    assert "token-secret" not in rendered
    assert "sheets.googleapis.com" not in rendered
    assert "Authorization" not in rendered


def test_google_api_client_http_error_shape_is_supported():
    class Resp:
        status = 403

    class ClientHttpError(Exception):
        def __init__(self):
            self.resp = Resp()
            self.content = b'{"error":{"message":"Access denied","errors":[{"reason":"forbidden"}]}}'

    diagnostic = diagnose_google_exception(ClientHttpError(), "worksheet lookup")
    assert diagnostic.status_code == 403
    assert diagnostic.reason == "forbidden"
    assert diagnostic.message == "Access denied"


def test_refresh_error_has_authentication_operation_and_safe_guidance():
    diagnostic = diagnose_google_exception(
        RefreshError("invalid_grant: Token has been expired or revoked."), "loading credentials")
    assert diagnostic.operation == "authentication"
    assert diagnostic.reason == "refresh_failed"
    assert "app.google_auth" in diagnostic.suggestion


def test_operation_error_reuses_already_sanitized_diagnostic():
    original = diagnose_google_exception(http_error(404, "NOT_FOUND", "notFound", "Missing"),
                                         "reading worksheet rows")
    assert diagnose_google_exception(GoogleSheetsOperationError(original)) is original


def test_sanitizer_redacts_urls_and_secret_fields():
    value = "See https://example.test/path access_token=abc client_secret=xyz Bearer token"
    sanitized = sanitize_text(value)
    assert "https://" not in sanitized
    assert "abc" not in sanitized and "xyz" not in sanitized and "Bearer token" not in sanitized
