import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import create_app

payload = {"name":"Test Person", "email":"test@esimerkki.fi", "phone":None, "message":"Hi"}


def test_accept_persist_and_auth(settings):
    client = TestClient(create_app(settings))
    assert client.post("/webhook", json=payload).status_code == 401
    response = client.post("/webhook", json=payload, headers={"X-Webhook-Token":"secret-token",
                                                             "Idempotency-Key":"abc"})
    assert response.status_code == 202
    id = response.json()["id"]
    row = client.app.state.store.get(id)
    assert row["message"] == "Hi" and row["status"] == "received"
    second = client.post("/webhook", json=payload, headers={"X-Webhook-Token":"secret-token",
                                                           "Idempotency-Key":"abc"})
    assert second.json()["id"] == id and second.json()["created"] is False
    separate = client.post("/webhook", json=payload, headers={"X-Webhook-Token":"secret-token",
                                                               "Idempotency-Key":"separate-submission"})
    assert separate.status_code == 202
    assert separate.json()["created"] is True and separate.json()["id"] != id
    other = dict(payload, message="something else")
    assert client.post("/webhook", json=other, headers={"X-Webhook-Token":"secret-token",
                                                           "Idempotency-Key":"abc"}).status_code == 409


def test_empty_message_is_accepted(settings):
    client = TestClient(create_app(settings))
    p = dict(payload, message="")
    assert client.post("/webhook", json=p, headers={"X-Webhook-Token":"secret-token"}).status_code == 202


def test_invalid_payload_not_accepted(settings):
    client = TestClient(create_app(settings))
    before = len(client.app.state.store.list_messages())
    response = client.post("/webhook", json={"name":"a", "email":"bad"},
                           headers={"X-Webhook-Token":"secret-token"})
    assert response.status_code == 422
    assert response.json()["detail"]
    assert len(client.app.state.store.list_messages()) == before


def test_incomplete_payload_not_falsely_acknowledged(settings):
    client = TestClient(create_app(settings))
    response = client.post("/webhook", json={"message": "Nimi ja sähköposti puuttuvat"},
                           headers={"X-Webhook-Token": "secret-token"})
    assert response.status_code == 422
    assert len(client.app.state.store.list_messages()) == 0


def test_production_rejects_missing_auth_and_mock(tmp_path):
    from app.config import Settings
    from pytest import raises
    s = Settings(app_env="production", database_path=str(tmp_path/"db.sqlite"))
    with raises(ValueError):
        create_app(s)
