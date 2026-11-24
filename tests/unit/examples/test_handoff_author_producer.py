import importlib
import json
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from examples.seat_handoffs import core, producer
from examples.seat_handoffs.core import Edge
from examples.seat_handoffs.producer import Journal, seal

RUN = "0b6f3c1e-8d2a-4f57-9a41-3c7e2d915b08"
OTHER_RUN = "5d1f7a20-3c44-4e8b-9b0e-6a2f81c0d4e7"
TASK_EDGES = (
    Edge(message="initial", sender="reporter", receiver="booker"),
    Edge(message="revision", sender="reporter", receiver="booker"),
)
FANOUT_EDGES = (
    Edge(message="initial", sender="reporter", receiver="booker"),
    Edge(message="initial", sender="reporter", receiver="auditor"),
    Edge(message="revision", sender="reporter", receiver="booker"),
)
SOURCE = Path(core.__file__).parent


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def findings(data: bytes, tmp_path: Path, boundary: str) -> dict[str, tuple[str, ...]]:
    sealed = tmp_path / "sealed.jsonl"
    sealed.write_bytes(data)
    return {
        f"{row.message}>{row.receiver}": (
            row.receipt,
            row.timing,
            row.commit,
            row.basis,
        )
        for row in core.inspect(sealed, boundary=boundary)
    }


def play_task(journal: Journal) -> None:
    assert journal.sent("initial", "reporter") is True
    assert journal.returned("initial", "booker") is True
    assert journal.acknowledged("initial", "booker") is True
    assert journal.sent("revision", "reporter") is True
    assert journal.returned("revision", "booker") is True
    assert journal.commit("booker") is True


def test_task_flow_freezes_native_cut_and_seals_a_closed_copy(tmp_path: Path) -> None:
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, TASK_EDGES, acknowledgements=True, run=RUN) as journal:
        play_task(journal)
        assert journal.sent("initial", "reporter") is False
        assert journal.returned("revision", "booker") is False
        assert journal.acknowledged("initial", "booker") is False
        assert journal.commit("booker") is False
        cut = journal.freeze()
        assert journal.freeze() == cut
    assert cut == {"run": RUN, "events": 7}
    assert json.loads(json.dumps(cut)) == cut
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_bytes()
    lines = records(path)
    assert [line["seq"] for line in lines] == list(range(7))
    assert lines[0]["acknowledgements"] is True
    assert all(line["type"] != "closed" for line in lines)
    sealed = seal(raw, cut, 1.0)
    assert path.read_bytes() == raw
    assert sealed.startswith(raw)
    assert json.loads(sealed.splitlines()[-1]) == {
        "seq": cut["events"],
        "type": "closed",
        "reward": 1.0,
    }
    assert findings(sealed, tmp_path, "acknowledged") == {
        "initial>booker": ("confirmed", "before", "recorded", "receiver_ack"),
        "revision>booker": ("absent", "unknown", "recorded", "receiver_ack"),
    }


def test_fanout_without_acknowledgements_keeps_per_receiver_commits(
    tmp_path: Path,
) -> None:
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, FANOUT_EDGES, acknowledgements=False) as journal:
        assert journal.sent("initial", "reporter") is True
        assert journal.returned("initial", "auditor") is True
        assert journal.commit("auditor") is True
        assert journal.returned("initial", "booker") is True
        assert journal.returned("initial", "booker") is False
        assert journal.commit("booker") is True
        with pytest.raises(ValueError, match="without acknowledgements"):
            journal.acknowledged("initial", "booker")
        cut = journal.freeze()
    header = records(path)[0]
    assert "acknowledgements" not in header
    assert core.canonical_uuid(cut["run"]) and cut["run"] == header["run"]
    assert cut["events"] == 6
    sealed = seal(path.read_bytes(), cut, None)
    assert findings(sealed, tmp_path, "returned") == {
        "initial>booker": ("confirmed", "before", "recorded", "sealed_world"),
        "initial>auditor": ("confirmed", "before", "recorded", "sealed_world"),
        "revision>booker": ("absent", "unknown", "recorded", "sealed_world"),
    }


