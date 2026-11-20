import json
import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, override

import pytest

from examples.seatlab.visibility import calendar_packet, replica_io
from examples.seatlab.visibility import calendar_replica as replica
from examples.seatlab.visibility.calendar_replica_seat import RelayCalendarSeat
from examples.seatlab.visibility.calendar_replica_source import Identity
from examples.seatlab.visibility.calendar_seat import _SeatError
from harbor.agents.factory import AgentFactory
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import ExceptionInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult

_RUN = "rx-w00-d1-r0"
_NATIVE = "a" * 64
_SEMANTICS = "b" * 64
_SEEDS = ('{"starts":[1]}', '{"starts":[2,3]}')
_INITIAL = (f"{_RUN}:cal_initial_p1", f"{_RUN}:cal_initial_p2")
_PEER = f"{_RUN}-peer:cal_recheck"
_BLIND = f"{_RUN}-blind:cal_recheck"
_DECIDE = f"{_RUN}-blind:cal_decide"


class _Replies(BytesIO):
    @override
    def read(self, size: int | None = -1, /) -> bytes:
        # The dispatcher replies to one request at a time.
        return self.readline(-1 if size is None else size)


@dataclass
class _Harness:
    task: Path
    out: Path
    created: list[TrialConfig] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    mutate_after_first: bool = False
    harbor_checksum: str = _NATIVE

    def args(self) -> list[str]:
        return [
            "play",
            "--tasks",
            str(self.task.parent),
            "--out",
            str(self.out),
            "--world",
            "0",
            "--direction",
            "1",
            "--repetition",
            "0",
        ]


