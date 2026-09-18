"""Keep tests independent of local credentials, .env files, and the network."""

import os
import socket

import dotenv
import pytest
import requests


# Config evaluates some defaults at import time, before fixtures can run.
# Apply this guard while conftest is loaded, before importing application modules.
_environment = pytest.MonkeyPatch()
_environment.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
for _name in list(os.environ):
    if _name.startswith(("REP0RTER_", "TELEGRAM_", "AI_")):
        _environment.delenv(_name)


def pytest_unconfigure(config):
    _environment.undo()


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, tmp_path):
    """Tests can replace these entrypoints with fakes, never real transports."""
    monkeypatch.setenv("REP0RTER_DATA_DIR", str(tmp_path / "data"))

    def deny_network(*args, **kwargs):
        raise AssertionError("Network access is disabled in tests; use an offline fake")

    monkeypatch.setattr(requests.sessions.Session, "request", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket, "gethostbyname", deny_network)
    monkeypatch.setattr(socket, "gethostbyname_ex", deny_network)
    monkeypatch.setattr(socket, "gethostbyaddr", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket.socket, "sendto", deny_network)
