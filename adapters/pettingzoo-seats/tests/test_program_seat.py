"""Host program-seat tests. The seat double only implements the stock exec surface."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harbor.environments.base import ExecResult  # noqa: E402
from harbor.environments.seat import SeatEnvironment  # noqa: E402
from harbor.models.agent.context import AgentContext  # noqa: E402

from program_seat import ProgramSeat  # noqa: E402

VIEW = {"observation": 3, "legal_actions": [0, 1, 2]}


class Mailbox(SeatEnvironment):
    def __init__(self) -> None:
        self.service = "p0"
        self.commands: list[str] = []
        self.commits: list[dict[str, object]] = []
        self.frames: list[dict[str, object] | None] = []

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        del cwd, env, timeout_sec, user
        self.commands.append(command)
        if command.startswith("mkdir"):
            return ExecResult(return_code=0, stdout="")
        if " next " in command:
            if not self.frames:
                return ExecResult(return_code=3, stdout="")
            frame = self.frames.pop(0)
            if frame is None:
                return ExecResult(return_code=3, stdout="")
            return ExecResult(return_code=0, stdout=json.dumps(frame) + "\n")
        if command.endswith("commit"):
            return ExecResult(return_code=0, stdout="")
        return ExecResult(return_code=1, stdout="")

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        if target_path.endswith("commit.json"):
            self.commits.append(
                json.loads(Path(source_path).read_text(encoding="utf-8"))
            )


def _program(path: Path, body: str) -> list[str]:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return [str(path)]


def _agent(tmp_path: Path, argv: list[str]) -> ProgramSeat:
    return ProgramSeat(tmp_path / "logs", argv=argv)


def _digest(view: dict[str, object]) -> str:
    from program_seat import _WIRE

    return _WIRE.canonical_digest(view)


def test_rejects_model_env_and_relative_program(tmp_path: Path) -> None:
    script = _program(tmp_path / "ok.py", "raise SystemExit(0)\n")
    with pytest.raises(ValueError, match="model_name"):
        ProgramSeat(tmp_path / "logs", "some-model", argv=script)
    with pytest.raises(ValueError, match="extra_env"):
        ProgramSeat(
            tmp_path / "logs", argv=script, extra_env={"OPENAI_API_KEY": "secret"}
        )
    with pytest.raises(ValueError, match="absolute"):
        ProgramSeat(tmp_path / "logs", argv=["policy"])


def test_stale_frame_is_not_a_new_turn(tmp_path: Path) -> None:
    seen = tmp_path / "stdin.txt"
    argv = _program(
        tmp_path / "policy.py",
        "import json, sys\nfrom pathlib import Path\nraw = sys.stdin.read()\n"
        f"Path({str(seen)!r}).write_text(raw)\n"
        "print(json.dumps({'action': json.loads(raw)['legal_actions'][0]}))\n",
    )
    seat = Mailbox()
    frame = {"step": 1, "view": VIEW, "view_sha256": _digest(VIEW)}
    seat.frames = [frame, frame, {"done": True}]
    context = AgentContext()
    asyncio.run(
        _agent(tmp_path, argv).run("do not leak this instruction", seat, context)
    )
    assert json.loads(seen.read_text(encoding="utf-8")) == VIEW
    assert "instruction" not in seen.read_text(encoding="utf-8")
    assert seat.commits == [
        {"step": 1, "view_sha256": frame["view_sha256"], "action": 0}
    ]
    assert context.metadata == {
        "schema": "seatlab.program-seat/v1",
        "role": "p0",
        "program_calls": 1,
        "stage": "done",
    }
    assert context.n_input_tokens is None and context.cost_usd is None


def test_bad_output_does_not_commit(tmp_path: Path) -> None:
    argv = _program(
        tmp_path / "bad.py", 'print(\'{"action": true}\\n{"action": 0}\')\n'
    )
    seat = Mailbox()
    seat.frames = [{"step": 1, "view": VIEW, "view_sha256": _digest(VIEW)}]
    context = AgentContext()
    with pytest.raises(ValueError):
        asyncio.run(_agent(tmp_path, argv).run("", seat, context))
    assert seat.commits == []
    assert context.metadata["stage"] == "failed"
    assert context.metadata["program_calls"] == 1


def test_program_env_is_fresh(tmp_path: Path) -> None:
    recorded = tmp_path / "env.json"
    argv = _program(
        tmp_path / "env.py",
        "import json, os, sys\nfrom pathlib import Path\n"
        f"Path({str(recorded)!r}).write_text(json.dumps("
        "{'env': dict(os.environ), 'argv': sys.argv, 'stdin': sys.stdin.read()}))\n"
        "print(json.dumps({'action': 0}))\n",
    )
    previous = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "secret"
    try:
        seat = Mailbox()
        seat.frames = [
            {"step": 2, "view": VIEW, "view_sha256": _digest(VIEW)},
            {"done": True},
        ]
        asyncio.run(
            _agent(tmp_path, argv).run("secret instruction", seat, AgentContext())
        )
        observed = json.loads(recorded.read_text(encoding="utf-8"))
    finally:
        if previous is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous
    assert "OPENAI_API_KEY" not in observed["env"]
    assert observed["env"]["HOME"] != str(Path.home())
    assert observed["env"]["PATH"] == "/usr/bin:/bin"
    assert observed["argv"] == argv
    assert json.loads(observed["stdin"]) == VIEW
