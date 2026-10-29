"""Sanitized Google API diagnostics safe for logs and evidence reports."""
import json
import re
from dataclasses import dataclass


_SECRET_PATTERNS = [
    (re.compile(r"https?://\S+", re.IGNORECASE), "[redacted URL]"),
    (re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE), "Bearer [redacted]"),
    (re.compile(r"(?i)(access_token|refresh_token|client_secret|authorization)\s*[:=]\s*[^\s,;}]+"),
     r"\1=[redacted]"),
]


def sanitize_text(value, fallback: str = "No additional message") -> str:
    text = " ".join(str(value or "").split())
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.replace("`", "'").replace("|", "/")[:500] or fallback


@dataclass(frozen=True)
class GoogleErrorDiagnostic:
    operation: str
    status_code: int | None
    reason: str
    message: str
    suggestion: str

    def evidence_lines(self) -> list[str]:
        return [
            f"- Operation: `{sanitize_text(self.operation)}`",
            f"- HTTP status: `{self.status_code if self.status_code is not None else 'not available'}`",
            f"- Reason: `{sanitize_text(self.reason)}`",
            f"- Message: {sanitize_text(self.message)}",
            f"- Suggested action: {sanitize_text(self.suggestion)}",
        ]


class GoogleSheetsOperationError(RuntimeError):
    def __init__(self, diagnostic: GoogleErrorDiagnostic):
        self.diagnostic = diagnostic
        status = f"HTTP {diagnostic.status_code}" if diagnostic.status_code else "no HTTP status"
        super().__init__(f"{diagnostic.operation}: {status}; {diagnostic.reason}; {diagnostic.message}")


def _payload_from_exception(exc) -> tuple[int | None, dict]:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    payload = None
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = None

    # googleapiclient.errors.HttpError exposes resp.status and content rather than response.
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    if payload is None:
        content = getattr(exc, "content", None)
        if content:
            try:
                payload = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
            except Exception:
                payload = None
    return status, payload if isinstance(payload, dict) else {}


def _suggestion(status: int | None, reason: str) -> str:
    reason_lower = reason.lower()
    if status == 400:
        return "Check GOOGLE_SHEET_TAB and the requested A1 range; confirm the worksheet title exactly."
    if status == 401 or "invalid_grant" in reason_lower or "refresh" in reason_lower or "reauthorization" in reason_lower:
        return "Reauthorize with 'python -m app.google_auth' and retry after a new token is saved."
    if status == 403:
        return "Enable the Sheets API and confirm the authorized Google account can open and edit the spreadsheet."
    if status == 404:
        return "Check GOOGLE_SHEET_ID and GOOGLE_SHEET_TAB; confirm the spreadsheet and worksheet still exist."
    if status == 429:
        return "Google API quota was exceeded; wait for retry backoff and review the project quota."
    return "Check OAuth authorization, Sheets API availability, and the configured spreadsheet and worksheet."


def diagnose_google_exception(exc, operation: str = "Google Sheets request") -> GoogleErrorDiagnostic:
    if isinstance(exc, GoogleSheetsOperationError):
        return exc.diagnostic

    status, payload = _payload_from_exception(exc)
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        error = {}
    details = error.get("errors", [])
    first = details[0] if isinstance(details, list) and details and isinstance(details[0], dict) else {}
    reason = first.get("reason") or error.get("status") or getattr(getattr(exc, "response", None), "reason", None)
    message = error.get("message") or first.get("message")

    type_name = type(exc).__name__
    if type_name == "RefreshError" or type_name == "RecoverableGoogleAuthError":
        operation = "authentication"
        reason = reason or ("refresh_failed" if type_name == "RefreshError" else "reauthorization_required")
    reason = sanitize_text(reason or type_name, fallback=type_name)
    message = sanitize_text(message or str(exc), fallback="Google request failed")
    return GoogleErrorDiagnostic(sanitize_text(operation), status, reason, message,
                                 _suggestion(status, reason))


def is_google_http_error(exc) -> bool:
    module = type(exc).__module__
    return (getattr(exc, "response", None) is not None
            or getattr(exc, "resp", None) is not None
            or module.startswith("googleapiclient.errors"))