ILLEGAL: dict[str, tuple[Callable[[Journal], object], type[Exception]]] = {
    "mismatching_sender": (lambda j: j.sent("initial", "booker"), ValueError),
    "undeclared_message": (lambda j: j.sent("summary", "reporter"), ValueError),
    "return_before_send": (lambda j: j.returned("revision", "booker"), ValueError),
    "undeclared_receiver": (lambda j: j.returned("initial", "auditor"), ValueError),
    "ack_before_return": (lambda j: j.acknowledged("revision", "booker"), ValueError),
    "foreign_commit": (lambda j: j.commit("reporter"), ValueError),
    "unsafe_identifier": (lambda j: j.sent("initial\n", "reporter"), ValueError),
    "non_string": (lambda j: j.commit(cast(str, 7)), ValueError),
}


@pytest.mark.parametrize("name", sorted(ILLEGAL))
def test_illegal_action_appends_nothing_and_keeps_play_open(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / "handoffs.jsonl"
    action, error = ILLEGAL[name]
    with Journal(path, TASK_EDGES, acknowledgements=True, run=RUN) as journal:
        assert journal.sent("initial", "reporter") is True
        before = path.read_bytes()
        with pytest.raises(error):
            action(journal)
        assert path.read_bytes() == before
        assert journal.returned("initial", "booker") is True
        assert journal.freeze() == {"run": RUN, "events": 3}


ACTIONS: dict[str, Callable[[Journal], object]] = {
    "sent": lambda j: j.sent("revision", "reporter"),
    "duplicate_sent": lambda j: j.sent("initial", "reporter"),
    "returned": lambda j: j.returned("initial", "booker"),
    "acknowledged": lambda j: j.acknowledged("initial", "booker"),
    "commit": lambda j: j.commit("booker"),
}


@pytest.mark.parametrize("name", sorted(ACTIONS))
def test_frozen_or_closed_journal_refuses_every_action(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, TASK_EDGES, acknowledgements=True, run=RUN) as journal:
        assert journal.sent("initial", "reporter") is True
        cut = journal.freeze()
        frozen = path.read_bytes()
        with pytest.raises(RuntimeError, match="frozen"):
            ACTIONS[name](journal)
        assert journal.freeze() == cut
    with pytest.raises(RuntimeError, match="not open"):
        ACTIONS[name](journal)
    with pytest.raises(RuntimeError, match="not open"):
        journal.freeze()
    assert path.read_bytes() == frozen


def test_exception_in_play_closes_without_cut_and_journal_is_single_use(
    tmp_path: Path,
) -> None:
    path = tmp_path / "handoffs.jsonl"
    journal = Journal(path, TASK_EDGES, acknowledgements=True, run=RUN)
    with pytest.raises(KeyError), journal:
        journal.sent("initial", "reporter")
        raise KeyError("world crashed")
    with pytest.raises(RuntimeError, match="not open"):
        journal.freeze()
    with pytest.raises(RuntimeError, match="only once"):
        journal.__enter__()
    assert [line["type"] for line in records(path)] == ["start", "sent"]


def test_start_record_is_durable_before_enter_returns(tmp_path: Path) -> None:
    path = tmp_path / "handoffs.jsonl"
    journal = Journal(path, TASK_EDGES, acknowledgements=False, run=RUN)
    assert not path.exists()
    with journal:
        frames, torn = core.frames_from_bytes(path.read_bytes())
        assert not torn and len(frames) == 1
        replay = core.replay_frames(frames)
        assert (replay.run, replay.edges, replay.acks) == (RUN, TASK_EDGES, False)
        assert journal.freeze() == {"run": RUN, "events": 1}


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling"])
def test_existing_path_is_refused_and_left_untouched(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "handoffs.jsonl"
    target = tmp_path / "target"
    if kind == "file":
        path.write_bytes(b"prior evidence\n")
    else:
        if kind == "symlink":
            target.write_bytes(b"prior evidence\n")
        path.symlink_to(target)
    with (
        pytest.raises(FileExistsError),
        Journal(path, TASK_EDGES, acknowledgements=True, run=RUN),
    ):
        pytest.fail("entered a journal over an existing path")
    if kind == "file":
        assert path.read_bytes() == b"prior evidence\n"
    elif kind == "symlink":
        assert target.read_bytes() == b"prior evidence\n"
    else:
        assert not target.exists()


CONSTRUCTION: dict[str, tuple[Callable[[Path], Journal], type[Exception]]] = {
    "empty_edges": (lambda p: Journal(p, (), acknowledgements=True), ValueError),
    "list_edges": (
        lambda p: Journal(
            p, cast(tuple[Edge, ...], list(TASK_EDGES)), acknowledgements=True
        ),
        ValueError,
    ),
    "tuple_items": (
        lambda p: Journal(
            p, cast(tuple[Edge, ...], (("a", "b", "c"),)), acknowledgements=True
        ),
        TypeError,
    ),
    "self_edge": (
        lambda p: Journal(p, (Edge("m", "x", "x"),), acknowledgements=True),
        ValueError,
    ),
    "duplicate_edge": (
        lambda p: Journal(p, (*TASK_EDGES, TASK_EDGES[0]), acknowledgements=True),
        ValueError,
    ),
    "two_senders": (
        lambda p: Journal(
            p,
            (*TASK_EDGES, Edge("initial", "auditor", "booker2")),
            acknowledgements=True,
        ),
        ValueError,
    ),
    "too_many_edges": (
        lambda p: Journal(
            p,
            tuple(Edge(f"m{i}", "s", "r") for i in range(core.MAX_EDGES + 1)),
            acknowledgements=True,
        ),
        ValueError,
    ),
    "upper_uuid": (
        lambda p: Journal(p, TASK_EDGES, acknowledgements=True, run=RUN.upper()),
        ValueError,
    ),
    "int_acknowledgements": (
        lambda p: Journal(p, TASK_EDGES, acknowledgements=cast(bool, 1)),
        TypeError,
    ),
    "string_path": (
        lambda p: Journal(cast(Path, str(p)), TASK_EDGES, acknowledgements=True),
        TypeError,
    ),
}


@pytest.mark.parametrize("name", sorted(CONSTRUCTION))
def test_invalid_declaration_creates_no_file(tmp_path: Path, name: str) -> None:
    build, error = CONSTRUCTION[name]
    with pytest.raises(error):
        build(tmp_path / "handoffs.jsonl")
    assert list(tmp_path.iterdir()) == []


def faulty(real: Callable[..., Any], path: Path, mode: str) -> Callable[..., Any]:
    def fake(fd: int, *args: Any) -> Any:
        if path.exists() and os.fstat(fd).st_ino == path.stat().st_ino:
            if mode == "short":
                return real(fd, args[0][:5])
            raise OSError(28, "No space left on device")
        return real(fd, *args)

    return fake


@pytest.mark.parametrize(
    ("call", "mode"), [("write", "raise"), ("write", "short"), ("fsync", "raise")]
)
def test_io_fault_refuses_play_and_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str, mode: str
) -> None:
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, TASK_EDGES, acknowledgements=True, run=RUN) as journal:
        assert journal.sent("initial", "reporter") is True
        with monkeypatch.context() as patch:
            patch.setattr(os, call, faulty(getattr(os, call), path, mode))
            with pytest.raises(OSError):
                journal.returned("initial", "booker")
        with pytest.raises(RuntimeError, match="write fault"):
            journal.returned("initial", "booker")
        with pytest.raises(RuntimeError, match="write fault"):
            journal.freeze()


