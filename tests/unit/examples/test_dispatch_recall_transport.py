import importlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from harbor.models.task.task import Task
from harbor.models.trial.config import Participant, TaskConfig, TrialConfig
from harbor.trial.seats import check_launch

TASK = Path(__file__).resolve().parents[3] / "examples/tasks/dispatch-recall"
ENVIRONMENT = TASK / "environment"
PACKAGE = "examples.tasks.dispatch-recall"
ROLES = ("loader", "depot")
TRANSITIONS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
ELIGIBLE = {
    "immediate": frozenset({"commit", "close"}),
    "deferred": frozenset({"close"}),
    "withheld": frozenset(),
}
SILENT = "--"
VIEW = {"op": "view"}
REFUSAL = {"ok": False, "error": "invalid request"}
CUT_SCHEMA = "harbor.dispatch-recall.cut/v1"
VERDICT_SCHEMA = "harbor.dispatch-recall.verdict/v1"
DIAGNOSIS_SCHEMA = "harbor.dispatch-recall.diagnosis/v1"
COMMIT_TICKET, CLOSE_TICKET = "c0" * 16, "c1" * 16
HEX = frozenset("0123456789abcdef")
Json = dict[str, Any]
VERIFIER_FILES = (
    "verdict.json",
    "diagnostics.json",
    "reward.json",
    "private-recall-cut.json",
)


def _load(name: str) -> Any:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module(name)
    finally:
        sys.dont_write_bytecode = previous


recall_server = _load(f"{PACKAGE}.environment.recall_server")
recall_world = _load(f"{PACKAGE}.environment.recall_world")
verify = _load(f"{PACKAGE}.tests.verify")
native = _load("examples.seatlab.dispatch_recall.native")


def _seed_with_initial(index: int) -> int:
    for seed in range(256):
        bays, initial = recall_world.layout(seed)
        if initial == bays[index]:
            return seed
    raise AssertionError(f"no seed in range starts at bay index {index}")


SEEDS = (_seed_with_initial(0), _seed_with_initial(1))


@dataclass(frozen=True)
class Floor:
    transition: str
    timing: str
    bays: list[str]
    initial: str

    def __post_init__(self) -> None:
        assert len(self.bays) == 2 and self.bays == sorted(set(self.bays))
        assert all(len(bay) == 2 and bay != SILENT for bay in self.bays)
        assert self.initial in self.bays

    @property
    def other(self) -> str:
        return self.bays[1 - self.bays.index(self.initial)]

    @property
    def current(self) -> str:
        return self.other if self.transition == "move" else self.initial

    def notice(self, window: str, report: str | None) -> str:
        if window in ELIGIBLE[self.timing] and report is not None:
            return report
        return SILENT


def _loader_view(floor: Floor, turn: str, notice: str | None = None) -> Json:
    view: dict[str, Any] = {
        "role": "loader",
        "turn": turn,
        "active": turn in ("stage", "commit", "close"),
        "bays": floor.bays,
        "initial_bay": floor.initial,
    }
    if turn in ("commit", "close"):
        view["notice"] = notice
    return view


def _depot_view(floor: Floor, turn: str, staged: bool) -> Json:
    view: dict[str, Any] = {
        "role": "depot",
        "turn": turn,
        "active": turn == "report",
        "bays": floor.bays,
    }
    if staged:
        view["current_bay"] = floor.current
    return view


def _windowless(view: Json) -> Json:
    ticket = view["ticket"]
    assert type(ticket) is str and len(ticket) == 32 and set(ticket) <= HEX
    return {key: value for key, value in view.items() if key != "ticket"}


def _frame(action: object) -> bytes:
    return json.dumps(action, separators=(",", ":")).encode() + b"\n"


