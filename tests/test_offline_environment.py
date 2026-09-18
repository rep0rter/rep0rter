import socket

import dotenv
import pytest
import requests

from rep0rter.config import Config


def test_dotenv_files_cannot_supply_credentials(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=synthetic-token\n", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert dotenv.load_dotenv(env_file) is False
    assert Config().telegram_bot_token is None
    assert Config().data_dir == tmp_path / "data"


def test_requests_and_socket_connections_are_blocked():
    with pytest.raises(AssertionError, match="Network access is disabled"):
        requests.get("https://example.test/")
    with pytest.raises(AssertionError, match="Network access is disabled"):
        socket.create_connection(("example.test", 443))
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="Network access is disabled"):
            connection.connect(("127.0.0.1", 80))
