import json
from typing import Any

import pytest

from examples.seatlab.receiver_memory import revised


def _frame(method: str, path: str, body: Any, reply: Any) -> dict[str, Any]:
    return {"method": method, "path": path, "body": body, "status": 200, "reply": reply}


def _inbox(*reports: tuple[str, str, str]) -> dict[str, Any]:
    return _frame(
        "GET",
        "/inbox",
        None,
        {"reports": [{"phase": p, "slot": s, "receipt": r} for p, s, r in reports]},
    )


def _ack(phase: str, receipt: str) -> dict[str, Any]:
    return _frame("POST", "/ack", {"phase": phase, "receipt": receipt}, {"ok": True})


def test_native_cases_preserve_only_booker_input_and_use_real_refresh(monkeypatch):
    native = revised.rt.native()
    runs: list[list[tuple[str, str]]] = []
    rewards: list[int] = []
    identifiers: set[str] = set()
    base = native.World
    native_grade = native.grade

    class ObservedWorld(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls: list[tuple[str, str]] = []
            runs.append(self.calls)
            identifiers.update((self.run, *self.tokens.values()))

        def handle(self, method, path, authorization, body):
            self.calls.append((method, path))
            status, reply = super().handle(method, path, authorization, body)
            if path == "/inbox":
                identifiers.update(r["receipt"] for r in reply["reports"])
            return status, reply

    def grade(*args):
        result = native_grade(*args)
        rewards.append(result.reward)
        return result

    native.World = ObservedWorld
    native.grade = grade
    monkeypatch.setattr(revised.rt, "native", lambda: native)
    confirm, replace = revised.cases(7, "truthful")
    assert len(runs) == len(rewards) == 12
    assert sum(rewards) == 4
    prefix = [
        ("GET", "/brief"),
        ("POST", "/report"),
        ("GET", "/inbox"),
        ("POST", "/ack"),
        ("POST", "/draft"),
        ("GET", "/brief"),
        ("POST", "/report"),
        ("GET", "/inbox"),
        ("POST", "/ack"),
    ]
    assert runs.count(prefix + [("POST", "/commit")]) == 6
    assert runs.count(prefix + [("GET", "/inbox"), ("POST", "/commit")]) == 6
    assert (confirm.name, replace.name) == ("confirm", "replace")
    assert confirm.mass == replace.mass == 1
    assert confirm.instruction == replace.instruction
    assert confirm.history[-1] == replace.history[-1] == _ack("revision", "receipt-2")
    assert confirm.history[:3] == replace.history[:3]
    for episode in (confirm, replace):
        assert [(f["method"], f["path"]) for f in episode.history] == [
            ("GET", "/inbox"),
            ("POST", "/ack"),
            ("POST", "/draft"),
            ("GET", "/inbox"),
            ("POST", "/ack"),
        ]
        frames = (*episode.history, episode.refresh)
        assert all(
            set(frame) == revised.FRAME_FIELDS and frame["status"] == 200
            for frame in frames
        )
        assert episode.refresh == episode.history[-2]
        reports = episode.refresh["reply"]["reports"]
        assert reports[0] == episode.history[0]["reply"]["reports"][0]
        assert episode.history[1] == _ack("initial", reports[0]["receipt"])
        assert episode.history[-1] == _ack("revision", reports[1]["receipt"])
        assert set(episode.scores) == {"A", "B", "C"}
        assert episode.scores[reports[1]["slot"]] == 1
        assert sum(episode.scores.values()) == 1
        text = json.dumps(frames)
        assert all(secret not in text for secret in identifiers)
    confirm_reports = confirm.refresh["reply"]["reports"]
    replace_reports = replace.refresh["reply"]["reports"]
    assert confirm_reports[0]["slot"] == confirm_reports[1]["slot"]
    assert replace_reports[0]["slot"] != replace_reports[1]["slot"]
    assert confirm.scores != replace.scores


def test_repeat_sender_has_indistinguishable_input_but_different_native_scores():
    confirm, replace = revised.cases(7, "repeat")
    assert confirm.history == replace.history
    assert confirm.refresh == replace.refresh
    reports = confirm.refresh["reply"]["reports"]
    initial = reports[0]["slot"]
    assert reports[1]["slot"] == initial
    assert confirm.scores[initial] == 1
    assert replace.scores[initial] == 0
    assert sum(replace.scores.values()) == 1


def test_receipt_aliases_preserve_ack_and_repeated_inbox_equality():
    first = ("initial", "A", "initial-raw")
    second = ("revision", "B", "revision-raw")
    raw = (
        _inbox(first),
        _ack(first[0], first[2]),
        _inbox(first, second),
        _ack(second[0], second[2]),
        _inbox(first, second),
    )
    normalized = revised._normalize(raw, frozenset())
    a = ("initial", "A", "receipt-1")
    b = ("revision", "B", "receipt-2")
    assert normalized == (
        _inbox(a),
        _ack(a[0], a[2]),
        _inbox(a, b),
        _ack(b[0], b[2]),
        _inbox(a, b),
    )
    assert raw[1]["body"]["receipt"] == "initial-raw"


@pytest.mark.parametrize(
    ("frames", "hidden", "message"),
    [
        (
            (_inbox(("initial", "A", "raw-one")), _inbox(("initial", "A", "raw-two"))),
            frozenset(),
            "receipt changed",
        ),
        (
            (_inbox(("initial", "A", "raw-one"), ("revision", "B", "raw-one")),),
            frozenset(),
            "receipt reused",
        ),
        (
            (_ack("initial", "raw-one"), _inbox(("initial", "A", "raw-one"))),
            frozenset(),
            "unmatched ack",
        ),
        (
            (_inbox(("initial", "A", "raw-one")), _ack("initial", "raw-two")),
            frozenset(),
            "unmatched ack",
        ),
        (
            (
                _inbox(("initial", "A", "raw-one")),
                _frame("POST", "/draft", {"slot": "A"}, {"echo": "raw-one"}),
            ),
            frozenset(),
            "raw receipt outside",
        ),
        (
            (
                _inbox(("initial", "A", "raw-one")),
                _frame("POST", "/draft", {"slot": "A"}, {"echo": "receipt-1"}),
            ),
            frozenset(),
            "alias collision",
        ),
        (
            (_inbox(("initial", "A", "receipt-1")),),
            frozenset(),
            "alias collision",
        ),
        (
            (
                _inbox(("initial", "A", "raw-one")),
                _frame("POST", "/draft", {"slot": "A"}, {"echo": "secret-id"}),
            ),
            frozenset({"secret-id"}),
            "hidden identifier",
        ),
        (
            (_frame("GET", "/brief", None, {"phase": "revision", "slot": "A"}),),
            frozenset(),
            "unexpected reader route",
        ),
    ],
)
def test_normalization_rejects_invalid_receipt_and_visibility(frames, hidden, message):
    with pytest.raises(ValueError, match=message):
        revised._normalize(frames, hidden)