def test_header_fault_leaves_no_usable_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "handoffs.jsonl"
    journal = Journal(path, TASK_EDGES, acknowledgements=True, run=RUN)
    monkeypatch.setattr(os, "fsync", faulty(os.fsync, path, "raise"))
    with pytest.raises(OSError):
        journal.__enter__()
    monkeypatch.undo()
    with pytest.raises(RuntimeError, match="not open"):
        journal.freeze()
    with (
        pytest.raises(FileExistsError),
        Journal(path, TASK_EDGES, acknowledgements=True, run=RUN),
    ):
        pytest.fail("reopened a faulted journal path")


def test_caller_state_failure_blocks_the_cut(tmp_path: Path) -> None:
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, TASK_EDGES, acknowledgements=True, run=RUN) as journal:
        assert journal.sent("initial", "reporter") is True
        journal.fail()
        with pytest.raises(RuntimeError, match="caller fault"):
            journal.returned("initial", "booker")
        with pytest.raises(RuntimeError, match="caller fault"):
            journal.freeze()
    assert [line["type"] for line in records(path)] == ["start", "sent"]


def open_journal(tmp_path: Path) -> tuple[bytes, dict[str, Any]]:
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, TASK_EDGES, acknowledgements=True, run=RUN) as journal:
        play_task(journal)
        cut = journal.freeze()
    return path.read_bytes(), cut


