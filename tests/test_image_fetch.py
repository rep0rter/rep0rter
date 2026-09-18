"""Remote identity images must connect only to validated, pinned public IPs."""

import io
import socket
import ssl

import certifi
import pytest
from PIL import Image

from rep0rter.config import Config
from rep0rter.publishers import cards
from rep0rter.store import Event


class Response:
    def __init__(self, data=b"", status=200, headers=None):
        self.data = io.BytesIO(data)
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def read1(self, count, decode_content):
        assert decode_content is False
        return self.data.read(count)

    def close(self):
        self.closed = True


def png():
    out = io.BytesIO()
    Image.new("RGB", (3, 2), "green").save(out, "PNG")
    return out.getvalue()


def fake_transport(monkeypatch, responses):
    calls = []

    class Pool:
        def __init__(self, host, **kwargs):
            self.call = {"host": host, **kwargs}
            calls.append(self.call)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def urlopen(self, method, target, **kwargs):
            self.call.update(method=method, target=target, request=kwargs)
            return responses.pop(0)

    monkeypatch.setattr(cards.urllib3, "HTTPSConnectionPool", Pool)
    return calls


def address(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def test_download_pins_validated_ip_and_preserves_tls_hostname(monkeypatch):
    lookups = []

    def resolve(host, port, **kwargs):
        lookups.append(host)
        # A second lookup would simulate rebinding to a private address.
        return address("1.1.1.1" if len(lookups) == 1 else "127.0.0.1")

    monkeypatch.setattr(cards.socket, "getaddrinfo", resolve)
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid:443")
    response = Response(png())
    calls = fake_transport(monkeypatch, [response])
    output = cards._fetch_image("https://images.example.test/avatar.png?size=72")
    with Image.open(io.BytesIO(output)) as image:
        assert image.size == (3, 2)
    assert lookups == ["images.example.test"]
    assert len(calls) == 1
    call = calls[0]
    assert call["host"] == "1.1.1.1"
    assert call["port"] == 443
    assert call["server_hostname"] == call["assert_hostname"] == "images.example.test"
    assert call["cert_reqs"] == ssl.CERT_REQUIRED
    assert call["ca_certs"] == certifi.where()
    assert "_proxy" not in call
    assert call["method"] == "GET"
    assert call["target"] == "/avatar.png?size=72"
    assert call["request"]["headers"]["Host"] == "images.example.test"
    assert call["request"]["headers"]["Accept-Encoding"] == "identity"
    assert call["request"]["redirect"] is False
    assert call["request"]["retries"] is False
    assert call["request"]["preload_content"] is False
    assert response.closed


def test_redirect_to_private_address_is_rejected_before_connect(monkeypatch):
    monkeypatch.setattr(cards.socket, "getaddrinfo", lambda host, *a, **kw:
                        address("127.0.0.1" if host == "internal.example.test" else "1.1.1.1"))
    response = Response(status=302, headers={"Location": "https://internal.example.test/secret"})
    calls = fake_transport(monkeypatch, [response])
    assert cards._fetch_image("https://images.example.test/logo") is None
    assert len(calls) == 1
    assert response.closed


def test_public_redirect_is_resolved_and_pinned_separately(monkeypatch):
    monkeypatch.setattr(cards.socket, "getaddrinfo", lambda host, *a, **kw:
                        address("1.0.0.1" if host == "cdn.example.test" else "1.1.1.1"))
    redirect = Response(status=302, headers={"Location": "https://cdn.example.test/image.png"})
    image = Response(png())
    calls = fake_transport(monkeypatch, [redirect, image])
    assert cards._fetch_image("https://images.example.test/logo")
    assert [call["host"] for call in calls] == ["1.1.1.1", "1.0.0.1"]
    assert [call["assert_hostname"] for call in calls] == ["images.example.test", "cdn.example.test"]
    assert redirect.closed and image.closed


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_initial_private_destination_is_never_contacted(monkeypatch, ip):
    monkeypatch.setattr(cards.socket, "getaddrinfo", lambda *a, **kw: address(ip))
    calls = fake_transport(monkeypatch, [])
    assert cards._fetch_image("https://images.example.test/logo") is None
    assert calls == []


def test_oversized_download_is_closed_without_image_decode(monkeypatch):
    monkeypatch.setattr(cards.socket, "getaddrinfo", lambda *a, **kw: address("1.1.1.1"))
    monkeypatch.setattr(cards, "MAX_IMAGE_BYTES", 32)
    response = Response(b"x" * 100)
    fake_transport(monkeypatch, [response])
    assert cards._fetch_image("https://images.example.test/logo") is None
    assert response.closed
    assert response.data.tell() == 33


def test_total_time_budget_stops_streaming(monkeypatch):
    monkeypatch.setattr(cards.socket, "getaddrinfo", lambda *a, **kw: address("1.1.1.1"))
    ticks = iter([100, 101, 119, 121])
    monkeypatch.setattr(cards.time, "monotonic", lambda: next(ticks))
    response = Response(png())
    fake_transport(monkeypatch, [response])
    assert cards._fetch_image("https://images.example.test/logo") is None
    assert response.closed


def test_tls_failure_falls_back_and_is_cached(monkeypatch, tmp_path):
    attempts = []

    def fail(url):
        attempts.append(url)
        raise cards.urllib3.exceptions.SSLError("synthetic certificate mismatch")

    monkeypatch.setattr(cards, "_fetch_image", fail)
    event = Event(id="slack:C_TEST:1", source="slack", kind="message", container_id="slack:C_TEST",
                  ts=1, meta={"avatar_url": "https://images.example.test/avatar"})
    cfg = Config(data_dir=tmp_path)
    assert cards.identity_image(event, cfg) is None
    assert cards.identity_image(event, cfg) is None
    assert len(attempts) == 1
