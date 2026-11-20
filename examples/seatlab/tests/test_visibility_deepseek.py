import io
import json
import ssl
from datetime import UTC, datetime
from decimal import Decimal
from http.client import HTTPException, HTTPSConnection
from typing import Any

import certifi
import pytest

from examples.seatlab.visibility import deepseek


class _Spend:
    def reserve(self, *_: object, **__: object) -> None:
        return None

    def settle(self, *_: object, **__: object) -> None:
        return None


class SpendRefused(RuntimeError):
    pass


spend = _Spend()
spend.SpendRefused = SpendRefused

_PRIVATE = "fixture-private-key"
_NOW = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_key() -> None:
    deepseek._KEY = None
    deepseek._BOUND = False


def test_second_key_binding_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PRIVATE)
    deepseek.bind_host_key()
    with pytest.raises(deepseek.SeatUnavailable, match="^key_already_bound$"):
        deepseek.bind_host_key()


class _Response:
    status = 200

    def __init__(self, document: dict[str, Any]) -> None:
        self._body = json.dumps(document).encode()

    def read(self, limit: int) -> bytes:
        assert limit == 64 * 1024 + 1
        return self._body


class _Connection:
    calls: list[tuple[str, str, bytes | None]] = []

    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        timeout: object = None,
        context: object = None,
        **_: object,
    ) -> None:
        assert host == "api.deepseek.com"
        assert port in (None, 443)
        assert context is not None
        assert timeout in (10, 60)

    def request(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> None:
        assert headers["Authorization"] == f"Bearer {_PRIVATE}"
        assert (body is None) == ("Content-Type" not in headers)
        self.calls.append((method, path, body))

    def getresponse(self) -> _Response:
        method, path, _ = self.calls[-1]
        if path == "/user/balance":
            return _Response(
                {
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "USD",
                            "total_balance": "2.00",
                            "granted_balance": "0.50",
                            "topped_up_balance": "1.50",
                        }
                    ],
                }
            )
        document = {
            "model": "deepseek-v4.1-flash-1017",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
                "prompt_cache_hit_tokens": 2,
                "prompt_cache_miss_tokens": 10,
            },
        }
        return _Response(document)

    def close(self) -> None:
        return None


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bound: bool = True,
    environment_key: bool = False,
) -> list[dict[str, Any]]:
    deepseek._KEY = _PRIVATE if bound else None
    deepseek._BOUND = bound
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    if environment_key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", _PRIVATE)
    monkeypatch.setattr(deepseek, "HTTPSConnection", _Connection)
    settled: list[dict[str, Any]] = []

    def reserve(run_id: str, role: str, *, balance_usd: Decimal) -> None:
        assert (run_id, role, balance_usd) == ("run-1", "receiver", Decimal("2.00"))

    def settle(run_id: str, role: str, **kwargs: object) -> None:
        settled.append({"run_id": run_id, "role": role, **kwargs})

    monkeypatch.setattr(deepseek, "_budget", lambda: spend)
    monkeypatch.setattr(spend, "reserve", reserve)
    monkeypatch.setattr(spend, "settle", settle)
    monkeypatch.setattr(
        deepseek, "datetime", type("Clock", (), {"now": staticmethod(lambda tz: _NOW)})
    )
    _Connection.calls.clear()
    return settled


def test_bound_key_leaves_environment_and_uses_direct_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PRIVATE)
    monkeypatch.setenv("OPENAI_API_KEY", _PRIVATE)
    deepseek._KEY = None
    deepseek._BOUND = False
    deepseek.bind_host_key()
    assert deepseek._KEY == _PRIVATE
    assert "DEEPSEEK_API_KEY" not in __import__("os").environ
    settled = _install(monkeypatch)
    result = deepseek.turn("seat", run_id="run-1", role="receiver")
    assert result == deepseek.SeatTurn(
        '{"ok":true}',
        "deepseek-v4.1-flash-1017",
        input_tokens=12,
        cache_tokens=2,
        output_tokens=4,
    )
    assert deepseek.ask("seat", run_id="run-1", role="receiver") == '{"ok":true}'
    assert [(method, path) for method, path, _ in _Connection.calls] == [
        ("GET", "/user/balance"),
        ("POST", "/chat/completions"),
        ("GET", "/user/balance"),
        ("POST", "/chat/completions"),
    ]
    body = json.loads(_Connection.calls[1][2])
    assert body == {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": "Answer only with JSON."},
            {"role": "user", "content": "seat"},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0.7,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    assert settled[0]["served_model"] == "deepseek-v4.1-flash-1017"
    assert settled[0]["cache_hit"] == 2


def test_preflight_reads_balance_without_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    assert deepseek.preflight()["balance_ok"] is True
    assert _Connection.calls == [("GET", "/user/balance", None)]


def test_unbound_environment_key_works_only_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, bound=False, environment_key=True)
    assert deepseek.preflight()["requested_model"] == "deepseek-flash"


