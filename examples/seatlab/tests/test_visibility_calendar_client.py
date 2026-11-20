import json
import stat
import subprocess
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import override

import pytest

from examples.seatlab.visibility import calendar_client, deepseek, spend
from examples.seatlab.visibility.calendar_protocol import Block
from examples.seatlab.visibility.replica_io import MAX_FRAME_BYTES, MAX_TEXT_BYTES

_BLOCK = Block(4, 1, 0)
_RUN = "rx-w04-d1-r0"
_IDS = (
    f"{_RUN}:cal_initial_p1",
    f"{_RUN}:cal_initial_p2",
    f"{_RUN}-peer:cal_recheck",
    f"{_RUN}-peer:cal_decide",
    f"{_RUN}-blind:cal_recheck",
    f"{_RUN}-blind:cal_decide",
)
_LIST = '{"starts":[]}'
_END = b'{"type":"end","status":"complete"}\n'


def _turn(identifier: str, prompt: str = "one seat\nonly") -> bytes:
    return (
        json.dumps({"type": "turn", "id": identifier, "prompt": prompt}).encode()
        + b"\n"
    )


class _RequestPipe(BytesIO):
    @override
    def read(self, size: int | None = -1, /) -> bytes:
        # A child waits for its reply before it can write the next request.
        return self.readline(-1 if size is None else size)


class _ReplyPipe(BytesIO):
    sent = b""

    @override
    def close(self) -> None:
        if not self.closed:
            self.sent = self.getvalue()
        super().close()


class _Child:
    def __init__(self, script: bytes, exit_code: int) -> None:
        self.stdin = _ReplyPipe()
        self.stdout = _RequestPipe(script)
        self.exit_code = exit_code

    def wait(self, *, timeout: float) -> int:
        assert self.stdin.closed
        return self.exit_code

    def replies(self) -> list[dict[str, str]]:
        return [json.loads(line) for line in self.stdin.sent.splitlines()]


def _launch(monkeypatch, script: bytes, exit_code: int = 0) -> _Child:
    child = _Child(script, exit_code)

    def popen(command, *, cwd, env, stdin, stdout, stderr, **kwargs):
        assert command == [
            sys.executable,
            "-B",
            "-m",
            "examples.seatlab.visibility.calendar_replica",
            "play",
            "--tasks",
            str(Path("fresh-tasks").absolute()),
            "--out",
            str(Path("fresh-out").absolute()),
            "--world",
            "4",
            "--direction",
            "1",
            "--repetition",
            "0",
        ]
        assert cwd == Path(calendar_client.__file__).resolve().parents[3]
        assert stdin == stdout == subprocess.PIPE
        assert stderr == subprocess.DEVNULL
        assert set(env) <= {
            "HOME",
            "TMPDIR",
            "PATH",
            "DOCKER_HOST",
            "COMPOSE_DISABLE_ENV_FILE",
            "HARBOR_TELEMETRY",
            "LITELLM_LOCAL_MODEL_COST_MAP",
            "PYTHON_DOTENV_DISABLED",
            "PYTHONNOUSERSITE",
        }
        assert Path(env["HOME"]).is_dir() and not any(Path(env["HOME"]).iterdir())
        assert env["HOME"] == env["TMPDIR"]
        assert "not-child-data" not in env.values()
        return child

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(calendar_client.subprocess, "Popen", popen)
    return child


def _play(respond) -> str:
    return calendar_client.play(Path("fresh-tasks"), Path("fresh-out"), _BLOCK, respond)


def test_prompt_only_roundtrip_and_clean_child(monkeypatch) -> None:
    prompts = [f"scoped prompt {n}\n保留 exactly" for n in range(6)]
    child = _launch(monkeypatch, b"".join(map(_turn, _IDS, prompts)) + _END)
    for name in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "SEATLAB_SPEND_DIR",
        "HTTPS_PROXY",
        "PYTHONPATH",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "MODEL_LEDGER_DIR",
    ):
        monkeypatch.setenv(name, "not-child-data")
    text = '  {"starts":[]}\n'
    seen = []

    def respond(prompt: str) -> str:
        seen.append(prompt)
        return text

    assert _play(respond) == "complete"
    assert seen == prompts
    assert child.replies() == [{"id": identifier, "text": text} for identifier in _IDS]