@dataclass
class _SimulatedTrial:
    config: TrialConfig
    harness: _Harness
    task_dir: Path
    checksum: str

    @property
    def task(self) -> "_SimulatedTrial":
        return self

    async def run(self) -> TrialResult:
        config = self.config
        self.harness.ran.append(config.trial_name)
        os.write(1, b"host diagnostic\n")
        trial = config.trials_dir / config.trial_name
        trial.mkdir()
        actors: dict[str, RelayCalendarSeat] = {}
        contexts: dict[str, AgentContext] = {}
        stages: dict[str, dict[str, Any]] = {}
        for participant in config.participants:
            seat = participant.seat
            actor = AgentFactory.create_agent_from_config(participant, trial / seat)
            assert isinstance(actor, RelayCalendarSeat)
            assert actor.to_agent_info().model_info is None
            actors[seat] = actor
            stages[seat] = {"stage": "complete"}
            contexts[seat] = AgentContext()

        # Only the native lifecycle is simulated. The stock import path and
        # relay encode, bind, request, decode, and seal actual turns.
        error = None
        try:
            revised = await actors["p2"]._measure(
                "synthetic recheck", "cal_recheck", contexts["p2"], stages["p2"]
            )
            if revised is None:
                stages["p1"]["stage"] = "peer_model_invalid"
                slot = None
            else:
                slot = await actors["p1"]._measure(
                    "synthetic decision", "cal_decide", contexts["p1"], stages["p1"]
                )
        except _SeatError as exc:
            stages["p2"]["stage"] = str(exc)
            error = ExceptionInfo.from_exception(exc)
            slot = None

        for seat, context in contexts.items():
            context.metadata = {"calendar": stages[seat]}

        # A zero must not be attributed to the model when a seat has an exception.
        verifier = trial / "verifier"
        verifier.mkdir()
        (verifier / "reward.json").write_text('{"reward":0}')
        (verifier / "score.json").write_text(
            json.dumps(
                {
                    "score": 0,
                    "error": None,
                    "n_submissions": int(slot is not None),
                    "submitted_by": "p1" if slot is not None else None,
                    "n_messages": 2,
                }
            )
        )
        for name in ("events", "messages"):
            artifact = trial / f"artifacts/logs/world/{name}.jsonl"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("{}\n")
        return TrialResult(
            trial_name=config.trial_name,
            trial_uri=trial.as_uri(),
            task_id=config.task.get_task_id(),
            task_name=self.task_dir.name,
            task_checksum=self.checksum,
            config=config,
            agent_info=actors["p1"].to_agent_info(),
            finished_at=datetime.now(UTC),
            verifier_result=VerifierResult(rewards={"reward": 0}),
            exception_info=error,
            seat_results=[
                SeatResult(seat=seat, under_test=True, agent_result=context)
                for seat, context in contexts.items()
            ],
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    with monkeypatch.context() as before_import:
        before_import.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "true")
        from harbor.trial.trial import Trial

    private = tmp_path.resolve() / "private"
    private.mkdir(mode=0o700)
    task = private / "regenerated" / "schedule-000-talk"
    task.mkdir(parents=True)
    state = _Harness(task, private / "block")
    monkeypatch.setattr(
        os, "environ", {"MODEL_API_KEY": "synthetic", "DOCKER_CONTEXT": "remote"}
    )
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(
        replica, "_local_docker_host", lambda _host: "unix:///local-test.sock"
    )

    def verify(path: Path, world: int) -> Identity:
        assert path == task and world == 0
        mark = state.ran[-1] if state.ran else "seed"
        state.checks.append(mark)
        native = "c" * 64 if state.mutate_after_first and state.ran else _NATIVE
        return Identity(native, _SEMANTICS)

    async def create(config: TrialConfig) -> _SimulatedTrial:
        assert "MODEL_API_KEY" not in os.environ and "DOCKER_CONTEXT" not in os.environ
        assert os.environ["HOME"] == str(state.out / "host" / "home")
        assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
        assert os.environ["COMPOSE_DISABLE_ENV_FILE"] == "1"
        assert config.task.path == task
        assert config.environment.type == "docker"
        assert config.environment.force_build and config.environment.delete
        assert not config.verifier.disable
        assert replica_io.current() is not None
        state.created.append(config)
        return _SimulatedTrial(config, state, task, state.harbor_checksum)

    tables = (((0, 0, 0, 0, 0),), ((1, 0, 0, 0, 0),))
    monkeypatch.setattr(
        calendar_packet, "load", lambda *_: (calendar_packet.World(0, _NATIVE, tables),)
    )
    monkeypatch.setattr(replica.calendar_replica_source, "verify", verify)
    monkeypatch.setattr(Trial, "create", create)
    return state


def _play(
    harness: _Harness,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    ids: tuple[str, ...],
    texts: tuple[str, ...],
) -> tuple[int, list[str], dict[str, str]]:
    replies = _Replies(
        b"".join(
            (json.dumps({"id": name, "text": text}) + "\n").encode()
            for name, text in zip(ids, texts, strict=True)
        )
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=replies))
    code = replica.main(harness.args())
    captured = capfd.readouterr()
    frames: list[dict[str, str]] = [
        json.loads(line) for line in captured.out.splitlines()
    ]
    assert not captured.err
    assert all(
        frame["type"] == "turn" and set(frame) == {"type", "id", "prompt"}
        for frame in frames[:-1]
    )
    with pytest.raises(ValueError):
        replica_io.current()
    return code, [frame["id"] for frame in frames[:-1]], frames[-1]


