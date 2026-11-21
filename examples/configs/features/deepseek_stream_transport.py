from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import math
import os
import re
import secrets
import select
import signal
import socket
import socketserver
import ssl
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, BinaryIO, override
from urllib.parse import urlsplit

import certifi
import httpx

_MODEL = "deepseek-flash"
_HOST = "api.deepseek.com"
_PATH = "/chat/completions"
_UPSTREAM_URL = f"https://{_HOST}{_PATH}"
_BALANCE_PATH = "/user/balance"
_BALANCE_URL = f"https://{_HOST}{_BALANCE_PATH}"
_MIN_BALANCE_USD = 1.50
_MAX_BALANCE_BYTES = 4096
_BALANCE_TIMEOUT = 20.0
_CALL_IDS = frozenset(("left-report", "left-audit", "right-report", "builder-choice"))
_MAX_REQUEST_BYTES = 65_536
_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_FRAME_BYTES = 131_072
_MAX_LINES = 65_536
_MAX_ERROR_BYTES = 32_768
_SOCKET_LIMIT = 8
_HEADER_TIMEOUT = 5.0
_CLIENT_TIMEOUT = 100.0
_WRITE_TIMEOUT = 1.0
_TIMER_SECONDS = 0.05
_HANDLED = (OSError, ValueError, TypeError, RecursionError, httpx.HTTPError)


class _TransportError(ValueError):
    def __init__(self, kind: str, **fields: Any):
        super().__init__(kind)
        self.details = {"kind": kind, **fields}


def _json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()


