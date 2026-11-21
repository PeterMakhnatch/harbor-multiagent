"""The hello-seats world sets a note's author from the seat token the request
sends, so one seat cannot post as another. Each start is a new session, with
new tokens and no notes, and `save_notes` writes the notes that the verifier
grades and Harbor keeps."""

import http.client
import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

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
SEATS = ("p1", "p2")


def start_world(tokens_dir: Path) -> ModuleType:
    """A new copy of the world module, as a new world process has, with
    TOKENS_DIR at tokens_dir and its session started."""
    spec = importlib.util.spec_from_file_location("hello_seats_world", WORLD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {WORLD_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    setattr(module, "TOKENS_DIR", tokens_dir)
    # Never the host's /logs: the issued-token list goes next to the tokens.
    setattr(
        module, "ISSUED_SECRETS", tokens_dir.parent / "artifacts" / ".harbor-secrets"
    )
    module.start_session()
    return module


@contextmanager
def serving(module: ModuleType) -> Iterator[int]:
    """Serve the world on a free local port, and yield the port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.World)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def tokens_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory per seat, as Compose mounts the token volumes in main."""
    # Loading world.py must not write __pycache__ into the example task.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    for seat in SEATS:
        (tmp_path / "tokens" / seat).mkdir(parents=True)
    return tmp_path / "tokens"


@pytest.fixture
def world(tokens_dir: Path) -> Iterator[tuple[ModuleType, int]]:
    """The world module, with tokens issued to p1 and p2, served on a free
    local port."""
    module = start_world(tokens_dir)
    with serving(module) as port:
        yield module, port


def post_note(
    port: int, body: dict[str, Any], token: str | None
) -> tuple[int, dict[str, Any]]:
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


def get_notes(port: int) -> list[dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/notes")
        return json.loads(connection.getresponse().read())
    finally:
        connection.close()


def token_files(tokens_dir: Path) -> dict[str, str]:
    return {seat: (tokens_dir / seat / "token").read_text() for seat in SEATS}


def test_each_seat_gets_a_new_token_in_its_own_directory(world, tokens_dir):
    module, _ = world
    tokens = token_files(tokens_dir)
    assert tokens == module.TOKENS
    assert tokens["p1"] != tokens["p2"]
    assert all(len(token) == 32 for token in tokens.values())


def test_a_restart_rotates_every_token_and_starts_with_no_notes(tokens_dir):
    first = start_world(tokens_dir)
    with serving(first) as port:
        status, _ = post_note(port, {"text": "hello from p2"}, first.TOKENS["p2"])
        assert status == 201

    # A new world process: its memory is new, the token volumes keep their files.
    second = start_world(tokens_dir)
    assert all(second.TOKENS[seat] != first.TOKENS[seat] for seat in SEATS)
    assert token_files(tokens_dir) == second.TOKENS
    with serving(second) as port:
        assert get_notes(port) == []
        assert post_note(port, {"text": "hello from p2"}, first.TOKENS["p2"])[0] == 401
        status, note = post_note(port, {"text": "hello from p2"}, second.TOKENS["p2"])
        assert (status, note["author"]) == (201, "p2")
    # The world writes the token files whole: no partial file is left over.
    files = sorted(path.relative_to(tokens_dir) for path in tokens_dir.rglob("*"))
    assert [path.as_posix() for path in files] == ["p1", "p1/token", "p2", "p2/token"]


def test_the_author_is_the_seat_whose_token_was_sent(world):
    module, port = world
    status, note = post_note(port, {"text": "hello from p2"}, module.TOKENS["p2"])
    assert status == 201
    assert note["author"] == "p2"
    assert note["reply_to"] is None
    assert get_notes(port) == [note]


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


def test_saved_notes_do_not_change_when_a_note_arrives_later(world, tmp_path):
    module, port = world
    _, p2_note = post_note(port, {"text": "hello from p2"}, module.TOKENS["p2"])
    reply = {"text": "hello from p1", "reply_to": p2_note["id"]}
    _, p1_note = post_note(port, reply, module.TOKENS["p1"])
    saved = tmp_path / "world" / "notes.json"

    module.save_notes(saved, f"http://127.0.0.1:{port}/notes")

    assert json.loads(saved.read_text()) == [p2_note, p1_note] == get_notes(port)
    assert [path.name for path in saved.parent.iterdir()] == ["notes.json"]
    before = saved.read_bytes()
    assert post_note(port, reply, module.TOKENS["p1"])[0] == 201
    assert len(get_notes(port)) == 3
    assert saved.read_bytes() == before


def test_the_world_lists_every_token_it_issues_for_harbor_to_scrub(tmp_path: Path):
    """Fork PR #2 review, P2: Harbor removes these tokens from saved output."""
    tokens_dir = tmp_path / "tokens"
    for seat in SEATS:
        (tokens_dir / seat).mkdir(parents=True)
    world = start_world(tokens_dir)
    first = {seat: (tokens_dir / seat / "token").read_text() for seat in SEATS}
    world.start_session()  # a restart issues new tokens; the old ones stay listed
    second = {seat: (tokens_dir / seat / "token").read_text() for seat in SEATS}
    listed = (tmp_path / "artifacts" / ".harbor-secrets").read_text().split()
    assert sorted(listed) == sorted([*first.values(), *second.values()])
