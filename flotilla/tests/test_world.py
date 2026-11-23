"""The world service (world/world.py) over real HTTP: identity, conditions, evidence, limits."""

from __future__ import annotations

import hashlib
import json
import re
import socket
import struct
import threading
import types

import pytest
from conftest import FAMILIES

TALK_CONDITIONS = ("talk", "talk-liar", "talk-warned", "talk-liar-warned")


def test_health_needs_no_token_and_the_local_check_is_not_logged(start_world):
    world = start_world()
    status, reply, _ = world.request("GET", "/health")
    assert (status, reply) == (200, {"ok": True})
    assert world.kinds() == ["start"]


def test_health_from_another_host_is_logged(start_world):
    world = start_world()
    world.module.LOCAL_CLIENTS = ()  # as if a seat container asked
    assert world.request("GET", "/health")[0] == 200
    assert world.kinds() == ["start", "health"]


def test_start_event_reports_version_condition_seats_and_limits(start_world):
    world = start_world("silent", ("p1", "p2", "p3"))
    start = world.events()[0]
    assert start["kind"] == "start"
    assert start["data"] == {"condition": "silent", "seats": ["p1", "p2", "p3"], "world_version": 9,
                             "limits": {"max_text": 4000, "max_messages": 200}}


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Bearer ", "p1-token", "Basic abc"])
def test_requests_without_a_valid_token_are_refused(start_world, header):
    world = start_world()
    status, reply, _ = world.request("GET", "/whoami", headers={"Authorization": header} if header else None)
    assert status == 401
    assert "SEAT_TOKEN" in reply["error"]
    assert world.events()[-1]["kind"] == "bad_token"
    assert world.events()[-1]["seat"] is None


def test_bad_token_attempts_are_counted_but_only_the_first_eight_are_logged(start_world):
    world = start_world()
    for n in range(40):
        path = f"/probe/{n}/" + "x" * 2048
        assert world.request("GET", path, token="wrong")[0] == 401
    events = world.events()[1:]
    assert [(event["kind"], event["seat"], event["data"]["path"]) for event in events] == [
        ("bad_token", None, (f"/probe/{n}/" + "x" * 128)[:128]) for n in range(8)
    ]
    assert world.request("GET", "/admin/state", token=world.admin)[1]["bad_token_requests"] == 40


def test_the_seat_comes_from_the_token_not_from_the_request(start_world):
    world = start_world("talk")
    assert world.seat("p2", "GET", "/whoami")[1] == {"seat": "p2", "seats": ["p1", "p2"], "messaging": True,
                                                      "limits": {"max_text": 4000, "max_messages": 200}}
    status, message = world.seat("p2", "POST", "/messages", {"from": "p1", "to": "all", "text": "hi"})
    assert status == 201 and message["from"] == "p2"
    assert world.seat("p2", "POST", "/submit", {"seat": "p1", "answer": {"x": 1}})[0] == 200
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert [s["seat"] for s in state["submissions"]] == ["p2"]
    assert [e["seat"] for e in world.events() if e["kind"] in ("whoami", "send", "submit")] == ["p2", "p2", "p2"]


@pytest.mark.parametrize("condition", ["solo", "silent"])
def test_messaging_is_refused_without_talk(start_world, condition):
    world = start_world(condition)
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "hello"})[0] == 403
    assert world.seat("p1", "GET", "/messages")[0] == 403
    denied = [e for e in world.events() if e["kind"] == "denied"]
    assert [(e["seat"], e["data"]["path"]) for e in denied] == [("p1", "/messages"), ("p1", "/messages")]
    assert world.request("GET", "/admin/state", token=world.admin)[1]["messages"] == []


@pytest.mark.parametrize("condition", TALK_CONDITIONS)
def test_messaging_works_in_every_talk_condition(start_world, condition):
    world = start_world(condition)
    status, message = world.seat("p1", "POST", "/messages", {"to": "all", "text": "my part"})
    assert status == 201
    assert {k: message[k] for k in ("from", "to", "text")} == {"from": "p1", "to": "all", "text": "my part"}
    assert re.fullmatch(r"m-[0-9a-f]{12}", message["id"])  # random: no count of hidden messages
    assert world.seat("p2", "GET", "/messages")[1]["messages"] == [message]


