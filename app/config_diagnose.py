"""Print effective non-secret configuration and where each value came from."""
from pathlib import Path

from app.config import ENV_FILE, Settings, setting_source


def main() -> int:
    settings = Settings.from_env()
    print("Nivoa effective configuration (secrets omitted)")
    print(f"Environment file: {ENV_FILE}")
    fields = [
        ("DATABASE_PATH", settings.database_path),
        ("ANALYZER_MODE", settings.analyzer_mode),
        ("SHEET_MODE", settings.sheet_mode),
        ("GOOGLE_AUTH_MODE", settings.google_auth_mode),
        ("GOOGLE_SHEET_TAB", settings.sheet_tab),
        ("EMAIL_MODE", settings.email_mode),
        ("SMTP_SECURITY", settings.smtp_security),
    ]
    for name, value in fields:
        print(f"{name}: {value} (source: {setting_source(name)})")
    if settings.google_auth_mode == "oauth":
        print(f"OAuth token file present: {Path(settings.google_token_file).is_file()}")
        print(f"OAuth client file present: {Path(settings.google_oauth_client_file).is_file()}")
    else:
        print(f"Service-account credential file present: {Path(settings.google_credentials_file).is_file()}")
    print("Secret values, tokens, spreadsheet IDs, email addresses and passwords are not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
