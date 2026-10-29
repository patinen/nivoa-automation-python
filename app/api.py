"""Minimal authenticated webhook. Return 202 only after SQLite commit."""
import hmac
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.config import Settings
from app.db import Store
from app.models import LeadInput


class Accepted(BaseModel):
    id: str
    status: str
    created: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings)
    app = FastAPI(title="Nivoa Lead Automation", version="1.0.0")
    app.state.store = store

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/webhook", response_model=Accepted, status_code=202)
    def webhook(lead: LeadInput, x_webhook_token: str = Header(default=""),
                idempotency_key: str | None = Header(default=None)):
        if settings.webhook_token and not hmac.compare_digest(x_webhook_token, settings.webhook_token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        if idempotency_key is not None and (len(idempotency_key) > 128 or not idempotency_key.strip()):
            raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
        try:
            id, created = store.accept(lead, idempotency_key)
        except ValueError:
            raise HTTPException(status_code=409, detail="Idempotency-Key conflict") from None
        return Accepted(id=id, status="received", created=created)

    return app


app = create_app()