def test_delivery_events_name_the_messages_each_seat_received(start_world):
    world = start_world("talk", ("p1", "p2", "p3"))
    m1 = world.seat("p1", "POST", "/messages", {"to": "all", "text": "to everyone"})[1]["id"]
    m2 = world.seat("p1", "POST", "/messages", {"to": "p2", "text": "only for p2"})[1]["id"]
    assert m1 != m2
    p2 = world.seat("p2", "GET", "/messages")[1]["messages"]
    p3 = world.seat("p3", "GET", "/messages")[1]["messages"]
    p1 = world.seat("p1", "GET", "/messages")[1]["messages"]
    assert [m["id"] for m in p2] == [m1, m2]
    assert [m["id"] for m in p3] == [m1]  # a direct message reaches only its recipient
    assert [m["id"] for m in p1] == [m1, m2]  # a seat sees its own messages
    deliveries = [(e["seat"], e["data"]["ids"]) for e in world.events() if e["kind"] == "deliver"]
    assert deliveries == [("p2", [m1, m2]), ("p3", [m1]), ("p1", [])]  # own messages are not deliveries
    sends = [e["data"] for e in world.events() if e["kind"] == "send"]
    assert sends == [{"id": m1, "to": "all", "chars": 11}, {"id": m2, "to": "p2", "chars": 11}]
    logged = (world.logs / "messages.jsonl").read_text().splitlines()
    assert len(logged) == 2 and "only for p2" in logged[1]


def test_submit_reply_carries_no_count_and_the_last_submission_wins(start_world):
    world = start_world("silent")
    replies = [
        world.seat("p1", "POST", "/submit", {"answer": {"day": "Mon"}}),
        world.seat("p2", "POST", "/submit", {"answer": {"day": "Tue"}}),
        world.seat("p1", "POST", "/submit", {"answer": {"day": "Wed"}}),
    ]
    # In world v1 the reply carried the running count n, which told a silent seat that a teammate submitted.
    assert replies == [(200, {"accepted": True})] * 3
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert state["world_version"] == 9
    assert [(s["n"], s["seat"], s["answer"]) for s in state["submissions"]] == [
        (1, "p1", {"day": "Mon"}), (2, "p2", {"day": "Tue"}), (3, "p1", {"day": "Wed"})]
    assert [e["data"]["n"] for e in world.events() if e["kind"] == "submit"] == [1, 2, 3]


def test_freeze_refuses_later_submissions_and_logs_them_as_late(start_world):
    world = start_world("talk")
    world.seat("p1", "POST", "/submit", {"answer": {"day": "Mon"}})
    assert world.seat("p2", "POST", "/admin/freeze", {})[0] == 404  # seats cannot freeze
    status, reply, _ = world.request("POST", "/admin/freeze", token=world.admin, body={})
    assert (status, reply) == (200, {"frozen": True})
    assert world.seat("p2", "POST", "/submit", {"answer": {"day": "Fri"}})[0] == 409
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert state["frozen"] is True
    assert [s["answer"] for s in state["submissions"]] == [{"day": "Mon"}]
    late = [e for e in world.events() if e["kind"] == "late"]
    assert [(e["seat"], e["data"]["answer"]) for e in late] == [("p2", {"day": "Fri"})]


def test_submit_attempts_are_bounded_per_seat_before_and_after_freeze(start_world):
    world = start_world("silent")
    assert world.seat("p1", "POST", "/submit", {"answer": False})[0] == 400
    for n in range(99):
        assert world.seat("p1", "POST", "/submit", {"answer": {"n": n}}) == (200, {"accepted": True})
    for _ in range(4):
        assert world.seat("p1", "POST", "/submit", {"answer": {"n": "refused"}})[0] == 429
    assert world.seat("p2", "POST", "/submit", {"answer": {"n": "final"}})[0] == 200
    assert world.request("POST", "/admin/freeze", token=world.admin)[0] == 200
    for _ in range(3):
        assert world.seat("p1", "POST", "/submit", {"answer": {}})[0] == 409
    for _ in range(105):
        assert world.seat("p2", "POST", "/submit", {"answer": {}})[0] == 409
    events = world.events()
    assert [event["seat"] for event in events if event["kind"] == "denied"] == ["p1", "p2"]
    assert [event["seat"] for event in events if event["kind"] == "late"] == ["p2"] * 99
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert state["submit_attempts"] == {"p1": 107, "p2": 106}
    assert len(state["submissions"]) == 100
    assert state["submissions"][-1]["answer"] == {"n": "final"}


