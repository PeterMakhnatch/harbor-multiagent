import io
import json
import os
import shutil
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fractions import Fraction
from threading import Barrier

import pytest

from examples.seatlab.receiver_memory import provenance, revised
from examples.seatlab.receiver_memory.study import manifest, session


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    root = tmp_path_factory.mktemp("receiver-session").resolve() / "plan"
    receipt = manifest.seal(root, 0, "truthful", "external_unverified")
    expected = receipt["sha256"]
    plan = manifest.load(root, expected)
    assignment = next(
        row
        for row in plan["assignments"]
        if row["world"] == "replace" and row["arm"] == "sham"
    )
    episodes = revised.cases(assignment["seed"], plan["sender"])
    return root, expected, assignment, episodes


@pytest.fixture
def replay(sealed, tmp_path):
    original, expected, assignment, episodes = sealed
    root = tmp_path.resolve() / "plan"
    shutil.copytree(original, root)
    return root, expected, assignment, episodes


@pytest.fixture
def cached(replay, monkeypatch):
    _, _, assignment, episodes = replay

    def native(seed, sender):
        assert seed == assignment["seed"] and sender == "truthful"
        return episodes

    monkeypatch.setattr(session, "cases", native)
    return replay


def _input(monkeypatch, raw):
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    )


def _attempts(root, ticket):
    attempts = root / "attempts"
    return attempts / f"{ticket}.start.json", attempts / f"{ticket}.result.json"


def _private_record(path):
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    return json.loads(path.read_bytes())


def _wire(assignment, episodes):
    case = next(case for case in episodes if case.name == assignment["world"])
    initial = provenance.offer(
        case, assignment["age"], assignment["arm"], assignment["coin"], Fraction(1, 4)
    )
    return initial + b"\n", provenance.reread(case) + b"\n"


def test_serves_real_native_wires_and_records_only_actions(
    replay, monkeypatch, capsysbinary
):
    root, expected, assignment, episodes = replay
    ticket = assignment["ticket"]
    initial, reply = _wire(assignment, episodes)
    _input(monkeypatch, b'{"op":"read"}\n{"op":"commit","slot":"B"}\n')
    session.serve(root, expected, ticket)
    output = capsysbinary.readouterr()
    assert output.out == initial + reply
    assert output.err == b""
    start, result = _attempts(root, ticket)
    assert _private_record(start) == {
        "schema": "seatlab.receiver-study.start/v1",
        "plan_sha256": expected,
        "ticket": ticket,
    }
    assert _private_record(result) == {
        "schema": "seatlab.receiver-study.result/v1",
        "plan_sha256": expected,
        "ticket": ticket,
        "offer_sha256": assignment["offer_sha256"],
        "read_sha256": assignment["read_sha256"],
        "actions": [{"op": "read"}, {"op": "commit", "slot": "B"}],
    }
    assert initial.strip() not in start.read_bytes() + result.read_bytes()
    assert reply.strip() not in start.read_bytes() + result.read_bytes()


def test_commit_ends_replay_without_disclosing_read(cached, monkeypatch, capsysbinary):
    root, expected, assignment, episodes = cached
    ticket = assignment["ticket"]
    _input(monkeypatch, b'{"op":"commit","slot":"C"}\n{"op":"read"}\n')
    session.serve(root, expected, ticket)
    initial, _ = _wire(assignment, episodes)
    assert capsysbinary.readouterr().out == initial
    _, result = _attempts(root, ticket)
    record = _private_record(result)
    assert record["read_sha256"] is None
    assert record["actions"] == [{"op": "commit", "slot": "C"}]
    assert sys.stdin.buffer.readline() == b'{"op":"read"}\n'