def _exchange(path: Path, payload: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        connection.sendall(payload)
        if not payload.endswith(b"\n"):
            connection.shutdown(socket.SHUT_WR)
        reply = bytearray()
        for _ in range(64):
            chunk = connection.recv(4096)
            if not chunk:
                break
            reply.extend(chunk)
        else:
            raise AssertionError("reply exceeds the read bound")
    assert reply.endswith(b"\n") and reply.count(b"\n") == 1, bytes(reply)
    return bytes(reply)


@dataclass
class Wire:
    root: Path
    control: Path
    session: Any
    log: list[tuple[str, str, bytes, bytes]] = field(default_factory=list)

    def socket(self, role: str) -> Path:
        return (
            self.control / "rpc.sock"
            if role == "control"
            else self.root / role / "rpc.sock"
        )

    def send(self, label: str, role: str, action: Json) -> Json:
        payload = _frame(action)
        line = _exchange(self.socket(role), payload)
        reply = json.loads(line)
        assert type(reply) is dict and set(reply) == {"ok", "data"}, reply
        assert reply["ok"] is True and type(reply["data"]) is dict
        assert line == recall_server.encode_reply(reply["data"])
        self.log.append((label, role, payload, line))
        return reply["data"]

    def refuse(self, role: str, payload: bytes) -> None:
        line = _exchange(self.socket(role), payload)
        assert json.loads(line) == REFUSAL and line == recall_server.encode_error()

    def freeze(self) -> Json:
        return verify.freeze(str(self.socket("control")))


@contextmanager
def _served(
    seed: int, transition: str, timing: str, tickets: tuple[str, str] | None = None
) -> Iterator[Wire]:
    session = native.Session(seed, transition, timing, tickets=tickets)
    with tempfile.TemporaryDirectory(prefix="dr-", dir="/tmp") as name:
        base = Path(name)
        wire = Wire(base / "r", base / "c", session)
        with recall_server.Family(wire.root, wire.control, session):
            for role in ROLES:
                assert stat.S_IMODE(wire.socket(role).stat().st_mode) == 0o666
            assert stat.S_IMODE(wire.socket("control").stat().st_mode) == 0o600
            yield wire
        assert not any(wire.socket(role).exists() for role in (*ROLES, "control"))


@dataclass
class Run:
    floor: Floor
    report: str | None = None
    committed: str | None = None
    snapshots: list[Json] = field(default_factory=list)
    tickets: dict[str, str] = field(default_factory=dict)


def _open_window(wire: Wire, run: Run, window: str) -> Json:
    seen = wire.send(f"{window} view", "loader", VIEW)
    expected = _loader_view(run.floor, window, run.floor.notice(window, run.report))
    assert _windowless(seen) == expected
    assert wire.send(f"{window} view again", "loader", VIEW) == seen
    assert wire.log[-1][3] == wire.log[-2][3]
    run.tickets[window] = seen["ticket"]
    return seen


def _play(wire: Wire, transition: str, timing: str, policy: str, stop: int = 4) -> Run:
    first = wire.send("loader stage view", "loader", VIEW)
    run = Run(Floor(transition, timing, first["bays"], first["initial_bay"]))
    floor = run.floor
    assert first == _loader_view(floor, "stage")
    assert wire.send("depot stage view", "depot", VIEW) == _depot_view(
        floor, "stage", False
    )
    run.snapshots.append(wire.session.snapshot())
    if stop == 0:
        return run
    staged = wire.send("stage", "loader", {"op": "stage", "bay": floor.initial})
    assert staged == {"op": "stage", "turn": "report"}
    run.snapshots.append(wire.session.snapshot())
    if stop == 1:
        return run
    seen = wire.send("depot report view", "depot", VIEW)
    assert seen == _depot_view(floor, "report", True)
    run.report = seen["current_bay"] if policy == "truthful" else None
    filed = wire.send("report", "depot", {"op": "report", "bay": run.report})
    assert filed == {"op": "report", "turn": "commit"}
    run.snapshots.append(wire.session.snapshot())
    if stop == 2:
        return run
    window = _open_window(wire, run, "commit")
    run.committed = floor.initial if window["notice"] == SILENT else window["notice"]
    action = {"op": "commit", "bay": run.committed, "ticket": window["ticket"]}
    assert wire.send("commit", "loader", action) == {"op": "commit", "turn": "close"}
    run.snapshots.append(wire.session.snapshot())
    if stop == 3:
        return run
    window = _open_window(wire, run, "close")
    action = {"op": "close", "ticket": window["ticket"]}
    assert wire.send("close", "loader", action) == {"op": "close", "turn": "done"}
    run.snapshots.append(wire.session.snapshot())
    assert wire.send("loader done view", "loader", VIEW) == _loader_view(floor, "done")
    assert wire.send("depot done view", "depot", VIEW) == _depot_view(
        floor, "done", True
    )
    return run


def _prefix(seed: int, run: Run, accepted: int) -> Json:
    floor = run.floor
    actions = [
        {"role": "loader", "op": "stage", "bay": floor.initial},
        {"role": "depot", "op": "report", "bay": run.report},
        {"role": "loader", "op": "commit", "bay": run.committed},
        {"role": "loader", "op": "close"},
    ][:accepted]
    windows: dict[str, Any] = {"commit": None, "close": None}
    for opened, window in ((2, "commit"), (3, "close")):
        if accepted > opened:
            windows[window] = {
                "notice": floor.notice(window, run.report),
                "redeemed": True,
            }
    return {
        "schema": CUT_SCHEMA,
        "seed": seed,
        "transition": floor.transition,
        "timing": floor.timing,
        "bays": floor.bays,
        "initial_bay": floor.initial,
        "current_bay": floor.current if accepted else floor.initial,
        "actions": actions,
        "windows": windows,
        "reward": None if accepted < 3 else int(run.committed == floor.current),
    }


def _verdict(cut: Json) -> Json:
    committed = cut["reward"] is not None
    return {
        "schema": VERDICT_SCHEMA,
        "disposition": "committed" if committed else "missing_commit",
        "reward": cut["reward"] if committed else 0,
        "closed": len(cut["actions"]) == 4,
    }


def _ticket_blind(wire: Wire, run: Run) -> list[tuple[str, str, bytes, bytes]]:
    uses = {"commit": 3, "close": 3}
    marks = {}
    for window, ticket in run.tickets.items():
        raw = ticket.encode()
        seen = sum(
            request.count(raw) + reply.count(raw) for *_, request, reply in wire.log
        )
        assert seen == uses[window]
        marks[raw] = f"<{window}-ticket>".encode()
    assert len(marks) == len(run.tickets) == 2
    blinded = []
    for label, role, request, reply in wire.log:
        for raw, mark in marks.items():
            request, reply = request.replace(raw, mark), reply.replace(raw, mark)
        blinded.append((label, role, request, reply))
    return blinded


def _world_differences(timing: str, policy: str) -> set[str]:
    differing = {"depot report view", "depot done view"}
    if policy == "truthful":
        differing.add("report")
        if timing == "immediate":
            differing |= {"commit view", "commit view again", "commit", "close view"}
            differing.add("close view again")
        if timing == "deferred":
            differing |= {"close view", "close view again"}
    return differing


@pytest.mark.parametrize("policy", ("truthful", "silent"))
@pytest.mark.parametrize("timing", TIMINGS)
@pytest.mark.parametrize("seed", SEEDS)
def test_both_worlds_share_one_envelope_until_a_timely_notice(
    seed: int, timing: str, policy: str
) -> None:
    runs, logs = {}, {}
    for transition in TRANSITIONS:
        with _served(seed, transition, timing) as wire:
            run = _play(wire, transition, timing, policy)
            for accepted, snapshot in enumerate(run.snapshots):
                assert snapshot == _prefix(seed, run, accepted)
                assert not any(t in json.dumps(snapshot) for t in run.tickets.values())
            cut = wire.freeze()
            assert cut == run.snapshots[-1] == wire.freeze()
            reward = int(
                transition == "stay" or (timing, policy) == ("immediate", "truthful")
            )
            assert cut["reward"] == reward
            assert (
                verify.verdict(cut)
                == _verdict(cut)
                == {
                    "schema": VERDICT_SCHEMA,
                    "disposition": "committed",
                    "reward": reward,
                    "closed": True,
                }
            )
            diagnosis = native.diagnose(cut)
            assert diagnosis["schema"] == DIAGNOSIS_SCHEMA
            assert diagnosis["reward"] == reward
            for *_, request, reply in wire.log:
                for word in (*TRANSITIONS, *TIMINGS):
                    assert word.encode() not in request + reply
            runs[transition], logs[transition] = run, _ticket_blind(wire, run)
    assert runs["stay"].tickets["commit"] != runs["move"].tickets["commit"]
    assert runs["stay"].tickets["close"] != runs["move"].tickets["close"]
    stay, move = logs["stay"], logs["move"]
    assert [entry[:2] for entry in stay] == [entry[:2] for entry in move]
    differing = {
        left[0] for left, right in zip(stay, move, strict=True) if left != right
    }
    assert differing == _world_differences(timing, policy)
    for left, right in zip(stay, move, strict=True):
        if left[0] in ("stage", "commit", "close", "report"):
            assert left[3] == right[3]


@pytest.mark.parametrize("timing", TIMINGS)
def test_private_freeze_cuts_each_wire_prefix(timing: str) -> None:
    seed = SEEDS[0]
    with _served(seed, "move", timing) as wire:
        full = _play(wire, "move", timing, "truthful")
    for accepted in range(5):
        with _served(seed, "move", timing) as wire:
            run = _play(wire, "move", timing, "truthful", stop=accepted)
            cut = wire.freeze()
            assert cut == run.snapshots[-1] == full.snapshots[accepted]
            assert cut == _prefix(seed, full, accepted)
            assert not any(t in json.dumps(cut) for t in run.tickets.values())
            assert verify.verdict(cut) == _verdict(cut)
            assert native.diagnose(cut)["progress"]["accepted"] == accepted
            _assert_sealed(wire, run.floor, tuple(run.tickets.values()), cut)


@pytest.mark.parametrize("timing", TIMINGS)
def test_freeze_at_commit_before_first_loader_view_keeps_window_unopened(
    timing: str,
) -> None:
    tickets = (COMMIT_TICKET, CLOSE_TICKET)
    with _served(SEEDS[1], "move", timing, tickets=tickets) as wire:
        run = _play(wire, "move", timing, "truthful", stop=2)
        assert run.tickets == {}
        cut = wire.freeze()
        assert cut == run.snapshots[-1] == _prefix(SEEDS[1], run, 2)
        assert cut["windows"] == {"commit": None, "close": None}
        assert cut["reward"] is None
        _assert_sealed(wire, run.floor, tickets, cut)
        for role in ROLES:
            wire.refuse(role, _frame(VIEW))
        assert wire.session.snapshot()["windows"] == {"commit": None, "close": None}
        assert verify.verdict(cut) == _verdict(cut)
        assert _verdict(cut)["disposition"] == "missing_commit"


def _malformed(bay: str, absent: str) -> list[bytes]:
    return [
        b"not json\n",
        b"[]\n",
        b"{}\n",
        b'{"op":"stage"}\n',
        _frame({"op": "stage", "bay": absent}),
        _frame({"op": "stage", "bay": [bay]}),
        _frame({"op": "stage", "bay": bay, "note": "x"}),
        f'{{"op":"stage","bay":"{bay}","bay":"{bay}"}}\n'.encode(),
        b'{"op":"stage","bay":NaN}\n',
        _frame({"op": "stage", "bay": bay})[:-1],
        _frame({"op": "freeze"}),
        _frame({"op": "Stage", "bay": bay}),
    ]


def _refuse_all(wire: Wire, floor: Floor, tickets: tuple[str, ...]) -> None:
    for role in ROLES:
        for bay in floor.bays:
            wire.refuse(role, _frame({"op": "stage", "bay": bay}))
            wire.refuse(role, _frame({"op": "report", "bay": bay}))
            for ticket in tickets:
                wire.refuse(
                    role, _frame({"op": "commit", "bay": bay, "ticket": ticket})
                )
        for ticket in tickets:
            wire.refuse(role, _frame({"op": "close", "ticket": ticket}))


def _assert_sealed(
    wire: Wire, floor: Floor, tickets: tuple[str, ...], cut: Json
) -> None:
    freeze = _frame({"op": "freeze"})
    raw = _exchange(wire.socket("control"), freeze)
    assert json.loads(raw) == {"ok": True, "data": cut}
    for role in ROLES:
        wire.refuse(role, _frame(VIEW))
    _refuse_all(wire, floor, (*tickets, COMMIT_TICKET, CLOSE_TICKET))
    wire.refuse("control", _frame(VIEW))
    assert _exchange(wire.socket("control"), freeze) == raw
    assert wire.session.snapshot() == cut


@pytest.mark.parametrize("target", ("notice", "stale"))
def test_refused_and_out_of_role_requests_never_revise_the_physical_outcome(
    target: str,
) -> None:
    tickets = (COMMIT_TICKET, CLOSE_TICKET)
    with _served(SEEDS[1], "move", "immediate", tickets=tickets) as wire:
        first = wire.send("loader stage view", "loader", VIEW)
        floor = Floor("move", "immediate", first["bays"], first["initial_bay"])
        absent = next(name for name in ("ZZ", "YY") if name not in floor.bays)
        baseline = wire.session.snapshot()
        for payload in _malformed(floor.initial, absent):
            wire.refuse("loader", payload)
        wire.refuse("depot", _frame({"op": "stage", "bay": floor.initial}))
        wire.refuse("loader", _frame({"op": "report", "bay": floor.initial}))
        wire.refuse(
            "loader",
            _frame({"op": "commit", "bay": floor.initial, "ticket": COMMIT_TICKET}),
        )
        wire.refuse("control", _frame(VIEW))
        wire.refuse("control", _frame({"op": "freeze", "role": "loader"}))
        wire.refuse("control", b"not json\n")
        assert wire.session.snapshot() == baseline
        wire.send("stage", "loader", {"op": "stage", "bay": floor.initial})
        current = wire.send("depot report view", "depot", VIEW)["current_bay"]
        assert current == floor.other
        wire.refuse("loader", _frame({"op": "report", "bay": floor.initial}))
        wire.refuse("depot", _frame({"op": "stage", "bay": floor.initial}))
        wire.refuse("depot", _frame({"op": "report", "bay": absent}))
        wire.refuse("depot", _frame({"op": "report"}))
        wire.send("report", "depot", {"op": "report", "bay": current})
        assert "notice" not in wire.send("depot commit view", "depot", VIEW)
        wire.refuse(
            "loader", _frame({"op": "commit", "bay": current, "ticket": COMMIT_TICKET})
        )
        assert wire.session.snapshot()["windows"] == {"commit": None, "close": None}
        window = wire.send("commit view", "loader", VIEW)
        assert window == {
            **_loader_view(floor, "commit", current),
            "ticket": COMMIT_TICKET,
        }
        for role, action in (
            ("depot", {"op": "commit", "bay": current, "ticket": COMMIT_TICKET}),
            ("loader", {"op": "commit", "bay": current, "ticket": CLOSE_TICKET}),
            (
                "loader",
                {"op": "commit", "bay": current, "ticket": COMMIT_TICKET.upper()},
            ),
            ("loader", {"op": "commit", "bay": absent, "ticket": COMMIT_TICKET}),
            ("loader", {"op": "commit", "bay": current}),
            ("loader", {"op": "close", "ticket": COMMIT_TICKET}),
        ):
            wire.refuse(role, _frame(action))
        committed = current if target == "notice" else floor.initial
        action = {"op": "commit", "bay": committed, "ticket": COMMIT_TICKET}
        assert wire.send("commit", "loader", action) == {
            "op": "commit",
            "turn": "close",
        }
        held = wire.session.snapshot()
        assert held["reward"] == int(target == "notice")
        assert held["windows"]["commit"] == {"notice": current, "redeemed": True}
        _refuse_all(wire, floor, tickets)
        assert wire.session.snapshot() == held
        window = wire.send("close view", "loader", VIEW)
        assert window == {
            **_loader_view(floor, "close", current),
            "ticket": CLOSE_TICKET,
        }
        wire.refuse("depot", _frame({"op": "close", "ticket": CLOSE_TICKET}))
        wire.refuse("loader", _frame({"op": "close", "ticket": COMMIT_TICKET}))
        wire.send("close", "loader", {"op": "close", "ticket": CLOSE_TICKET})
        _refuse_all(wire, floor, tickets)
        closed = wire.session.snapshot()
        assert closed["reward"] == held["reward"]
        assert closed["actions"] == [
            *held["actions"],
            {"role": "loader", "op": "close"},
        ]
        assert wire.send("done view", "loader", VIEW) == _loader_view(floor, "done")
        assert wire.send("done view", "depot", VIEW) == _depot_view(floor, "done", True)
        cut = wire.freeze()
        assert cut == closed
        assert COMMIT_TICKET not in json.dumps(cut) and CLOSE_TICKET not in json.dumps(
            cut
        )
        _assert_sealed(wire, floor, tickets, cut)
        assert verify.verdict(cut) == _verdict(cut)


def _seat(wire: Wire, role: str, *args: str, refused: bool = False) -> Json:
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(ENVIRONMENT / "seat/recall.py"), *args],
        env={
            "PATH": os.environ.get("PATH", ""),
            "RECALL_SEAT_SOCKET": str(wire.socket(role)),
        },
        cwd=wire.root.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if refused:
        assert result.returncode == 1 and not result.stdout
        assert result.stderr.startswith("recall: ")
        return {}
    assert result.returncode == 0 and not result.stderr, result.stderr
    data = json.loads(result.stdout)
    assert (
        result.stdout == json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return data


def _verifier(
    control: Path, logs: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    logs.mkdir(exist_ok=True)
    command = [
        sys.executable,
        "-I",
        "-B",
        str(TASK / "tests/verify.py"),
        "--control",
        str(control),
        "--logs",
        str(logs),
        "--app",
        str(ENVIRONMENT),
        *extra,
    ]
    return subprocess.run(
        command,
        env={"PATH": os.environ.get("PATH", ""), "RECALL_RETAIN_CUT": "1"},
        cwd=TASK / "tests",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _seat_play(wire: Wire, outcome: str) -> Floor:
    first = _seat(wire, "loader", "view")
    floor = Floor("move", "immediate", first["bays"], first["initial_bay"])
    assert first == _loader_view(floor, "stage")
    _seat(wire, "depot", "stage", floor.initial, refused=True)
    assert _seat(wire, "loader", "stage", floor.initial) == {
        "op": "stage",
        "turn": "report",
    }
    seen = _seat(wire, "depot", "wait", "report")
    assert seen == _depot_view(floor, "report", True)
    _seat(wire, "loader", "report", floor.initial, refused=True)
    assert _seat(wire, "depot", "report", seen["current_bay"]) == {
        "op": "report",
        "turn": "commit",
    }
    window = _seat(wire, "loader", "wait", "commit")
    assert _windowless(window) == _loader_view(floor, "commit", floor.current)
    _seat(wire, "depot", "commit", floor.current, window["ticket"], refused=True)
    if outcome == "missing":
        return floor
    bay = window["notice"] if outcome == "notice" else floor.initial
    ack = _seat(wire, "loader", "commit", bay, window["ticket"])
    assert ack == {"op": "commit", "turn": "close"}
    window = _seat(wire, "loader", "wait", "close")
    assert _windowless(window) == _loader_view(floor, "close", floor.current)
    assert _seat(wire, "loader", "close", window["ticket"]) == {
        "op": "close",
        "turn": "done",
    }
    assert _seat(wire, "depot", "wait", "done") == _depot_view(floor, "done", True)
    return floor


@pytest.mark.parametrize("outcome", ("notice", "stale", "missing"))
def test_seat_client_and_private_verifier_grade_the_physical_commit(
    outcome: str, tmp_path: Path
) -> None:
    logs = tmp_path / "verifier"
    with _served(SEEDS[0], "move", "immediate") as wire:
        _seat_play(wire, outcome)
        graded = _verifier(wire.socket("control"), logs)
        cut = wire.freeze()
        assert _verifier(wire.socket("control"), logs).returncode == 1
    reward = int(outcome == "notice")
    disposition = "missing_commit" if outcome == "missing" else "committed"
    verdict = {
        "schema": VERDICT_SCHEMA,
        "disposition": disposition,
        "reward": reward,
        "closed": outcome != "missing",
    }
    assert cut["reward"] == (None if outcome == "missing" else reward)
    assert graded.returncode == 0, graded.stderr
    assert graded.stdout.splitlines() == [f"reward={reward} disposition={disposition}"]
    assert verify.verdict(cut) == verdict
    assert json.loads((logs / "verdict.json").read_text()) == verdict
    assert json.loads((logs / "reward.json").read_text()) == {"reward": reward}
    diagnosis = json.loads(json.dumps(native.diagnose(cut)))
    assert json.loads((logs / "diagnostics.json").read_text()) == diagnosis
    private = logs / "private-recall-cut.json"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert json.loads(private.read_text()) == cut
    replay_logs = tmp_path / "replay"
    unserved = tmp_path / "unserved.sock"
    replay = _verifier(unserved, replay_logs, "--cut", str(private))
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == {"diagnostics": diagnosis, "verdict": verdict}
    assert not any(replay_logs.iterdir())


def test_layout_and_public_views_do_not_depend_on_condition() -> None:
    for seed in SEEDS:
        for role in ROLES:
            views = {
                json.dumps(native.Session(seed, transition, timing).view(role))
                for transition in TRANSITIONS
                for timing in TIMINGS
            }
            assert len(views) == 1


def test_two_role_package_installs_without_disclosing_arm_or_seed() -> None:
    task = Task(TASK)
    assert set(task.config.agents) == set(ROLES)
    participants = [Participant(seat=role, name="nop") for role in ROLES]
    check_launch(
        TrialConfig(task=TaskConfig(path=TASK), participants=participants), task
    )
    services = yaml.safe_load((ENVIRONMENT / "docker-compose.yaml").read_text())[
        "services"
    ]
    assert set(services) == {"main", *ROLES}
    main_mounts = services["main"]["volumes"]
    hidden = ("RECALL_SEED", "RECALL_TRANSITION", "RECALL_TIMING", "recall-control")
    for role in ROLES:
        seat = services[role]
        assert "environment" not in seat and "env_file" not in seat
        assert seat["build"] == "./seat" and seat["network_mode"] == "none"
        assert seat["volumes"] == [f"{role}-rpc:/run/recall-seat:ro"]
        assert f"{role}-rpc:/run/recall/{role}" in main_mounts
        exposed = json.dumps(seat)
        assert "RECALL_" not in exposed and "${" not in exposed
        instruction = task.paths.seat_instruction_path(role).read_text()
        for name in (
            *hidden,
            "recall_world",
            "recall_evidence",
            "/run/recall/",
            "seed",
        ):
            assert name.lower() not in instruction.lower()
    seat_files = {path.name for path in (ENVIRONMENT / "seat").iterdir()}
    assert seat_files - {"__pycache__"} == {"Dockerfile", "recall.py"}
    client = (ENVIRONMENT / "seat/recall.py").read_text()
    for name in (*hidden, "recall_world", "recall_evidence"):
        assert name not in client