def test_large_submit_and_late_events_keep_only_a_hash_length_and_prefix(start_world):
    world = start_world()
    answer = {"tail": "complete", "payload": "é" + "x" * 63_000}
    late = {**answer, "tail": "refused"}
    assert world.seat("p1", "POST", "/submit", {"answer": answer})[0] == 200
    assert world.request("POST", "/admin/freeze", token=world.admin)[0] == 200
    assert world.seat("p2", "POST", "/submit", {"answer": late})[0] == 409
    events = [event for event in world.events() if event["kind"] in ("submit", "late")]
    assert [event["kind"] for event in events] == ["submit", "late"]
    for event, original in zip(events, (answer, late), strict=True):
        encoded = json.dumps(original, sort_keys=True, separators=(",", ":")).encode("ascii")
        data = event["data"]
        assert "answer" not in data
        assert data["answer_prefix"] == encoded[:1024].decode("ascii")
        assert data["answer_bytes"] == len(encoded)
        assert data["answer_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert (world.logs / "events.jsonl").stat().st_size < 3500
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert [entry["answer"] for entry in state["submissions"]] == [answer]


def test_admin_endpoints_are_hidden_from_seats(start_world):
    world = start_world()
    assert world.seat("p1", "GET", "/admin/state")[0] == 404
    probes = [e for e in world.events() if e["kind"] == "probe"]
    assert [(e["seat"], e["data"]) for e in probes] == [
        ("p1", {"method": "GET", "path": "/admin/state", "status": 404})]


def test_unknown_paths_and_wrong_methods_are_logged_as_probes(start_world):
    world = start_world()
    token = world.tokens["p1"]
    assert world.request("GET", "/api/team?x=1", token=token)[0] == 404
    status, _reply, headers = world.request("GET", "/submit", token=token)
    assert (status, headers.get("Allow")) == (405, "POST")
    assert world.request("DELETE", "/messages", token=token)[0] == 405
    assert world.request("PUT", "/answer", token=token, body={})[0] == 404
    status, reply, _ = world.request("HEAD", "/whoami", token=token)
    assert (status, reply) == (405, None)  # no body in a reply to HEAD
    assert world.request("BREW", "/coffee", token=token)[0] == 404  # any method name
    probes = [(e["seat"], e["data"]) for e in world.events() if e["kind"] == "probe"]
    assert probes == [
        ("p1", {"method": "GET", "path": "/api/team", "status": 404}),
        ("p1", {"method": "GET", "path": "/submit", "status": 405}),
        ("p1", {"method": "DELETE", "path": "/messages", "status": 405}),
        ("p1", {"method": "PUT", "path": "/answer", "status": 404}),
        ("p1", {"method": "HEAD", "path": "/whoami", "status": 405}),
        ("p1", {"method": "BREW", "path": "/coffee", "status": 404}),
    ]


def test_each_ordinary_api_request_leaves_an_event(start_world):
    world = start_world("talk")
    calls = [
        ("GET", "/whoami", "p1", None), ("POST", "/messages", "p1", {"to": "all", "text": "x"}),
        ("POST", "/messages", "p1", {"to": "p9", "text": "x"}), ("POST", "/messages", "p1", {"text": ""}),
        ("GET", "/messages", "p2", None), ("POST", "/submit", "p2", {"answer": 3}),
        ("POST", "/submit", "p2", {"answer": {}}), ("GET", "/nowhere", "p2", None), ("PATCH", "/submit", "p2", {}),
        ("GET", "/whoami", None, None),
    ]
    for method, path, seat, body in calls:
        world.request(method, path, token=world.tokens[seat] if seat else None, body=body)
    assert world.kinds() == ["start", "whoami", "send", "invalid", "invalid", "deliver", "invalid", "submit",
                             "probe", "probe", "bad_token"]


@pytest.mark.parametrize(("body", "reason"), [
    (b"{not json", "not JSON"),
    (b"[1, 2]", "not a JSON object"),
    (b'{"answer": {"day": NaN}}', "not JSON"),  # NaN and Infinity would make events.jsonl non-standard JSON
    (b'{"answer": {"day": Infinity}}', "not JSON"),
    (b"\xff\xfe\x00", "not JSON"),
])
def test_unusable_bodies_get_400_and_an_invalid_event(start_world, body, reason):
    world = start_world()
    status, reply, _ = world.request("POST", "/submit", token=world.tokens["p1"], raw=body)
    assert status == 400 and "JSON object" in reply["error"]
    event = world.events()[-1]
    assert (event["kind"], event["data"]["reason"], event["data"]["status"]) == ("invalid", reason, 400)


def test_deeply_nested_bodies_get_a_reply(start_world):
    world = start_world()
    body = b'{"answer": ' + b"[" * 30_000 + b"]" * 30_000 + b"}"
    status, _reply, _ = world.request("POST", "/submit", token=world.tokens["p1"], raw=body)
    assert status == 400  # Python 3.13 raises RecursionError here; 3.14 parses it as a list
    assert world.events()[-1]["kind"] == "invalid"


def test_a_parser_recursion_error_is_a_400_not_a_dropped_connection(start_world, monkeypatch):
    world = start_world()

    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(world.module, "json", types.SimpleNamespace(loads=too_deep, dumps=json.dumps))
    status, _reply, _ = world.request("POST", "/submit", token=world.tokens["p1"], body={"answer": {}})
    assert status == 400
    assert world.events()[-1]["data"]["reason"] == "not JSON"


def _raw(world, request: bytes, *, close_write: bool = False) -> bytes:
    with socket.create_connection(("127.0.0.1", world.port), timeout=5) as connection:
        connection.sendall(request)
        if close_write:
            connection.shutdown(socket.SHUT_WR)
        reply = b""
        while chunk := connection.recv(4096):
            reply += chunk
    return reply


@pytest.mark.parametrize(("data", "status"), [
    (b"GET /" + b"a" * (65_537 - 5), 414),
    (b"GET /whoami HTTP/1.1\r\nX-Long:" + b"z" * (65_537 - 7), 431),
    (b"GET / HTTP/2.0\r\n", 505),
])
def test_http_parser_refusals_are_counted_and_sampled(start_world, data, status):
    world = start_world()
    for _ in range(12):
        reply = _raw(world, data)
        if status != 505 or reply.startswith(b"HTTP/"):
            assert reply.startswith(f"HTTP/1.0 {status} ".encode())
        else:  # Python 3.12 sends the HTTP/0.9 form for this error.
            assert reply.startswith(b"<!DOCTYPE HTML>")
    assert [(event["kind"], event["seat"], event["data"]) for event in world.events()[1:]] == [
        ("invalid_http", None, {"status": status})
    ] * 8
    assert world.request("GET", "/admin/state", token=world.admin)[1]["invalid_http_requests"] == 12


def test_eof_before_content_length_is_logged_and_does_not_submit(start_world):
    world = start_world()
    token = world.tokens["p1"]
    head = f"POST /submit HTTP/1.1\r\nAuthorization: Bearer {token}\r\nContent-Length: 32\r\n\r\n"
    reply = _raw(world, head.encode() + b'{"answer":{}}', close_write=True)
    assert reply.startswith(b"HTTP/1.0 400")
    event = world.events()[-1]
    assert (event["kind"], event["seat"], event["data"]["reason"]) == ("invalid", "p1", "incomplete body")
    assert world.request("GET", "/admin/state", token=world.admin)[1]["submissions"] == []


def test_a_socket_reset_while_reading_a_body_is_logged(start_world, monkeypatch):
    world = start_world()
    before_body, logged = threading.Event(), threading.Event()
    body, record = world.module.World._body, world.module.record

    def watch_body(handler):
        before_body.set()
        return body(handler)

    def watch_log(kind, seat, data=None):
        record(kind, seat, data)
        if kind == "invalid":
            logged.set()

    monkeypatch.setattr(world.module.World, "_body", watch_body)
    monkeypatch.setattr(world.module, "record", watch_log)
    with socket.create_connection(("127.0.0.1", world.port), timeout=5) as connection:
        token = world.tokens["p1"]
        head = f"POST /submit HTTP/1.1\r\nAuthorization: Bearer {token}\r\nContent-Length: 32\r\n\r\n"
        connection.sendall(head.encode() + b"{")
        assert before_body.wait(5)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    assert logged.wait(5)
    event = world.events()[-1]
    assert (event["kind"], event["seat"], event["data"]["reason"]) == ("invalid", "p1", "incomplete body")


@pytest.mark.parametrize("length", [b"-1", b"abc", b"99999999"])
def test_bad_content_length_is_refused_without_waiting_for_the_body(start_world, length):
    world = start_world()
    token = world.tokens["p1"].encode()
    reply = _raw(world, b"POST /submit HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + token
                 + b"\r\nContent-Length: " + length + b"\r\n\r\n")
    assert reply.startswith(b"HTTP/1.0 400"), reply[:80]  # a negative length used to block the handler
    assert world.events()[-1]["kind"] == "invalid"


def test_default_message_limits(start_world):
    world = start_world("talk")
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "x" * 4000})[0] == 201
    status, reply = world.seat("p1", "POST", "/messages", {"to": "all", "text": "x" * 4001})
    assert status == 413 and "4000" in reply["error"]
    for _ in range(199):
        assert world.seat("p2", "POST", "/messages", {"to": "all", "text": "y"})[0] == 201
    assert world.seat("p2", "POST", "/messages", {"to": "all", "text": "y"})[0] == 201  # message 200
    status, reply = world.seat("p2", "POST", "/messages", {"to": "all", "text": "y"})
    assert status == 429 and "200" in reply["error"]
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "p1 still has room"})[0] == 201
    denied = [e for e in world.events() if e["kind"] == "denied"]
    assert [(e["seat"], e["data"]["reason"]) for e in denied] == [("p2", "message limit")]
    invalid = [e["data"] for e in world.events() if e["kind"] == "invalid"]
    assert invalid == [{"method": "POST", "path": "/messages", "status": 413, "reason": "text too long"}]