@pytest.mark.parametrize(
    ("raw", "lines"),
    [
        (b"", 1),
        (b'{"op":"commit","slot":"invalid"}\n', 1),
        (b'{"op":"read"}\n', 2),
        (b'{"op":"read"}\n{"op":"read"}\n{"op":"commit","slot":"A"}\n', 2),
    ],
)
def test_failed_replay_is_permanently_incomplete(
    cached, monkeypatch, capsysbinary, raw, lines
):
    root, expected, assignment, episodes = cached
    ticket = assignment["ticket"]
    _input(monkeypatch, raw)
    with pytest.raises(ValueError):
        session.serve(root, expected, ticket)
    initial, reply = _wire(assignment, episodes)
    assert capsysbinary.readouterr().out == initial + (reply if lines == 2 else b"")
    start, result = _attempts(root, ticket)
    before = start.read_bytes()
    _private_record(start)
    assert not result.exists()
    _input(monkeypatch, b'{"op":"commit","slot":"A"}\n')
    with pytest.raises((ValueError, FileExistsError)):
        session.serve(root, expected, ticket)
    assert capsysbinary.readouterr().out == b""
    assert start.read_bytes() == before
    assert not result.exists()


@pytest.mark.parametrize("mismatch", ["pin", "offer", "read", "score"])
def test_mismatch_neither_emits_nor_claims(cached, monkeypatch, capsysbinary, mismatch):
    root, expected, assignment, episodes = cached
    ticket = assignment["ticket"]
    if mismatch == "pin":
        expected = "f" * 64 if expected != "f" * 64 else "e" * 64
    elif mismatch == "offer":
        monkeypatch.setattr(
            session, "offer", lambda *args: provenance.offer(*args) + b" "
        )
    elif mismatch == "read":
        monkeypatch.setattr(
            session, "reread", lambda case: provenance.reread(case) + b" "
        )
    else:
        original = assignment["scores"]
        loser = next(slot for slot, score in original.items() if score == 0)
        scores = {slot: int(slot == loser) for slot in original}
        changed = [
            replace(case, scores=scores) if case.name == assignment["world"] else case
            for case in episodes
        ]
        monkeypatch.setattr(session, "cases", lambda *_: changed)
    _input(monkeypatch, b'{"op":"commit","slot":"A"}\n')
    with pytest.raises(ValueError):
        session.serve(root, expected, ticket)
    assert capsysbinary.readouterr().out == b""
    assert not any(path.exists() for path in _attempts(root, ticket))


def test_existing_orphan_result_is_not_replaced(cached, monkeypatch, capsysbinary):
    root, expected, assignment, _ = cached
    ticket = assignment["ticket"]
    start, result = _attempts(root, ticket)
    result.write_bytes(b"private existing evidence")
    os.chmod(result, 0o600)
    _input(monkeypatch, b'{"op":"commit","slot":"B"}\n')
    with pytest.raises((ValueError, FileExistsError)):
        session.serve(root, expected, ticket)
    assert capsysbinary.readouterr().out == b""
    assert not start.exists()
    assert result.read_bytes() == b"private existing evidence"


def test_simultaneous_claim_has_one_emitting_winner(replay, monkeypatch):
    root, expected, assignment, episodes = replay
    ticket = assignment["ticket"]
    barrier = Barrier(2, timeout=10)
    emitted = []

    def native(*_):
        barrier.wait()
        return episodes

    monkeypatch.setattr(session, "cases", native)
    monkeypatch.setattr(session, "_emit", emitted.append)
    monkeypatch.setattr(session, "_read_action", lambda: "A")

    def attempt():
        try:
            session.serve(root, expected, ticket)
        except (ValueError, FileExistsError):
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt)
        second = pool.submit(attempt)
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
            False,
            True,
        ]
    initial, _ = _wire(assignment, episodes)
    assert emitted == [initial[:-1]]
    start, result = _attempts(root, ticket)
    assert _private_record(start)["ticket"] == ticket
    assert _private_record(result)["actions"] == [{"op": "commit", "slot": "A"}]
    assert {path.name for path in start.parent.iterdir()} == {start.name, result.name}