@pytest.mark.parametrize(
    ("script", "category"),
    [
        (b"", "invalid_replica_frame"),
        (b"x" * (MAX_FRAME_BYTES + 1), "invalid_replica_frame"),
        (_turn(_IDS[0])[:-1], "invalid_replica_frame"),
        (_turn(_IDS[0])[:-1] + b"\r\n", "invalid_replica_frame"),
        (b' {"type":"turn","type":"turn"}\n', "invalid_replica_frame"),
        (b"\xff\n", "invalid_replica_frame"),
        (_turn(_IDS[0], "\ud800"), "invalid_replica_prompt"),
        (_turn(_IDS[0], "é" * (MAX_TEXT_BYTES // 2 + 1)), "invalid_replica_prompt"),
        (_turn(_IDS[0])[:-2] + b',"seat":"p1"}\n', "invalid_replica_turn"),
        (_turn(_IDS[0].replace("w04", "w05")), "invalid_replica_turn"),
        (_turn(_IDS[1]), "invalid_replica_turn"),
        (_END, "invalid_replica_end"),
        (
            b'{"type":"end","status":"failed","error":"local_docker_required"}\n',
            "local_docker_required",
        ),
        (
            b'{"type":"end","status":"failed","error":"not_child_data"}\n',
            "replica_failed",
        ),
    ],
)
def test_reject_malformed_requests_without_callback(
    monkeypatch, script: bytes, category: str
) -> None:
    child = _launch(monkeypatch, script)
    with pytest.raises(calendar_client.CalendarClientError, match=f"^{category}$"):
        _play(lambda _: pytest.fail("unexpected callback"))
    assert child.stdout.closed and not child.replies()


def test_no_duplicate_or_post_end_callback(monkeypatch) -> None:
    child = _launch(monkeypatch, _turn(_IDS[0]) * 2)
    with pytest.raises(
        calendar_client.CalendarClientError, match="invalid_replica_turn"
    ):
        _play(lambda _: _LIST)
    assert child.replies() == [{"id": _IDS[0], "text": _LIST}]

    child = _launch(monkeypatch, b"".join(map(_turn, _IDS)) + _END + _turn(_IDS[0]))
    with pytest.raises(
        calendar_client.CalendarClientError, match="trailing_replica_output"
    ):
        _play(lambda _: _LIST)
    assert len(child.replies()) == 6


def test_no_success_if_replica_exits_nonzero(monkeypatch) -> None:
    _launch(monkeypatch, b"".join(map(_turn, _IDS)) + _END, exit_code=1)
    with pytest.raises(
        calendar_client.CalendarClientError, match="replica_exit_failed"
    ):
        _play(lambda _: _LIST)


def test_only_local_docker_host(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    assert calendar_client._child_env(tmp_path)["DOCKER_HOST"] == (
        "unix:///var/run/docker.sock"
    )
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2375")
    with pytest.raises(
        calendar_client.CalendarClientError, match="local_docker_socket_required"
    ):
        calendar_client._child_env(tmp_path)


def test_invalid_seeds_and_rechecks_are_forwarded_without_repairs(monkeypatch) -> None:
    seed_end = b'{"type":"end","status":"invalid_seed"}\n'
    child = _launch(monkeypatch, b"".join(map(_turn, _IDS[:2])) + seed_end)
    replies = iter(("not a list", _LIST))
    assert _play(lambda _: next(replies)) == "invalid_seed"
    assert child.replies()[0]["text"] == "not a list"
    assert len(child.replies()) == 2

    child = _launch(monkeypatch, b"".join(map(_turn, _IDS[:3] + _IDS[4:])) + _END)
    replies = iter((_LIST, _LIST, "not a list", _LIST, '{"slot":9}'))
    assert _play(lambda _: next(replies)) == "complete"
    assert child.replies()[2] == {"id": _IDS[2], "text": "not a list"}
    assert [reply["id"] for reply in child.replies()] == list(_IDS[:3] + _IDS[4:])


def test_callback_failure_closes_pipe_without_reply(monkeypatch) -> None:
    child = _launch(monkeypatch, _turn(_IDS[0]))

    def unavailable(_prompt: str) -> str:
        raise ValueError("private provider failure")

    with pytest.raises(
        calendar_client.CalendarClientError, match="^model_callback_failed$"
    ):
        _play(unavailable)
    assert child.stdin.closed and not child.replies()


def test_demo_fixed_replies(monkeypatch) -> None:
    child = _launch(monkeypatch, b"".join(map(_turn, _IDS)) + _END)
    assert (
        calendar_client.main(
            [
                "demo",
                "--tasks",
                "fresh-tasks",
                "--out",
                "fresh-out",
                "--world",
                "4",
                "--direction",
                "1",
                "--repetition",
                "0",
            ]
        )
        == 0
    )
    assert [reply["text"] for reply in child.replies()] == [
        _LIST,
        _LIST,
        _LIST,
        '{"slot":0}',
        _LIST,
        '{"slot":0}',
    ]


def test_direct_meter_first_attempt_only_and_without_raw_text(
    monkeypatch, tmp_path: Path
) -> None:
    parent = tmp_path.resolve() / "private"
    parent.mkdir(mode=0o700)
    native, meter = parent / "native", parent / "meter"
    calls = []

    def turn(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        calls.append((prompt, run_id, role))
        if role == "cal_initial_p2":
            raise deepseek.SeatUnavailable("private provider text")
        return deepseek.SeatTurn(_LIST, "deepseek-flash", 20, 3, 5)

    monkeypatch.setattr(deepseek, "turn", turn)
    with calendar_client._meter_dir(native, meter) as directory:
        assert (
            calendar_client._direct_turn(directory, _IDS[0], "private prompt") == _LIST
        )
        with pytest.raises(FileExistsError):
            calendar_client._direct_turn(directory, _IDS[0], "private prompt")
        with pytest.raises(deepseek.SeatUnavailable):
            calendar_client._direct_turn(directory, _IDS[1], "second prompt")

    assert calls == [
        ("private prompt", _RUN, "cal_initial_p1"),
        ("second prompt", _RUN, "cal_initial_p2"),
    ]
    assert stat.S_IMODE(meter.stat().st_mode) == 0o700 and not native.exists()
    receipt = meter / f"{_RUN}_cal_initial_p1.json"
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert json.loads(receipt.read_text()) == {
        "schema": "seatlab.calendar-direct-meter/v1",
        "attempt": 1,
        "status": "success",
        "run": _RUN,
        "role": "cal_initial_p1",
        "prompt_sha256": sha256(b"private prompt").hexdigest(),
        "response_sha256": sha256(_LIST.encode()).hexdigest(),
        "served_model": "deepseek-flash",
        "input_tokens": 20,
        "cache_tokens": 3,
        "output_tokens": 5,
    }
    failed = (meter / f"{_RUN}_cal_initial_p2.json").read_text()
    assert "second prompt" not in failed and "private provider text" not in failed
    assert json.loads(failed)["status"] == "failed"
    with (
        pytest.raises(
            calendar_client.CalendarClientError, match="private_meter_required"
        ),
        calendar_client._meter_dir(native, meter),
    ):
        pytest.fail("meter must be fresh")
    symlink = parent / "link"
    symlink.symlink_to(parent / "absent")
    with (
        pytest.raises(
            calendar_client.CalendarClientError, match="private_meter_required"
        ),
        calendar_client._meter_dir(native, symlink),
    ):
        pytest.fail("meter must not be a symlink")


def test_direct_refuses_missing_ledger_without_initializing_it(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(tmp_path.resolve()))
    monkeypatch.setattr(
        spend, "initialize", lambda *_: pytest.fail("budget initialization")
    )
    with pytest.raises(
        calendar_client.CalendarClientError, match="direct_spend_required"
    ):
        calendar_client._bind_direct()
    assert not (tmp_path / "direct-flash.sqlite3").exists()