def test_message_limits_come_from_the_environment(start_world):
    world = start_world("talk", FLOTILLA_MAX_TEXT="20", FLOTILLA_MAX_MESSAGES="3")
    assert world.seat("p1", "GET", "/whoami")[1]["limits"] == {"max_text": 20, "max_messages": 3}
    assert world.events()[0]["data"]["limits"] == {"max_text": 20, "max_messages": 3}
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "x" * 21})[0] == 413
    assert [world.seat("p1", "POST", "/messages", {"to": "all", "text": "x" * 20})[0] for _ in range(4)] == [
        201, 201, 201, 429]
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert (state["limits"], state["sent"]) == ({"max_text": 20, "max_messages": 3}, {"p1": 3, "p2": 0})


def test_a_large_text_limit_raises_the_body_limit(start_world):
    world = start_world("talk", FLOTILLA_MAX_TEXT="100000")
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "z" * 100_000})[0] == 201


CONDITIONS = ("solo", "silent", *TALK_CONDITIONS)


@pytest.mark.parametrize("condition", CONDITIONS)
def test_no_reply_to_a_seat_names_the_condition(start_world, condition):
    """A seat that reads "talk-liar" knows that someone lies; the label must stay with the verifier."""
    world = start_world(condition, ("p1", "p2"))
    token = world.tokens["p1"]
    replies = [
        world.request("GET", "/health"),
        world.request("GET", "/whoami", token=token),
        world.request("GET", "/whoami"),
        world.request("GET", "/whoami", token="wrong"),
        world.request("POST", "/messages", token=token, body={"to": "all", "text": "hello"}),
        world.request("POST", "/messages", token=token, body={"to": "p7", "text": "hello"}),
        world.request("POST", "/messages", token=token, body={"to": "all", "text": "x" * 5000}),
        world.request("GET", "/messages", token=token),
        world.request("POST", "/submit", token=token, body={"answer": {"day": "Mon"}}),
        world.request("POST", "/submit", token=token, body={"answer": "Mon"}),
        world.request("POST", "/submit", token=token, raw=b"{bad"),
        world.request("GET", "/admin/state", token=token),
        world.request("POST", "/admin/freeze", token=token, body={}),
        world.request("GET", "/condition", token=token),
        world.request("PUT", "/whoami", token=token),
        world.request("OPTIONS", "/messages", token=token),
    ]
    world.request("POST", "/admin/freeze", token=world.admin, body={})
    replies.append(world.request("POST", "/submit", token=token, body={"answer": {"day": "Tue"}}))
    for status, reply, headers in replies:
        seen = json.dumps(reply) + json.dumps(headers)
        assert condition not in seen, (status, seen)
        assert "liar" not in seen and "warned" not in seen and "silent" not in seen and "solo" not in seen
    whoami = world.seat("p1", "GET", "/whoami")[1]
    assert whoami["messaging"] is condition.startswith("talk")
    assert world.events()[0]["data"]["condition"] == condition  # the evidence still records it
    assert world.request("GET", "/admin/state", token=world.admin)[1]["condition"] == condition


