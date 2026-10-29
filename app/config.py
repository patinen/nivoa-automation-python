"""Centralized, environment-driven settings."""
from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("NIVOA_ENV_FILE", PROJECT_ROOT / ".env")).resolve()
_PROCESS_ENV_KEYS = set(os.environ)
_DOTENV_KEYS = set(dotenv_values(ENV_FILE)) if ENV_FILE.is_file() else set()
load_dotenv(ENV_FILE, override=False)


def _project_path(value: str) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())


def setting_source(name: str) -> str:
    if name in _PROCESS_ENV_KEYS:
        return "process environment"
    if name in _DOTENV_KEYS:
        return str(ENV_FILE)
    return "application default"


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/messages.sqlite3"
    webhook_token: str = ""
    app_env: str = "development"
    analyzer_mode: str = "fixture"  # fixture or gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: int = 30
    sheet_mode: str = "mock"  # mock or google
    sheet_id: str = ""
    sheet_tab: str = "Leads"
    google_auth_mode: str = "oauth"  # oauth or service_account
    google_credentials_file: str = ""
    google_oauth_client_file: str = ".secrets/google-oauth-client.json"
    google_token_file: str = ".secrets/google-token.json"
    email_mode: str = "mock"  # mock or smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"  # starttls or ssl
    smtp_timeout_seconds: int = 30
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    owner_email: str = ""
    error_email: str = ""
    mock_dir: str = "data/mock"
    retry_limit: int = 5
    worker_poll_seconds: int = 3
    lease_seconds: int = 300

    @classmethod
    def from_env(cls) -> "Settings":
        get = os.getenv
        s = cls(
            database_path=_project_path(get("DATABASE_PATH", cls.database_path)),
            webhook_token=get("WEBHOOK_TOKEN", ""),
            app_env=get("APP_ENV", "development"),
            analyzer_mode=get("ANALYZER_MODE", "fixture").lower(),
            gemini_api_key=get("GEMINI_API_KEY", ""),
            gemini_model=get("GEMINI_MODEL", cls.gemini_model),
            gemini_timeout_seconds=int(get("GEMINI_TIMEOUT_SECONDS", "30")),
            sheet_mode=get("SHEET_MODE", "mock").lower(),
            sheet_id=get("GOOGLE_SHEET_ID", ""),
            sheet_tab=get("GOOGLE_SHEET_TAB", "Leads"),
            google_auth_mode=get("GOOGLE_AUTH_MODE", cls.google_auth_mode).lower(),
            google_credentials_file=_project_path(get("GOOGLE_CREDENTIALS_FILE", "")),
            google_oauth_client_file=_project_path(get("GOOGLE_OAUTH_CLIENT_FILE", ".secrets/google-oauth-client.json")),
            google_token_file=_project_path(get("GOOGLE_TOKEN_FILE", ".secrets/google-token.json")),
            email_mode=get("EMAIL_MODE", "mock").lower(),
            smtp_host=get("SMTP_HOST", ""),
            smtp_port=int(get("SMTP_PORT", "587")),
            smtp_security=get("SMTP_SECURITY", "starttls").lower(),
            smtp_timeout_seconds=int(get("SMTP_TIMEOUT_SECONDS", "30")),
            smtp_username=get("SMTP_USERNAME", ""),
            smtp_password=get("SMTP_PASSWORD", ""),
            email_from=get("EMAIL_FROM", ""),
            owner_email=get("OWNER_EMAIL", ""),
            error_email=get("ERROR_EMAIL", ""),
            mock_dir=_project_path(get("MOCK_DIR", "data/mock")),
            retry_limit=int(get("RETRY_LIMIT", "5")),
            worker_poll_seconds=int(get("WORKER_POLL_SECONDS", "3")),
            lease_seconds=int(get("LEASE_SECONDS", "300")),
        )
        s.validate()
        return s

    def validate(self) -> None:
        if self.app_env == "production":
            if len(self.webhook_token) < 24:
                raise ValueError("In production WEBHOOK_TOKEN must be >= 24 characters")
            if "fixture" == self.analyzer_mode or self.sheet_mode == "mock" or self.email_mode == "mock":
                raise ValueError("Production requires real integrations, not mock/fixture modes")
        if self.analyzer_mode not in {"fixture", "gemini"}:
            raise ValueError("Unknown ANALYZER_MODE")
        if self.sheet_mode not in {"mock", "google"}:
            raise ValueError("Unknown SHEET_MODE")
        if self.google_auth_mode not in {"service_account", "oauth"}:
            raise ValueError("Unknown GOOGLE_AUTH_MODE")
        if self.email_mode not in {"mock", "smtp"}:
            raise ValueError("Unknown EMAIL_MODE")
        if self.smtp_security not in {"starttls", "ssl"}:
            raise ValueError("SMTP_SECURITY must be starttls or ssl")
        if self.smtp_timeout_seconds < 1:
            raise ValueError("SMTP_TIMEOUT_SECONDS must be positive")
        if self.retry_limit < 1 or self.lease_seconds < 30 or self.gemini_timeout_seconds < 1:
            raise ValueError("RETRY_LIMIT and LEASE_SECONDS must be positive")

    def ensure_paths(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.mock_dir).mkdir(parents=True, exist_ok=True)
