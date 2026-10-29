"""Explicit interactive Google OAuth setup; never imported by API or worker startup."""
import argparse
from pathlib import Path

from app.config import Settings
from app.services import GoogleSheet


def authorize(settings: Settings, flow_factory=None) -> Path:
    """Run the user-initiated browser flow and atomically save its token."""
    client_path = Path(settings.google_oauth_client_file)
    token_path = Path(settings.google_token_file)
    if not client_path.is_file():
        raise FileNotFoundError(f"OAuth Desktop client file not found: {client_path}")

    if flow_factory is None:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow_factory = InstalledAppFlow.from_client_secrets_file
    flow = flow_factory(str(client_path), [GoogleSheet.SCOPE])
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        success_message="Nivoa authorization completed. You may close this browser window.",
    )
    token_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = token_path.with_name(token_path.name + ".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    temporary.replace(token_path)
    return token_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize Nivoa Google Sheets access in a browser")
    parser.parse_args()
    settings = Settings.from_env()
    if settings.google_auth_mode != "oauth":
        parser.error("Set GOOGLE_AUTH_MODE=oauth before running this command")

    try:
        token_path = authorize(settings)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    print(f"Google OAuth authorization completed; token saved to {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