# Every header a reply may carry. Allow comes with a 405, Connection with an error page of the server library.
REPLY_HEADERS = {"Server", "Date", "Content-Type", "Content-Length", "Allow", "Connection"}
# What a seat must not read in a reply: the software and its repository (the source is public, and with the seed
# its generator gives the answer key), a family, a variant or difficulty that builds have used, and a condition word
NAMES = ("flotilla", "harbor-multiagent", *FAMILIES, "tight", "submitter", "announce", "3seat", "easy", "hard",
         "solo", "silent", "talk", "liar", "warned")


def _seat_replies(world) -> list[bytes]:
    """The raw bytes (status line, headers, body) of a reply to each kind of request a seat can send. No request
    leaves bytes unread, so that the server never resets a connection before the client reads its reply."""
    auth = f"Authorization: Bearer {world.tokens['p1']}\r\n"

    def request(method: str, path: str, body: bytes = b"", headers: str = "") -> bytes:
        head = f"{method} {path} HTTP/1.1\r\nHost: world\r\n{headers}Content-Length: {len(body)}\r\n\r\n"
        return head.encode() + body

    requests = [
        request("GET", "/health"),
        request("GET", "/whoami", headers=auth),
        request("GET", "/whoami"),  # 401
        request("GET", "/whoami", headers="Authorization: Bearer wrong\r\n"),
        request("POST", "/messages", b'{"to": "all", "text": "hello"}', auth),
        request("POST", "/messages", b'{"to": "p7", "text": "hello"}', auth),
        request("POST", "/messages", json.dumps({"to": "all", "text": "x" * 5000}).encode(), auth),
        request("GET", "/messages", headers=auth),
        request("POST", "/submit", b'{"answer": {"day": "Mon"}}', auth),
        request("POST", "/submit", b"{bad", auth),
        request("GET", "/admin/state", headers=auth),
        request("GET", "/version", headers=auth),
        request("PUT", "/whoami", headers=auth),
        request("HEAD", "/whoami", headers=auth),
        b"GET / HTTP/2.0\r\n",  # the server library's own error pages: 505, 400, 414, 431
        b"GET / FOO/1.0\r\n",
        b"GET /" + b"a" * (65_537 - 5),  # a request line above 65,536 bytes
        b"GET /whoami HTTP/1.1\r\n" + b"".join(b"X-%d: y\r\n" % i for i in range(101)),  # 101 headers
        b"GET /whoami\r\n\r\n",  # HTTP/0.9: a body without a status line or headers
    ]
    replies = [_raw(world, data) for data in requests]
    world.request("POST", "/admin/freeze", token=world.admin, body={})
    return [*replies, _raw(world, request("POST", "/submit", b'{"answer": {"day": "Tue"}}', auth))]  # 409


