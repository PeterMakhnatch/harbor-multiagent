"""The hello-seats verifier pays p1 only for exactly one note, with the asked-for
text, in reply to a note by p2, as the saved record shows them."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

GRADE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "tasks"
    / "hello-seats"
    / "tests"
    / "grade.py"
)
P2_NOTE = {"id": "0a1b2c3d", "author": "p2", "text": "hello from p2", "reply_to": None}
P1_REPLY = {
    "id": "4e5f6a7b",
    "author": "p1",
    "text": "hello from p1",
    "reply_to": "0a1b2c3d",
}


@pytest.fixture
def grade(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # Loading grade.py must not write __pycache__ into the task's tests/,
    # which Harbor uploads to main.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("hello_seats_grade", GRADE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {GRADE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_reply_by_p1_to_a_note_by_p2_earns_one(grade):
    assert grade.reward([P2_NOTE, P1_REPLY]) == 1


@pytest.mark.parametrize(
    "second",
    [
        pytest.param(P1_REPLY | {"id": "8c9d0e1f"}, id="the same reply again"),
        pytest.param(
            P1_REPLY | {"id": "8c9d0e1f", "text": "hello again"}, id="another text"
        ),
        pytest.param(P1_REPLY | {"id": "8c9d0e1f", "reply_to": None}, id="not a reply"),
    ],
)
def test_a_second_authenticated_note_by_p1_earns_zero(grade, second):
    assert grade.reward([P2_NOTE, P1_REPLY, second]) == 0


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"reply_to": None}, id="no reply_to"),
        pytest.param({"reply_to": "ffffffff"}, id="reply to an unknown id"),
        pytest.param({"reply_to": "4e5f6a7b"}, id="reply to itself"),
        pytest.param({"reply_to": ["0a1b2c3d"]}, id="reply_to not a string"),
        pytest.param({"text": "Hello from p1"}, id="other text"),
        pytest.param({"text": "hello from p1\n"}, id="extra newline"),
    ],
)
def test_a_note_by_p1_that_does_not_qualify_earns_zero(grade, change):
    assert grade.reward([P2_NOTE, P1_REPLY | change]) == 0


def test_no_note_by_p2_leaves_nothing_to_reply_to(grade):
    assert grade.reward([P1_REPLY]) == 0


def test_a_note_without_an_id_is_no_reply_target(grade):
    p2_note = {key: value for key, value in P2_NOTE.items() if key != "id"}
    assert grade.reward([p2_note, P1_REPLY | {"reply_to": None}]) == 0


def test_p1s_note_by_another_author_earns_zero(grade):
    # p2 can send p1's exact note with its own token; the world then records
    # p2 as the author.
    assert grade.reward([P2_NOTE, P1_REPLY | {"author": "p2"}]) == 0


@pytest.mark.parametrize(
    ("saved", "reward"),
    [
        (json.dumps([P2_NOTE, P1_REPLY]), "1\n"),
        (None, "0\n"),
        ("", "0\n"),
        ("[P2_NOTE, P1_REPLY", "0\n"),
        (json.dumps({"notes": [P2_NOTE, P1_REPLY]}), "0\n"),
        (json.dumps([P2_NOTE, P1_REPLY, "a note"]), "0\n"),
    ],
    ids=["notes", "no file", "empty", "not JSON", "not a list", "not all notes"],
)
def test_main_grades_the_saved_notes_and_fails_closed(
    grade, tmp_path, monkeypatch, saved, reward
):
    notes_path = tmp_path / "notes.json"
    if saved is not None:
        notes_path.write_text(saved)
    monkeypatch.setattr(grade, "NOTES_PATH", notes_path)
    monkeypatch.setattr(grade, "REWARD_PATH", tmp_path / "reward.txt")

    grade.main()

    assert (tmp_path / "reward.txt").read_text() == reward