def _parse_json(body: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON constant: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    return json.loads(
        body.decode("utf-8"), parse_constant=reject_constant, parse_float=finite_float
    )


def _write_json(path: Path, value: Any) -> None:
    with path.open("xb") as file:
        file.write(_json(value) + b"\n")


def _timestamp() -> tuple[int, int]:
    return time.monotonic_ns(), time.time_ns()


def _error(exc: Exception, credential: str = "") -> dict[str, Any]:
    if isinstance(exc, _TransportError):
        details = exc.details
    else:
        details = {"kind": type(exc).__name__, "message": str(exc)[:_MAX_ERROR_BYTES]}
    if credential and credential in _json(details).decode("ascii"):
        return {"kind": "provider_response_contains_credential"}
    return details


class _Events:
    def __init__(self, fd: int):
        self.fd = fd

    def write(
        self, event: str, *, stamp: tuple[int, int] | None = None, **fields: Any
    ) -> None:
        monotonic_ns, wall_time_ns = _timestamp() if stamp is None else stamp
        line = (
            _json(
                {
                    "event": event,
                    "monotonic_ns": monotonic_ns,
                    "wall_time_ns": wall_time_ns,
                    **fields,
                }
            )
            + b"\n"
        )
        if os.write(self.fd, line) != len(line):
            raise OSError("incomplete event write")


def _headers(token: str, accept: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": accept,
        "Accept-Encoding": "identity",
    }


def _media_type(response: httpx.Response) -> str:
    return response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()


def _shutdown_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        return


async def _read_body(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_raw():
        body.extend(chunk[: limit - len(body)])
        if len(body) == limit:
            break
    return bytes(body)


async def _sse_lines(response: httpx.Response) -> AsyncIterator[bytes]:
    tail = bytearray()
    total_bytes = 0
    async for chunk in response.aiter_raw():
        total_bytes += len(chunk)
        if total_bytes > _MAX_BODY_BYTES:
            raise _TransportError("body_byte_limit")
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start) + 1
            stop = end or len(chunk)
            if len(tail) + stop - start > _MAX_FRAME_BYTES:
                raise _TransportError("sse_frame_byte_limit")
            if end and not tail:
                yield chunk[start:end]
            else:
                tail.extend(chunk[start:stop])
                if end:
                    yield bytes(tail)
                    tail.clear()
            start = stop
    if tail:
        yield bytes(tail)


async def _frames(
    response: httpx.Response, *, raw: BinaryIO | None = None, credential: bytes = b""
) -> AsyncIterator[tuple[bytes, bytes | None]]:
    lines: list[bytes] = []
    data: list[bytes] = []
    frame_bytes = 0
    line_count = 0
    async for line in _sse_lines(response):
        line_count += 1
        if line_count > _MAX_LINES:
            raise _TransportError("sse_line_limit")
        if credential and credential in line:
            raise _TransportError("provider_response_contains_credential")
        if raw is not None:
            raw.write(line)
        frame_bytes += len(line)
        if frame_bytes > _MAX_FRAME_BYTES:
            raise _TransportError("sse_frame_byte_limit")
        lines.append(line)
        field_line = line.removesuffix(b"\n").removesuffix(b"\r")
        if not field_line:
            yield b"".join(lines), b"\n".join(data) if data else None
            lines.clear()
            data.clear()
            frame_bytes = 0
        else:
            field, separator, value = field_line.partition(b":")
            if field == b"data":
                data.append(value.removeprefix(b" ") if separator else b"")
    if lines:
        raise _TransportError("incomplete_sse_frame")


def _initial_record(call_id: str, *, upstream: bool) -> dict[str, Any]:
    record: dict[str, Any] = dict.fromkeys(
        (
            "response_id",
            "response_model",
            "response_created",
            "started_ns",
            "started_wall_time_ns",
            "first_delta_ns",
            "terminal_ns",
            "usage_received_ns",
            "completed_ns",
            "usage",
            "content",
            "reasoning_content",
            "finish_reason",
            "error",
        )
    )
    record.update(call_id=call_id, request_model=_MODEL, stream_done=False)
    if upstream:
        record.update(
            downstream_disconnected_ns=None,
            upstream_url=_UPSTREAM_URL,
            http_status=None,
            balance=None,
            stop_new_admission=False,
            admission_stop_reason=None,
        )
    return record


class _Stream:
    def __init__(self, call_id: str, events: _Events, *, upstream: bool = False):
        self.events = events
        self.lock = threading.Lock()
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.record = _initial_record(call_id, upstream=upstream)

    def start(self) -> None:
        stamp = _timestamp()
        self.record["started_ns"], self.record["started_wall_time_ns"] = stamp
        self.events.write("started", stamp=stamp)

    def is_live(self) -> bool:
        with self.lock:
            return (
                self.record["first_delta_ns"] is not None
                and self.record["terminal_ns"] is None
            )

    def note(
        self,
        key: str,
        event: str,
        hook: Callable[[], None] | None = None,
        **fields: Any,
    ) -> bool:
        with self.lock:
            if self.record[key] is not None:
                return False
            stamp = _timestamp()
            self.record[key] = stamp[0]
        if hook is not None:
            hook()
        self.events.write(event, stamp=stamp, **fields)
        return True

    def _metadata(self, chunk: dict[str, Any]) -> None:
        for field, expected in (("id", str), ("model", str), ("created", int)):
            value = chunk.get(field)
            if value is None:
                continue
            if type(value) is not expected or (field == "created" and value < 0):
                raise _TransportError("invalid_provider_metadata", field=field)
            key = f"response_{field}"
            previous = self.record[key]
            if previous is not None and previous != value:
                raise _TransportError("inconsistent_provider_metadata", field=field)
            self.record[key] = value

    def consume(
        self,
        data: bytes,
        *,
        on_first: Callable[[], None] | None = None,
        on_terminal: Callable[[], None] | None = None,
    ) -> None:
        if data == b"[DONE]":
            self.note("terminal_ns", "terminal_delta", on_terminal, reason="done")
            self.record["stream_done"] = True
            if self.record["finish_reason"] is None:
                raise _TransportError("provider_stream_missing_finish_reason")
            return
        chunk = _parse_json(data)
        if not isinstance(chunk, dict):
            raise _TransportError("invalid_provider_chunk")
        if chunk.get("error") is not None:
            self.note(
                "terminal_ns", "terminal_delta", on_terminal, reason="provider_error"
            )
            raise _TransportError(
                "provider_stream_error", provider_error=chunk["error"]
            )
        self._metadata(chunk)
        usage = chunk.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise _TransportError("invalid_provider_usage")
        content, reasoning, finish = _choice_text(chunk.get("choices", []))
        if finish is not None or usage is not None:
            self.note(
                "terminal_ns",
                "terminal_delta",
                on_terminal,
                reason="finish_reason" if finish is not None else "usage",
            )
        if finish is not None:
            previous = self.record["finish_reason"]
            if previous is not None and previous != finish:
                raise _TransportError("inconsistent_finish_reason")
            self.record["finish_reason"] = finish
        if usage is not None:
            previous = self.record["usage"]
            if previous is not None and previous != usage:
                raise _TransportError("inconsistent_provider_usage")
            self.record["usage"] = usage
            self.note("usage_received_ns", "usage_received")
        if content is not None:
            self.content.append(content)
        if reasoning is not None:
            self.reasoning.append(reasoning)
        meaningful = (content and content.strip()) or (reasoning and reasoning.strip())
        if meaningful and self.record["terminal_ns"] is None:
            if (
                self.note("first_delta_ns", "first_model_delta")
                and on_first is not None
            ):
                on_first()

    def complete(self, error: dict[str, Any] | None) -> None:
        self.record["content"] = "".join(self.content) if self.content else None
        self.record["reasoning_content"] = (
            "".join(self.reasoning) if self.reasoning else None
        )
        self.record["error"] = error
        if error is not None:
            self.note("terminal_ns", "terminal_delta", reason="error")
            self.note("completed_ns", "response_error", error=error)
        else:
            self.note("completed_ns", "response_complete")


def _choice_text(choices: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(choices, list) or len(choices) > 1:
        raise _TransportError("invalid_provider_choices")
    if not choices:
        return None, None, None
    choice = choices[0]
    if (
        not isinstance(choice, dict)
        or type(choice.get("index")) is not int
        or choice["index"] != 0
    ):
        raise _TransportError("invalid_provider_choice")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        raise _TransportError("invalid_provider_delta")
    content = delta.get("content")
    reasoning = delta.get("reasoning_content")
    finish = choice.get("finish_reason")
    if any(
        value is not None and not isinstance(value, str)
        for value in (content, reasoning, finish)
    ):
        raise _TransportError("invalid_provider_delta_text")
    return content, reasoning, finish


def _validate_request(
    body: Any, max_prompt_bytes: int, max_output_tokens: int
) -> tuple[str, dict[str, Any]]:
    if not isinstance(body, dict) or set(body) != {"call_id", "payload"}:
        raise _TransportError("invalid_request")
    call_id, payload = body["call_id"], body["payload"]
    if (
        not isinstance(call_id, str)
        or call_id not in _CALL_IDS
        or not isinstance(payload, dict)
    ):
        raise _TransportError("invalid_call")
    fields = {
        "model",
        "messages",
        "temperature",
        "thinking",
        "max_tokens",
        "stream",
        "stream_options",
    }
    if call_id != "left-audit":
        fields.add("response_format")
    if (
        set(payload) != fields
        or payload["model"] != _MODEL
        or payload["temperature"] != 0.6
    ):
        raise _TransportError("invalid_payload")
    options = payload["stream_options"]
    if (
        payload["thinking"] != {"type": "disabled"}
        or payload["stream"] is not True
        or not isinstance(options, dict)
        or set(options) != {"include_usage"}
        or options["include_usage"] is not True
    ):
        raise _TransportError("invalid_stream_options")
    if call_id != "left-audit" and payload["response_format"] != {
        "type": "json_object"
    }:
        raise _TransportError("invalid_response_format")
    max_tokens = payload["max_tokens"]
    if type(max_tokens) is not int or not 0 < max_tokens <= max_output_tokens:
        raise _TransportError("output_token_limit")
    messages = payload["messages"]
    if not isinstance(messages, list) or not 0 < len(messages) <= 16:
        raise _TransportError("invalid_messages")
    prompt_bytes = 0
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ("system", "user", "assistant")
            or not isinstance(message["content"], str)
        ):
            raise _TransportError("invalid_message")
        prompt_bytes += len(message["content"].encode("utf-8"))
        if prompt_bytes > max_prompt_bytes:
            raise _TransportError("prompt_byte_limit")
    return call_id, payload


class _GatewayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = _SOCKET_LIMIT

    def __init__(self, gateway: DeepSeekGateway):
        self.gateway = gateway
        super().__init__(("127.0.0.1", 0), _GatewayHandler)
        self.timeout = 0.05
        self.socket.settimeout(self.timeout)

    def serve(self) -> None:
        while not self.gateway._stop_requested.is_set():
            self.handle_request()
            self.gateway._expire_pending_connections(time.monotonic())

    @override
    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("gateway requires a TCP socket")
        request.settimeout(_HEADER_TIMEOUT)
        with self.gateway._condition:
            if (
                not self.gateway._accepting
                or len(self.gateway._clients) >= _SOCKET_LIMIT
            ):
                self.shutdown_request(request)
                return
            self.gateway._clients[request] = time.monotonic() + _HEADER_TIMEOUT
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._remove_client(request)
            raise

    @override
    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("gateway requires a TCP socket")
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._remove_client(request)

    def _remove_client(self, request: socket.socket) -> None:
        with self.gateway._condition:
            del self.gateway._clients[request]
            self.gateway._condition.notify_all()

    @override
    def handle_error(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        return


class _GatewayHandler(BaseHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _body(self) -> Any:
        values = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(values) != 1:
            raise _TransportError("invalid_request_length")
        length = values[0]
        if (
            not re.fullmatch(r"[0-9]{1,6}", length)
            or not 0 < int(length) <= _MAX_REQUEST_BYTES
        ):
            raise _TransportError("request_byte_limit")
        body = self.rfile.read(int(length))
        if len(body) != int(length):
            raise _TransportError("incomplete_request")
        return _parse_json(body)

    def _headers(self, status: int, media_type: str, length: int | None = None) -> None:
        self.connection.settimeout(_WRITE_TIMEOUT)
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Connection", "close")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _reject(self, status: int, error: dict[str, Any]) -> None:
        body = _json({"error": error})
        self._headers(status, "application/json", len(body))
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not isinstance(self.server, _GatewayServer):
            raise TypeError("missing gateway server")
        gateway = self.server.gateway
        auth = self.headers.get_all("Authorization", [])
        if self.path != _PATH:
            self._reject(404, {"kind": "invalid_gateway_path"})
            return
        if len(auth) != 1 or not hmac.compare_digest(
            auth[0].encode("latin-1"), gateway._bearer
        ):
            self._reject(401, {"kind": "invalid_gateway_token"})
            return
        try:
            body = self._body()
            call_id, payload = _validate_request(
                body, gateway._max_prompt_bytes, gateway._max_output_tokens
            )
            gateway._claim(call_id, self.connection)
        except _HANDLED as exc:
            self._reject(400, _error(exc))
            return
        gateway._run_call(self, call_id, payload)


class _Downstream:
    def __init__(self, handler: _GatewayHandler, stream: _Stream):
        self.handler = handler
        self.stream = stream
        self.headers_started = False

    def _disconnect(self, reason: str) -> None:
        self.stream.note(
            "downstream_disconnected_ns", "downstream_disconnected", reason=reason
        )

    def disconnected(self) -> bool:
        return self.stream.record["downstream_disconnected_ns"] is not None

    def poll(self) -> None:
        if self.disconnected():
            return
        connection = self.handler.connection
        try:
            readable, _, _ = select.select((connection,), (), (), 0)
            if readable:
                reason = (
                    "unexpected_data" if connection.recv(1, socket.MSG_PEEK) else "eof"
                )
                self._disconnect(reason)
        except OSError as exc:
            self._disconnect(type(exc).__name__)

    def check_connected(self) -> None:
        self.poll()
        if self.disconnected():
            raise _TransportError("downstream_disconnected_before_request")

    def start(self) -> None:
        self.headers_started = True
        if not self.disconnected():
            try:
                self.handler._headers(200, "text/event-stream")
            except OSError as exc:
                self._disconnect(type(exc).__name__)

    def frame(self, frame: bytes) -> None:
        if self.disconnected():
            return
        try:
            self.handler.connection.settimeout(_WRITE_TIMEOUT)
            self.handler.wfile.write(frame)
        except OSError as exc:
            self._disconnect(type(exc).__name__)

    def error(self, status: int, error: dict[str, Any]) -> None:
        if not self.headers_started and not self.disconnected():
            try:
                self.handler._reject(status, error)
            except OSError as exc:
                self._disconnect(type(exc).__name__)


def _balance_fields(body: bytes, balance: dict[str, Any]) -> None:
    if len(body) > _MAX_BALANCE_BYTES:
        raise _TransportError("provider_balance_byte_limit")
    value = _parse_json(body)
    if not isinstance(value, dict):
        raise _TransportError("invalid_provider_balance")
    balance["response"] = value
    available, entries = value.get("is_available"), value.get("balance_infos")
    if type(available) is not bool or not isinstance(entries, list):
        raise _TransportError("invalid_provider_balance")
    balance["is_available"] = available
    usd = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("currency") == "USD"
    ]
    if len(usd) != 1 or type(usd[0].get("total_balance")) not in (str, int, float):
        raise _TransportError("invalid_provider_usd_balance")
    total = float(str(usd[0]["total_balance"]))
    if not math.isfinite(total):
        raise _TransportError("invalid_provider_usd_balance")
    balance["usd"] = total
    if not available:
        raise _TransportError("provider_balance_unavailable")
    if total < _MIN_BALANCE_USD:
        raise _TransportError("provider_balance_below_floor")


def _is_out_of_usage(exc: Exception) -> bool:
    if not isinstance(exc, _TransportError):
        return False
    if exc.details["kind"] not in ("provider_stream_error", "provider_http_error"):
        return False
    text = _json(exc.details).decode("ascii").casefold()
    return any(
        phrase in text
        for phrase in (
            "insufficient balance",
            "insufficient_balance",
            "insufficient quota",
            "insufficient_quota",
            "out of usage",
            "out_of_usage",
        )
    )


class DeepSeekGateway:
    def __init__(
        self,
        output_dir: Path,
        api_key: str,
        *,
        max_calls: int = 4,
        max_prompt_bytes: int = 8192,
        max_output_tokens: int = 4096,
        api_timeout_sec: float = 90.0,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", api_key):
            raise ValueError("invalid DeepSeek API key format")
        for limit, maximum in (
            (max_calls, 4),
            (max_prompt_bytes, 8192),
            (max_output_tokens, 4096),
        ):
            if type(limit) is not int or not 0 < limit <= maximum:
                raise ValueError("gateway limits exceed study caps")
        if not math.isfinite(api_timeout_sec) or not 0 < api_timeout_sec <= 90:
            raise ValueError("invalid API deadline")
        self._output_dir = output_dir
        self._api_key = api_key
        self._max_calls = max_calls
        self._max_prompt_bytes = max_prompt_bytes
        self._max_output_tokens = max_output_tokens
        self._api_timeout_sec = api_timeout_sec
        self._token = secrets.token_urlsafe(32)
        self._bearer = f"Bearer {self._token}".encode("ascii")
        self._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl.load_verify_locations(cafile=certifi.where())
        self._condition = threading.Condition()
        self._cancelled = threading.Event()
        self._stop_requested = threading.Event()
        self._budget_gate = threading.Lock()
        self._admission_stop: str | None = None
        self._server: _GatewayServer | None = None
        self._listener: threading.Thread | None = None
        self._accepting = False
        self._clients: dict[socket.socket, float | None] = {}
        self._active: dict[asyncio.AbstractEventLoop, asyncio.Task[None]] = {}
        self._calls: dict[str, _Stream | None] = {}
        self._results: dict[str, dict[str, Any]] = {}

    @property
    def token(self) -> str:
        return self._token

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("gateway has not started")
        return f"http://127.0.0.1:{self._server.server_address[1]}{_PATH}"

    def start(self) -> None:
        if self._server is not None or self._cancelled.is_set():
            raise RuntimeError("cannot restart gateway")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        server = _GatewayServer(self)
        listener = threading.Thread(target=server.serve, daemon=True)
        self._server = server
        self._accepting = True
        try:
            listener.start()
        except BaseException:
            self._accepting = False
            server.server_close()
            raise
        self._listener = listener

    def _claim(self, call_id: str, connection: socket.socket) -> None:
        with self._condition:
            deadline = self._clients[connection]
            if (
                not self._accepting
                or deadline is None
                or deadline <= time.monotonic()
                or call_id in self._calls
                or len(self._calls) >= self._max_calls
            ):
                raise _TransportError("call_not_admitted")
            self._clients[connection] = None
            self._calls[call_id] = None

    def _expire_pending_connections(self, now: float) -> None:
        with self._condition:
            for sock, deadline in self._clients.items():
                if deadline is not None and deadline <= now:
                    _shutdown_socket(sock)

    def is_live(self, call_id: str) -> bool:
        with self._condition:
            stream = self._calls.get(call_id)
        return stream is not None and stream.is_live()

    def _stop_admission(self, stream: _Stream, reason: str) -> None:
        with self._condition:
            if self._admission_stop is None:
                self._admission_stop = reason
        if not stream.record["stop_new_admission"]:
            stream.record.update(stop_new_admission=True, admission_stop_reason=reason)
            stream.events.write("stop_new_admission", reason=reason)

    def _require_admission(self, stream: _Stream) -> None:
        with self._condition:
            reason = self._admission_stop
        if reason is not None:
            self._stop_admission(stream, reason)
            raise _TransportError("provider_admission_stopped", reason=reason)

    async def _balance_guard(
        self, client: httpx.AsyncClient, stream: _Stream, credential: bytes
    ) -> None:
        stamp = _timestamp()
        balance: dict[str, Any] = {
            "url": _BALANCE_URL,
            "http_status": None,
            "usd": None,
            "is_available": None,
            "minimum_usd": _MIN_BALANCE_USD,
            "started_ns": stamp[0],
            "completed_ns": None,
            "response": None,
        }
        stream.record["balance"] = balance
        stream.events.write("balance_started", stamp=stamp, url=_BALANCE_URL)
        try:
            async with (
                asyncio.timeout(_BALANCE_TIMEOUT),
                client.stream(
                    "GET",
                    _BALANCE_URL,
                    headers=_headers(self._api_key, "application/json"),
                ) as response,
            ):
                balance["http_status"] = response.status_code
                body = await _read_body(response, _MAX_BALANCE_BYTES + 1)
            if credential in body:
                raise _TransportError("provider_response_contains_credential")
            if balance["http_status"] != 200:
                raise _TransportError(
                    "provider_balance_http_error",
                    http_status=balance["http_status"],
                    body=body[:_MAX_BALANCE_BYTES].decode("utf-8", "replace"),
                    body_truncated=len(body) > _MAX_BALANCE_BYTES,
                )
            _balance_fields(body, balance)
        finally:
            stamp = _timestamp()
            balance["completed_ns"] = stamp[0]
            stream.events.write(
                "balance_checked",
                stamp=stamp,
                http_status=balance["http_status"],
                usd=balance["usd"],
                is_available=balance["is_available"],
            )

    async def _preflight(
        self, client: httpx.AsyncClient, stream: _Stream, credential: bytes
    ) -> None:
        locked = False
        try:
            while not locked:
                locked = self._budget_gate.acquire(blocking=False)
                if not locked:
                    await asyncio.sleep(0.025)
            self._require_admission(stream)
            try:
                await self._balance_guard(client, stream, credential)
            except _HANDLED as exc:
                kind = _error(exc, self._api_key)["kind"]
                if kind not in (
                    "provider_balance_unavailable",
                    "provider_balance_below_floor",
                ):
                    kind = "provider_balance_unverified"
                self._stop_admission(stream, kind)
                raise
        except asyncio.CancelledError:
            if not self._cancelled.is_set():
                self._stop_admission(stream, "provider_balance_unverified")
            raise
        finally:
            if locked:
                self._budget_gate.release()

    async def _forward(
        self,
        stream: _Stream,
        downstream: _Downstream,
        payload: dict[str, Any],
        raw: BinaryIO,
    ) -> None:
        credential = self._api_key.encode("ascii")
        loop = asyncio.get_running_loop()
        fd = downstream.handler.connection.fileno()

        def observe_disconnect() -> None:
            downstream.poll()
            if downstream.disconnected():
                loop.remove_reader(fd)

        loop.add_reader(fd, observe_disconnect)
        try:
            async with (
                asyncio.timeout(self._api_timeout_sec),
                httpx.AsyncClient(
                    verify=self._ssl,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self._api_timeout_sec,
                ) as client,
            ):
                self._require_admission(stream)
                downstream.check_connected()
                await self._preflight(client, stream, credential)
                self._require_admission(stream)
                downstream.check_connected()
                stream.start()
                async with client.stream(
                    "POST",
                    _UPSTREAM_URL,
                    content=_json(payload),
                    headers=_headers(self._api_key, "text/event-stream"),
                ) as response:
                    await self._forward_response(
                        response, stream, downstream, raw, credential
                    )
        finally:
            loop.remove_reader(fd)

    async def _forward_response(
        self,
        response: httpx.Response,
        stream: _Stream,
        downstream: _Downstream,
        raw: BinaryIO,
        credential: bytes,
    ) -> None:
        status = response.status_code
        stream.record["http_status"] = status
        stream.events.write(
            "provider_http_response", http_status=status, upstream_url=_UPSTREAM_URL
        )
        if status == 402:
            self._stop_admission(stream, "provider_out_of_usage")
        if status != 200 or _media_type(response) != "text/event-stream":
            stream.note("terminal_ns", "terminal_delta", reason="http_error")
            body = await _read_body(response, _MAX_ERROR_BYTES + 1)
            if credential in body:
                raise _TransportError("provider_response_contains_credential")
            raw.write(body[:_MAX_ERROR_BYTES])
            raise _TransportError(
                "provider_http_error"
                if status != 200
                else "invalid_provider_media_type",
                http_status=status,
                body=body[:_MAX_ERROR_BYTES].decode("utf-8", "replace"),
                body_truncated=len(body) > _MAX_ERROR_BYTES,
            )
        downstream.start()
        async for frame, data in _frames(response, raw=raw, credential=credential):
            downstream.poll()
            if data is not None:
                try:
                    stream.consume(data)
                except _HANDLED as exc:
                    if _is_out_of_usage(exc):
                        self._stop_admission(stream, "provider_out_of_usage")
                    stream.note("terminal_ns", "terminal_delta", reason="error")
                    downstream.frame(frame)
                    raise
            downstream.frame(frame)
            if stream.record["stream_done"]:
                return
        raise _TransportError("provider_stream_missing_done")

    def _observe_upstream(
        self,
        stream: _Stream,
        downstream: _Downstream,
        payload: dict[str, Any],
        raw: BinaryIO,
    ) -> None:
        loop = asyncio.new_event_loop()
        try:
            with self._condition:
                if self._cancelled.is_set():
                    raise _TransportError("gateway_closed")
                task = loop.create_task(self._forward(stream, downstream, payload, raw))
                self._active[loop] = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError as exc:
                raise _TransportError("gateway_closed") from exc
        finally:
            with self._condition:
                self._active.pop(loop, None)
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def _run_call(
        self, handler: _GatewayHandler, call_id: str, payload: dict[str, Any]
    ) -> None:
        directory = self._output_dir / call_id
        stream = None
        try:
            directory.mkdir()
            with (directory / "events.jsonl").open("xb", buffering=0) as events:
                stream = _Stream(call_id, _Events(events.fileno()), upstream=True)
                with self._condition:
                    self._calls[call_id] = stream
                downstream = _Downstream(handler, stream)
                error = None
                try:
                    _write_json(
                        directory / "request.json",
                        {"call_id": call_id, "payload": payload},
                    )
                    with (directory / "upstream.sse").open("xb") as raw:
                        self._observe_upstream(stream, downstream, payload, raw)
                except _HANDLED as exc:
                    if _is_out_of_usage(exc):
                        self._stop_admission(stream, "provider_out_of_usage")
                    error = _error(exc, self._api_key)
                    stream.note("terminal_ns", "terminal_delta", reason="error")
                    status = stream.record["http_status"]
                    downstream.error(
                        status if status is not None and 400 <= status <= 599 else 502,
                        error,
                    )
                stream.complete(error)
                _write_json(directory / "result.json", stream.record)
        except _HANDLED as exc:
            error = {
                "kind": "gateway_artifact_error",
                "cause": _error(exc, self._api_key),
            }
            if stream is None:
                result = _initial_record(call_id, upstream=True)
                result.update(
                    error=error,
                    terminal_ns=time.monotonic_ns(),
                    completed_ns=time.monotonic_ns(),
                )
                with self._condition:
                    self._results[call_id] = result
                handler._reject(500, error)
            else:
                with stream.lock:
                    stream.record["terminal_ns"] = (
                        stream.record["terminal_ns"] or time.monotonic_ns()
                    )
                    stream.record["completed_ns"] = (
                        stream.record["completed_ns"] or time.monotonic_ns()
                    )
                    stream.record["error"] = error
        finally:
            if stream is not None:
                with self._condition:
                    self._results[call_id] = stream.record

    def _stop(self, deadline: float) -> None:
        with self._condition:
            self._accepting = False
            self._stop_requested.set()
        self._expire_pending_connections(math.inf)
        if self._listener is not None and self._server is not None:
            self._listener.join(timeout=max(0.0, deadline - time.monotonic()))
            if self._listener.is_alive():
                raise TimeoutError("gateway listener did not stop")
            self._server.server_close()
            self._listener = None

    def finish(self, *, timeout_sec: float = 150.0) -> dict[str, dict[str, Any]]:
        if not math.isfinite(timeout_sec) or timeout_sec <= 0:
            raise ValueError("invalid drain deadline")
        deadline = time.monotonic() + timeout_sec
        self._stop(deadline)
        with self._condition:
            if not self._condition.wait_for(
                lambda: not self._clients, max(0.0, deadline - time.monotonic())
            ):
                raise TimeoutError("gateway did not drain before the deadline")
            return {call_id: dict(result) for call_id, result in self._results.items()}

    def close(self) -> None:
        try:
            self._stop(time.monotonic() + 0.5)
        finally:
            self._cancel_active()

    def _cancel_active(self) -> None:
        with self._condition:
            self._cancelled.set()
            sockets = tuple(self._clients)
            for loop, task in self._active.items():
                loop.call_soon_threadsafe(task.cancel)
        for sock in sockets:
            _shutdown_socket(sock)
        with self._condition:
            if not self._condition.wait_for(lambda: not self._clients, 6.0):
                raise TimeoutError("gateway connections did not close")
        self._api_key = ""


class _WorkerTimer:
    def __init__(self, events: _Events, enabled: bool):
        self.events = events
        self.enabled = enabled
        self.armed = False

    def fire(self, signum: int, frame: Any) -> None:
        if self.armed:
            try:
                self.events.write("timeout_fired")
            finally:
                os._exit(124)

    def arm(self) -> None:
        if not self.enabled:
            return
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        try:
            stamp = _timestamp()
            self.armed = True
            signal.setitimer(signal.ITIMER_REAL, _TIMER_SECONDS)
            self.events.write(
                "deadline_armed", stamp=stamp, timeout_after_delta_sec=_TIMER_SECONDS
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def stop(self) -> None:
        if self.armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            self.armed = False


def _worker_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        body = file.read(_MAX_REQUEST_BYTES + 1)
    if len(body) > _MAX_REQUEST_BYTES:
        raise _TransportError("worker_config_byte_limit")
    config = _parse_json(body)
    required = {
        "gateway_url",
        "gateway_token",
        "call_id",
        "payload",
        "result_path",
        "events_path",
        "mode",
        "timeout_after_delta_sec",
    }
    if not isinstance(config, dict) or set(config) != required:
        raise _TransportError("invalid_worker_config")
    if (
        config["mode"] not in ("normal", "timeout")
        or config["timeout_after_delta_sec"] != _TIMER_SECONDS
    ):
        raise _TransportError("invalid_worker_timeout")
    for key in ("gateway_url", "gateway_token", "result_path", "events_path"):
        if not isinstance(config[key], str) or not config[key]:
            raise _TransportError("invalid_worker_config_field", field=key)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", config["gateway_token"]):
        raise _TransportError("invalid_gateway_token")
    _validate_request(
        {"call_id": config["call_id"], "payload": config["payload"]}, 8192, 4096
    )
    url = urlsplit(config["gateway_url"])
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.netloc != f"127.0.0.1:{url.port}"
        or url.path != _PATH
        or url.query
        or url.fragment
    ):
        raise _TransportError("invalid_gateway_url")
    return config


async def _worker_stream(
    config: dict[str, Any], stream: _Stream, timer: _WorkerTimer
) -> None:
    body = _json({"call_id": config["call_id"], "payload": config["payload"]})
    async with (
        asyncio.timeout(_CLIENT_TIMEOUT),
        httpx.AsyncClient(
            verify=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            trust_env=False,
            follow_redirects=False,
            timeout=_CLIENT_TIMEOUT,
        ) as client,
    ):
        stream.start()
        async with client.stream(
            "POST",
            config["gateway_url"],
            content=body,
            headers=_headers(config["gateway_token"], "text/event-stream"),
        ) as response:
            if (
                response.status_code != 200
                or _media_type(response) != "text/event-stream"
            ):
                raw = await _read_body(response, _MAX_FRAME_BYTES)
                raise _TransportError(
                    "gateway_http_error",
                    http_status=response.status_code,
                    body=raw.decode("utf-8", "replace"),
                )
            async for _, data in _frames(response):
                if data is not None:
                    stream.consume(data, on_first=timer.arm, on_terminal=timer.stop)
                    if stream.record["stream_done"]:
                        return
    raise _TransportError("gateway_stream_missing_done")


def _worker(path: Path) -> int:
    config = _worker_config(path)
    with Path(config["events_path"]).open("xb", buffering=0) as events_file:
        events = _Events(events_file.fileno())
        stream = _Stream(config["call_id"], events)
        timer = _WorkerTimer(events, config["mode"] == "timeout")
        if timer.enabled:
            signal.signal(signal.SIGALRM, timer.fire)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
        error = None
        try:
            asyncio.run(_worker_stream(config, stream, timer))
        except _HANDLED as exc:
            error = _error(exc, config["gateway_token"])
        finally:
            timer.stop()
        if error is None and timer.enabled:
            events.write("missed_injection", reason="stream_finished_before_timeout")
        stream.complete(error)
        _write_json(Path(config["result_path"]), stream.record)
        return 0 if error is None else 1


def _main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        return _worker(args.config)
    except _HANDLED as exc:
        print(_json(_error(exc)).decode("ascii"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
