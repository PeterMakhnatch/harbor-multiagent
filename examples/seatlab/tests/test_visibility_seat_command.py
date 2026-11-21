import fcntl
import json
import sys
import time
from pathlib import Path

import pytest

from examples.seatlab.visibility.seat_command import CommandSeat, load_commands


def _filter(body: str, *args: str) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-c",
        "import json, sys\nturn = json.loads(sys.stdin.buffer.readline())\n" + body,
        *args,
    ]


def _config(
    path: Path, argv: list[str], review_env: list[str], decide_env: list[str]
) -> None:
    path.write_text(
        json.dumps(
            {
                "reviewer": {"argv": argv, "env": review_env},
                "decider": {"argv": argv, "env": decide_env},
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("text", ["  A\n\t café 😃 \r\n", "\0" * 8192])
def test_preserves_prompt_and_reply(text: str) -> None:
    seat = CommandSeat(
        _filter(
            'assert turn == {"schema": "seatlab.seat-turn/v1", "prompt": turn["prompt"]}\n'
            'assert sys.stdin.buffer.read() == b""\n'
            'print(json.dumps({"text": turn["prompt"]}))\n'
        )
    )
    assert seat(text) == text


def test_private_home_and_fresh_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEATLAB_UNLISTED_FLAG", "parent-only")
    monkeypatch.setenv("HTTPS_PROXY", "parent-only")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    seat = CommandSeat(
        _filter(
            "import os\n"
            "from pathlib import Path\n"
            'assert os.path.samefile(os.getcwd(), os.environ["HOME"])\n'
            'assert os.stat(".").st_mode & 0o077 == 0\n'
            'assert "SEATLAB_UNLISTED_FLAG" not in os.environ\n'
            'assert "HTTPS_PROXY" not in os.environ\n'
            'assert os.environ["LANGSMITH_TRACING"] == "false"\n'
            'marker = Path("state")\n'
            "assert not marker.exists()\n"
            "marker.touch()\n"
            'print(json.dumps({"text": os.getcwd()}))\n'
        )
    )
    first = Path(seat("A"))
    second = Path(seat("A"))
    assert first != second
    assert not first.exists() and not second.exists()


def test_each_role_gets_only_its_named_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEATLAB_REVIEW_FLAG", "review")
    monkeypatch.setenv("SEATLAB_DECIDE_FLAG", "decide")
    argv = _filter(
        "import os\n"
        'flags = [os.environ[name] for name in ("SEATLAB_REVIEW_FLAG", "SEATLAB_DECIDE_FLAG") if name in os.environ]\n'
        'print(json.dumps({"text": ",".join(flags)}))\n'
    )
    path = tmp_path / "commands.json"
    _config(path, argv, ["SEATLAB_REVIEW_FLAG"], ["SEATLAB_DECIDE_FLAG"])
    reviewer, decider = load_commands(path)
    assert reviewer("A") == "review"
    assert decider("A") == "decide"


def test_missing_host_environment_has_fixed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEATLAB_UNSET_FLAG", raising=False)
    path = tmp_path / "commands.json"
    _config(path, [sys.executable], ["SEATLAB_UNSET_FLAG"], [])
    with pytest.raises(
        ValueError, match="^required seat command environment variable is missing$"
    ):
        load_commands(path)


@pytest.mark.parametrize(
    "name",
    ["PATH", "HOME", "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "bad-name"],
)
def test_environment_cannot_override_process_controls(name: str) -> None:
    with pytest.raises(ValueError):
        CommandSeat([sys.executable], environ={name: "unused"})


@pytest.mark.parametrize(
    "template",
    [
        '{{"reviewer": {role}, "decider": {role}, "extra": null}}',
        '{{"reviewer": {role}, "reviewer": {role}, "decider": {role}}}',
        '{{"reviewer": {{"argv": {argv}, "env": {{"FLAG": "unused"}}}}, "decider": {role}}}',
        '{{"reviewer": {{"argv": ["python"], "env": []}}, "decider": {role}}}',
        '{{"reviewer": {{"argv": {argv}, "env": [NaN]}}, "decider": {role}}}',
    ],
)
def test_rejects_nonconforming_command_configs(tmp_path: Path, template: str) -> None:
    path = tmp_path / "commands.json"
    argv = [sys.executable]
    role = {"argv": argv, "env": []}
    path.write_text(
        template.format(argv=json.dumps(argv), role=json.dumps(role)), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_commands(path)


@pytest.mark.parametrize(
    "output",
    [
        b'{"text":"A"}',
        b'{"text":"A"}\n ',
        b'{"text":"A"}\n{"text":"A"}\n',
        b'{"text":"A","\\u0074ext":"B"}\n',
        b'{"text":"A","extra":0}\n',
        b'{"text":true}\n',
        b'{"text":NaN}\n',
        b'{"text":"\xff"}\n',
        b'{"text":"\\ud800"}\n',
    ],
)
def test_rejects_malformed_replies(output: bytes) -> None:
    seat = CommandSeat(_filter(f"sys.stdout.buffer.write({output!r})\n"))
    with pytest.raises(ValueError, match="^invalid seat command reply$"):
        seat("A")


def test_nonzero_exit_rejects_reply_without_exposing_stderr() -> None:
    seat = CommandSeat(
        _filter(
            'sys.stderr.write("private diagnostic")\n'
            'print(json.dumps({"text": "A"}))\n'
            "sys.exit(2)\n"
        )
    )
    with pytest.raises(ValueError, match="^seat command failed$"):
        seat("A")


def test_reply_limit_counts_utf8_bytes() -> None:
    seat = CommandSeat(_filter('print(json.dumps({"text": "é" * 4097}))\n'))
    with pytest.raises(ValueError, match="^seat command reply is too large$"):
        seat("A")


def test_rejects_oversized_stream_before_eof() -> None:
    seat = CommandSeat(
        _filter('import time\nsys.stdout.buffer.write(b"x" * 262144)\ntime.sleep(5)\n'),
        timeout=2.0,
    )
    with pytest.raises(ValueError, match="^seat command reply is too large$"):
        seat("A")


def test_late_stderr_does_not_block_a_valid_turn() -> None:
    seat = CommandSeat(
        _filter(
            "import os\n"
            'print(json.dumps({"text": "A"}), flush=True)\n'
            "os.close(sys.stdout.fileno())\n"
            'sys.stderr.buffer.write(b"diagnostic" * 65536)\n'
        ),
        timeout=2.0,
    )
    assert seat("A") == "A"


def test_timeout_covers_blocked_request_write() -> None:
    seat = CommandSeat(
        [sys.executable, "-I", "-c", "import time; time.sleep(5)"], timeout=0.5
    )
    with pytest.raises(TimeoutError, match="^seat command timed out$"):
        seat("A" * (1 << 20))


def test_timeout_stops_descendants_and_removes_workspace(tmp_path: Path) -> None:
    locked = tmp_path / "lock"
    seat = CommandSeat(
        _filter(
            "import fcntl, os, time\n"
            "if os.fork() == 0:\n"
            '    with open(sys.argv[1], "w") as marker:\n'
            "        fcntl.flock(marker, fcntl.LOCK_EX)\n"
            "        marker.write(os.getcwd())\n"
            "        marker.flush()\n"
            "        time.sleep(6)\n"
            "    os._exit(0)\n"
            'print(json.dumps({"text": "A"}), flush=True)\n'
            "time.sleep(6)\n",
            str(locked),
        ),
        timeout=2.0,
    )
    with pytest.raises(TimeoutError):
        seat("A")
    with locked.open() as marker:
        cwd = marker.read()
        assert cwd and not Path(cwd).exists()
        # SIGKILL is asynchronous for the descendant. Its file lock must release.
        deadline = time.monotonic() + 1.0
        while True:
            try:
                fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    pytest.fail(
                        "seat descendant still holds its file lock after timeout"
                    )
                time.sleep(0.01)
