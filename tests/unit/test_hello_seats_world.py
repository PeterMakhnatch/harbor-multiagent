"""The hello-seats world sets a note's author from the seat token the request
sends, so one seat cannot post as another."""

import http.client
import importlib.util
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "tasks"
    / "hello-seats"
    / "environment"
    / "world.py"
)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[ModuleType, int]]:
    """The world module, with tokens issued to p1 and p2, served on a free
    local port."""
    spec = importlib.util.spec_from_file_location("hello_seats_world", WORLD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {WORLD_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TOKENS_DIR = tmp_path / "tokens"
    module.NOTES_PATH = tmp_path / "notes.jsonl"
    for seat in ("p1", "p2"):
        (module.TOKENS_DIR / seat).mkdir(parents=True)
    module.issue_tokens()
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.World)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield module, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def post_note(port: int, body: dict, token: str | None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/note", body=json.dumps(body), headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_each_seat_gets_a_new_token_in_its_own_directory(world):
    module, _ = world
    tokens = {
        seat: (module.TOKENS_DIR / seat / "token").read_text() for seat in module.TOKENS
    }
    assert tokens == module.TOKENS
    assert sorted(tokens) == ["p1", "p2"]
    assert tokens["p1"] != tokens["p2"]
    assert all(len(token) == 32 for token in tokens.values())


def test_the_author_is_the_seat_whose_token_was_sent(world):
    module, port = world
    status, note = post_note(port, {"text": "hello from p2"}, module.TOKENS["p2"])
    assert status == 201
    assert note["author"] == "p2"
    assert note["reply_to"] is None
    assert json.loads(module.NOTES_PATH.read_text()) == note


@pytest.mark.parametrize("token", [None, "", "not-a-seat-token"])
def test_a_post_without_a_seat_token_is_refused(world, token):
    module, port = world
    status, _ = post_note(port, {"author": "p1", "text": "hello from p1"}, token)
    assert status == 401
    assert module.NOTES == []


@pytest.mark.parametrize("length", ["-1", str(10**12), "many"])
def test_a_body_length_out_of_bounds_is_refused_unread(world, length):
    module, port = world
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("POST", "/note")
        connection.putheader("Authorization", f"Bearer {module.TOKENS['p1']}")
        connection.putheader("Content-Length", length)
        connection.endheaders()
        assert connection.getresponse().status == 400
    finally:
        connection.close()
    assert module.NOTES == []


def test_a_seat_cannot_post_as_another_seat(world):
    module, port = world
    body = {"author": "p1", "text": "hello from p1"}
    status, _ = post_note(port, body, module.TOKENS["p2"])
    assert status == 403
    assert module.NOTES == []


def test_a_reply_must_name_an_existing_note(world):
    module, port = world
    p1, p2 = module.TOKENS["p1"], module.TOKENS["p2"]
    reply = {"text": "hello from p1", "reply_to": "no-such-note"}
    assert post_note(port, reply, p1)[0] == 400
    _, p2_note = post_note(port, {"text": "hello from p2"}, p2)
    status, p1_note = post_note(port, reply | {"reply_to": p2_note["id"]}, p1)
    assert status == 201
    assert p1_note["reply_to"] == p2_note["id"]
    assert module.NOTES == [p2_note, p1_note]