@pytest.mark.parametrize(
    ("change", "category"),
    [
        ({"model": "deepseek-v4-flash"}, "served_model_unverified"),
        ({"usage": {"prompt_cache_hit_tokens": 1}}, "usage_unverified"),
    ],
)
def test_unknown_usage_or_model_leaves_reservation(
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    category: str,
) -> None:
    settled = _install(monkeypatch)
    original = _Connection.getresponse

    def changed(self: _Connection) -> _Response:
        response = original(self)
        if self.calls[-1][1] == "/chat/completions":
            document = json.loads(response.read(64 * 1024 + 1))
            document.update(change)
            return _Response(document)
        return response

    monkeypatch.setattr(_Connection, "getresponse", changed)
    with pytest.raises(deepseek.SeatUnavailable, match=f"^{category}$") as raised:
        deepseek.turn("seat", run_id="run-1", role="receiver")
    assert raised.value.settled_usage is None
    assert settled == []


def test_invalid_content_settles_verified_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settled = _install(monkeypatch)
    original = _Connection.getresponse

    def changed(self: _Connection) -> _Response:
        response = original(self)
        if self.calls[-1][1] == "/chat/completions":
            document = json.loads(response.read(64 * 1024 + 1))
            document["choices"] = []
            return _Response(document)
        return response

    monkeypatch.setattr(_Connection, "getresponse", changed)
    with pytest.raises(
        deepseek.SeatUnavailable, match="^provider_response_invalid$"
    ) as raised:
        deepseek.turn("seat", run_id="run-1", role="receiver")
    assert raised.value.settled_usage == (12, 2, 4)
    assert settled == [
        {
            "run_id": "run-1",
            "role": "receiver",
            "cache_hit": 2,
            "cache_miss": 10,
            "output": 4,
            "served_model": "deepseek-v4.1-flash-1017",
            "started_at": _NOW,
            "finished_at": _NOW,
        }
    ]


def test_budget_refusal_sends_no_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)

    def refuse(*_: object, **__: object) -> None:
        raise spend.SpendRefused("fixture")

    monkeypatch.setattr(spend, "reserve", refuse)
    with pytest.raises(deepseek.SeatUnavailable, match="^budget_guard$"):
        deepseek.turn("seat", run_id="run-1", role="receiver")
    assert [path for _, path, _ in _Connection.calls] == ["/user/balance"]


def test_transport_failure_keeps_exception_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)

    def fail(self: _Connection) -> _Response:
        raise TimeoutError(_PRIVATE)

    monkeypatch.setattr(_Connection, "getresponse", fail)
    with pytest.raises(deepseek.SeatUnavailable, match="^provider_timeout$") as raised:
        deepseek.preflight()
    assert _PRIVATE not in str(raised.value)


def test_http_exception_has_fixed_category(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)

    def fail(self: _Connection) -> _Response:
        raise HTTPException(_PRIVATE)

    monkeypatch.setattr(_Connection, "getresponse", fail)
    with pytest.raises(
        deepseek.SeatUnavailable, match="^provider_unavailable$"
    ) as raised:
        deepseek.preflight()
    assert _PRIVATE not in str(raised.value)


def test_invalid_balance_text_is_rejected() -> None:
    with pytest.raises(deepseek.SeatUnavailable, match="^balance_unverified$"):
        deepseek._decimal("not-a-balance")


def test_default_connection_wraps_before_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    raw_sent = bytearray()

    class _Raw:
        def setsockopt(self, *_: object) -> None:
            return None

        def sendall(self, data: bytes) -> None:
            raw_sent.extend(data)

        def close(self) -> None:
            return None

    class _Wrapped:
        def sendall(self, data: bytes) -> None:
            assert events == ["connect", "wrap"]
            assert b"Authorization: Bearer fixture-private-key" in data
            events.append("send")

        def makefile(self, *_: object, **__: object) -> io.BytesIO:
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

        def close(self) -> None:
            return None

    raw = _Raw()
    wrapped = _Wrapped()

    def create_connection(
        address: tuple[str, int], timeout: object = None, source_address: object = None
    ) -> _Raw:
        assert address == ("api.deepseek.com", 443)
        events.append("connect")
        return raw

    def wrap_socket(
        self: ssl.SSLContext, sock: object, server_hostname: str | None = None
    ) -> _Wrapped:
        assert sock is raw
        assert server_hostname == "api.deepseek.com"
        assert self.verify_mode == ssl.CERT_REQUIRED
        assert self.check_hostname is True
        events.append("wrap")
        return wrapped

    original_context = ssl.create_default_context

    def create_context(
        *args: object, cafile: str | None = None, **kwargs: object
    ) -> ssl.SSLContext:
        assert cafile == certifi.where()
        return original_context(*args, cafile=cafile, **kwargs)

    deepseek._KEY = _PRIVATE
    deepseek._BOUND = True
    monkeypatch.setattr(ssl, "create_default_context", create_context)
    monkeypatch.setattr("http.client.socket.create_connection", create_connection)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)
    assert deepseek.HTTPSConnection is HTTPSConnection
    with pytest.raises(deepseek.SeatUnavailable, match="^balance_unverified$"):
        deepseek.preflight()
    assert events == ["connect", "wrap", "send"]
    assert raw_sent == b""
