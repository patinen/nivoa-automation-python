from dataclasses import replace

from app.google_auth import authorize
from app.services import GoogleSheet


class Credentials:
    def to_json(self):
        return '{"token":"offline-test-token","refresh_token":"offline-test-refresh"}'


class Flow:
    def __init__(self):
        self.kwargs = None

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return Credentials()


def test_explicit_google_oauth_cli_flow_saves_token(settings, tmp_path):
    client = tmp_path / "google-oauth-client.json"
    token = tmp_path / "google-token.json"
    client.write_text('{"installed":{}}', encoding="utf-8")
    configured = replace(settings, google_auth_mode="oauth",
                         google_oauth_client_file=str(client), google_token_file=str(token))
    flow = Flow()
    captured = {}

    def flow_factory(path, scopes):
        captured["path"] = path
        captured["scopes"] = scopes
        return flow

    assert authorize(configured, flow_factory=flow_factory) == token
    assert captured == {"path": str(client), "scopes": [GoogleSheet.SCOPE]}
    assert flow.kwargs["open_browser"] is True
    assert flow.kwargs["access_type"] == "offline"
    assert flow.kwargs["prompt"] == "consent"
    assert token.read_text(encoding="utf-8") == Credentials().to_json()