def test_native_zero_can_complete_matched_block(
    harness: _Harness,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = (*_INITIAL, _PEER, _BLIND, _DECIDE)
    code, turns, end = _play(
        harness, capfd, monkeypatch, ids, (*_SEEDS, "bad", _SEEDS[1], '{"slot":3}')
    )
    assert (code, turns, end) == (0, list(ids), {"type": "end", "status": "complete"})
    assert harness.ran == [f"{_RUN}-peer", f"{_RUN}-blind"]
    assert len(harness.checks) == 5
    first, second = harness.created
    for a, b in zip(first.participants, second.participants, strict=True):
        assert a.seat == b.seat and a.import_path == b.import_path == replica._IMPORT
        assert a.model_name is None and b.model_name is None and not a.env and not b.env
        assert {k: v for k, v in a.kwargs.items() if k != "arm"} == {
            k: v for k, v in b.kwargs.items() if k != "arm"
        }
    assert [p.kwargs["own_starts"] for p in first.participants] == [[1], [2, 3]]
    assert json.loads((harness.out / "block.json").read_bytes()) == {
        "schema": "seatlab.calendar-replica/v1",
        "world": 0,
        "direction": 1,
        "repetition": 0,
        "source": str(harness.task),
        "native_sha256": _NATIVE,
        "semantics_sha256": _SEMANTICS,
    }
    assert stat.S_IMODE(harness.out.stat().st_mode) == 0o700
    assert stat.S_IMODE((harness.out / "block.json").stat().st_mode) == 0o600
    receipts = harness.out / "turns"
    assert {path.name for path in receipts.iterdir()} == {
        f"{turn.replace(':', '_')}.json" for turn in ids
    }
    log = (harness.out / "host/native.log").read_bytes()
    assert log.count(b"host diagnostic\n") == 2


@pytest.mark.parametrize(
    "case", ["invalid_seed", "seed_eof", "native_eof", "changed", "mismatch"]
)
def test_stops_at_first_incomplete_stage(
    harness: _Harness,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    ids, texts = _INITIAL, _SEEDS
    expected = list(_INITIAL)
    end = {"type": "end", "status": "failed", "error": "transport_failed"}
    if case == "invalid_seed":
        texts = ("bad", _SEEDS[1])
        end = {"type": "end", "status": "invalid_seed"}
    elif case == "seed_eof":
        ids, texts, expected = (), (), [_INITIAL[0]]
    elif case == "native_eof":
        expected.append(_PEER)
    elif case == "changed":
        harness.mutate_after_first = True
        ids, texts = (*_INITIAL, _PEER), (*_SEEDS, "bad")
        expected.append(_PEER)
        end["error"] = "source_changed"
    else:
        harness.harbor_checksum = "c" * 64
        end["error"] = "native_checksum_mismatch"
    code, turns, actual = _play(harness, capfd, monkeypatch, ids, texts)
    assert (code, turns, actual) == (int(case != "invalid_seed"), expected, end)
    assert harness.ran == (
        [f"{_RUN}-peer"] if case in ("native_eof", "changed") else []
    )
    assert len(harness.created) == int(case in ("native_eof", "changed", "mismatch"))


def test_does_not_claim_existing_symlink_or_checkout_output(tmp_path: Path) -> None:
    private = tmp_path.resolve() / "private"
    private.mkdir(mode=0o700)
    source = private / "source"
    source.mkdir()
    existing = private / "existing"
    existing.mkdir()
    sentinel = existing / "keep"
    sentinel.write_text("unchanged")
    link = private / "link"
    link.symlink_to(existing, target_is_directory=True)
    tracked = private / "checkout"
    tracked.mkdir()
    (tracked / ".git").write_text("gitdir: worktree")
    for out in (existing, link / "new", tracked / "new", source / "new"):
        with pytest.raises(replica._Failed):
            replica._claim(out, source)
    assert sentinel.read_text() == "unchanged"
    assert not (tracked / "new").exists() and not (existing / "new").exists()
    with pytest.raises(replica._Failed, match="local_docker_required"):
        replica._local_docker_host("ssh://remote")


def test_rejects_nonregistered_coordinates(capfd: pytest.CaptureFixture[str]) -> None:
    args = [
        "play",
        "--tasks",
        "/private/tasks",
        "--out",
        "/private/block",
        "--world",
        "1",
        "--direction",
        "0",
        "--repetition",
        "0",
    ]
    assert replica.main(args) == 1
    captured = capfd.readouterr()
    assert not captured.err
    assert json.loads(captured.out) == {
        "type": "end",
        "status": "failed",
        "error": "invalid_arguments",
    }
