from dataclasses import replace

from app.e2e_live import live_preflight


def test_live_preflight_reports_required_configuration(settings, monkeypatch):
    monkeypatch.setattr("app.e2e_live.setting_source", lambda name: "application default")
    failures = live_preflight(settings)

    assert "an explicit DATABASE_PATH" in failures
    assert "GEMINI_API_KEY" in failures
    assert "GOOGLE_SHEET_ID" in failures
    assert "SMTP_HOST" in failures


def test_live_preflight_accepts_complete_local_configuration(settings, tmp_path, monkeypatch):
    token = tmp_path / "google-token.json"
    token.write_text("{}", encoding="utf-8")
    configured = replace(
        settings,
        analyzer_mode="gemini",
        gemini_api_key="not-a-real-key",
        sheet_mode="google",
        sheet_id="not-a-real-sheet",
        google_auth_mode="oauth",
        google_token_file=str(token),
        email_mode="smtp",
        smtp_host="smtp.example.test",
        smtp_username="owner@example.test",
        smtp_password="not-a-real-password",
        email_from="owner@example.test",
    )
    monkeypatch.setattr("app.e2e_live.setting_source", lambda name: "test environment")

    assert live_preflight(configured) == []