SEAL_REJECTIONS: dict[
    str, tuple[Callable[[bytes, dict[str, Any]], Any], type[Exception]]
] = {
    "torn_tail": (lambda raw, cut: seal(raw + b'{"seq":7', cut, 1.0), ValueError),
    "dropped_newline": (lambda raw, cut: seal(raw[:-1], cut, 1.0), ValueError),
    "already_closed": (
        lambda raw, cut: seal(seal(raw, cut, 1.0), {**cut, "events": 8}, 1.0),
        ValueError,
    ),
    "wrong_run": (
        lambda raw, cut: seal(raw, {**cut, "run": OTHER_RUN}, 1.0),
        ValueError,
    ),
    "ahead_cut": (lambda raw, cut: seal(raw, {**cut, "events": 8}, 1.0), ValueError),
    "header_excluded_count": (
        lambda raw, cut: seal(raw, {**cut, "events": cut["events"] - 1}, 1.0),
        ValueError,
    ),
    "bool_events": (
        lambda raw, cut: seal(raw, {**cut, "events": True}, 1.0),
        ValueError,
    ),
    "extra_cut_key": (
        lambda raw, cut: seal(raw, {**cut, "reward": 1}, 1.0),
        ValueError,
    ),
    "reward_range": (lambda raw, cut: seal(raw, cut, 1.5), ValueError),
    "bool_reward": (lambda raw, cut: seal(raw, cut, True), ValueError),
    "bytearray": (
        lambda raw, cut: seal(cast(bytes, bytearray(raw)), cut, 1.0),
        TypeError,
    ),
    "empty": (lambda raw, cut: seal(b"", {**cut, "events": 1}, 1.0), ValueError),
    "repeated_send": (
        lambda raw, cut: seal(
            raw + b'{"seq":7,"type":"sent","message":"initial","sender":"reporter"}\n',
            {**cut, "events": 8},
            1.0,
        ),
        ValueError,
    ),
}


@pytest.mark.parametrize("name", sorted(SEAL_REJECTIONS))
def test_seal_refuses_inexact_or_invalid_journal(tmp_path: Path, name: str) -> None:
    raw, cut = open_journal(tmp_path)
    attempt, error = SEAL_REJECTIONS[name]
    with pytest.raises(error):
        attempt(raw, cut)


def test_full_fanout_at_edge_limit_fits_the_frame_bound(tmp_path: Path) -> None:
    edges = tuple(
        Edge(message=f"m{i:02d}", sender="s", receiver=f"r{i:02d}")
        for i in range(core.MAX_EDGES)
    )
    path = tmp_path / "handoffs.jsonl"
    with Journal(path, edges, acknowledgements=True, run=RUN) as journal:
        for edge in edges:
            assert journal.sent(edge.message, edge.sender) is True
            assert journal.returned(edge.message, edge.receiver) is True
            assert journal.acknowledged(edge.message, edge.receiver) is True
            assert journal.commit(edge.receiver) is True
        cut = journal.freeze()
    assert cut["events"] == core.MAX_FRAMES - 1
    sealed = seal(path.read_bytes(), cut, 0.0)
    frames, torn = core.frames_from_bytes(sealed)
    assert not torn and len(frames) == core.MAX_FRAMES
    assert core.replay_frames(frames).reward == 0.0


def test_relocated_handoff_v1_package_runs_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "kit" / "handoff_v1"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    shutil.copyfile(SOURCE / "core.py", package / "core.py")
    shutil.copyfile(Path(producer.__file__), package / "producer.py")
    monkeypatch.syspath_prepend(str(tmp_path / "kit"))
    names = ("handoff_v1", "handoff_v1.core", "handoff_v1.producer")
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        kit = importlib.import_module("handoff_v1.producer")
        kit_core = importlib.import_module("handoff_v1.core")
        edges = tuple(
            kit_core.Edge(message=e.message, sender=e.sender, receiver=e.receiver)
            for e in FANOUT_EDGES
        )
        path = tmp_path / "handoffs.jsonl"
        with kit.Journal(path, edges, acknowledgements=False, run=RUN) as journal:
            assert journal.sent("initial", "reporter") is True
            assert journal.returned("initial", "booker") is True
            cut = journal.freeze()
        with pytest.raises(TypeError):
            kit.Journal(tmp_path / "other.jsonl", FANOUT_EDGES, acknowledgements=False)
        sealed = kit.seal(path.read_bytes(), cut, 0.5)
    finally:
        for name in names:
            sys.modules.pop(name, None)
    assert sealed == seal(path.read_bytes(), cut, 0.5)
