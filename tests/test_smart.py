"""Unit tests for the /smart endpoint client (HTTP mocked)."""

from __future__ import annotations

import io
import urllib.error

import pytest

from mitsubishi_echonet import smart


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _patched_urlopen(monkeypatch: pytest.MonkeyPatch, responder: object) -> list[object]:
    captured: list[object] = []

    def fake_urlopen(request: object, timeout: float = 0) -> object:
        captured.append((request, timeout))
        if isinstance(responder, Exception):
            raise responder
        return responder

    monkeypatch.setattr(smart.urllib.request, "urlopen", fake_urlopen)
    return captured


class TestPayloads:
    def test_enable_payload_is_the_published_one(self) -> None:
        # Byte-exact match with the community-published command in
        # echonetlite_homeassistant issue #226.
        assert smart.ENABLE_ECHONET_PAYLOAD == (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<ESV>7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFvP93"
            "fQ30AvEJCNcd0GTwPID0F8V5eyMVj/qAQCXFqYrRtJh8MIpm2/h7jZ2SsPj0</ESV>"
        )

    def test_disable_payload_differs_only_in_ciphertext(self) -> None:
        prefix = '<?xml version="1.0" encoding="UTF-8"?><ESV>'
        assert smart.DISABLE_ECHONET_PAYLOAD.startswith(prefix)
        assert smart.DISABLE_ECHONET_PAYLOAD.endswith("</ESV>")
        assert smart.DISABLE_ECHONET_PAYLOAD != smart.ENABLE_ECHONET_PAYLOAD


class TestSmartUrl:
    def test_default_port_omitted(self) -> None:
        assert smart.smart_url("192.0.2.10") == "http://192.0.2.10/smart"

    def test_explicit_port(self) -> None:
        assert smart.smart_url("192.0.2.10", 8080) == "http://192.0.2.10:8080/smart"


class TestPostSmart:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = '<?xml version="1.0" encoding="UTF-8"?><ESV>encryptedreply</ESV>'
        captured = _patched_urlopen(monkeypatch, FakeResponse(200, body))
        result = smart.enable_echonet("192.0.2.10", timeout=5.0)
        assert result.ok
        assert result.status == 200
        assert result.error is None
        (request, timeout), = captured
        assert timeout == 5.0
        assert request.headers["Content-type"] == "text/xml"  # type: ignore[attr-defined]
        assert request.data == smart.ENABLE_ECHONET_PAYLOAD.encode()  # type: ignore[attr-defined]
        assert "Authorization" not in request.headers  # type: ignore[attr-defined]

    def test_disable_sends_disable_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = '<?xml version="1.0" encoding="UTF-8"?><ESV>ok</ESV>'
        captured = _patched_urlopen(monkeypatch, FakeResponse(200, body))
        result = smart.disable_echonet("192.0.2.10")
        assert result.ok
        (request, _), = captured
        assert request.data == smart.DISABLE_ECHONET_PAYLOAD.encode()  # type: ignore[attr-defined]

    def test_200_without_esv_is_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patched_urlopen(monkeypatch, FakeResponse(200, "<html>nope</html>"))
        result = smart.enable_echonet("192.0.2.10")
        assert not result.ok
        assert "unexpected response body" in (result.error or "")

    def test_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exc = urllib.error.HTTPError(
            "http://x/smart", 401, "Unauthorized", None, io.BytesIO(b"denied")  # type: ignore[arg-type]
        )
        _patched_urlopen(monkeypatch, exc)
        result = smart.enable_echonet("192.0.2.10")
        assert not result.ok
        assert result.status == 401
        assert "401" in (result.error or "")

    def test_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patched_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
        result = smart.enable_echonet("192.0.2.10")
        assert not result.ok
        assert result.status is None
        assert "cannot reach" in (result.error or "")

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patched_urlopen(monkeypatch, TimeoutError())
        result = smart.enable_echonet("192.0.2.10", timeout=1.5)
        assert not result.ok
        assert "timed out" in (result.error or "")