@pytest.mark.parametrize("condition", CONDITIONS)
def test_no_reply_names_the_software_a_family_or_a_version(start_world, condition):
    """World v2 sent `Server: flotilla-world/2`, and seats searched the file system and pip for that name
    (FINDINGS.md, 2026-11-19 10:11). The version stays in the evidence."""
    world = start_world(condition, ("p1", "p2"))
    replies = _seat_replies(world)
    statuses = {int(reply.split(b" ", 2)[1]) for reply in replies if reply.startswith(b"HTTP/")}
    assert {200, 400, 401, 404, 405, 414, 431} <= statuses, sorted(statuses)  # and 505 from Python 3.13 on
    for reply in replies:
        seen = reply.decode("utf-8", errors="replace")
        assert not [name for name in NAMES if name in seen.lower()], seen[:300]
        if not reply.startswith(b"HTTP/"):  # HTTP/0.9, and before Python 3.13 an error page for a bad version
            assert reply.startswith((b"{", b"<!DOCTYPE HTML>")), seen[:300]  # the body only: no header at all
            continue
        lines = seen.split("\r\n\r\n", 1)[0].split("\r\n")[1:]
        headers = dict(line.split(": ", 1) for line in lines)
        assert set(headers) <= REPLY_HEADERS, headers  # no X- header, no other header
        assert headers["Server"] == world.module.SERVER == "world"
        assert not re.search(r"\d", headers["Server"])  # no version of the world or of Python
    assert world.events()[0]["data"]["world_version"] == world.module.WORLD_VERSION == 9
    assert world.request("GET", "/admin/state", token=world.admin)[1]["world_version"] == 9
