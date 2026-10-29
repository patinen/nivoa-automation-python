import pytest
from app.config import Settings
from app.db import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=str(tmp_path / "messages.sqlite3"),
                    mock_dir=str(tmp_path / "mock"), webhook_token="secret-token",
                    owner_email="owner@example.test", error_email="ops@example.test")


@pytest.fixture
def store(settings):
    return Store(settings)
