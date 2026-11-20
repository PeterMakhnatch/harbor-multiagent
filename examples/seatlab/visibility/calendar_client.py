"""Run one fresh calendar replica with a prompt-only callback.

The demo uses fixed replies and does not call a model. For the explicit paid
adapter, start `keys run -- <venv-python> -B -m
examples.seatlab.visibility.calendar_client direct ... --meter <fresh-dir>`
with an existing, initialized SEATLAB_SPEND_DIR. Only this parent uses the key.
"""

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, BinaryIO, cast

from . import calendar_protocol
from .replica_io import MAX_FRAME_BYTES, MAX_TEXT_BYTES, read_frame

_REPO = Path(__file__).resolve().parents[3]
_NATIVE = "examples.seatlab.visibility.calendar_replica"
_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_IDLE_TIMEOUT = 1800
_TEARDOWN_TIMEOUT = 180
_EXIT_TIMEOUT = 10
_METER_SCHEMA = "seatlab.calendar-direct-meter/v1"
_ACTIONABLE_NATIVE_ERRORS = frozenset(
    {
        "invalid_arguments",
        "local_docker_required",
        "output_exists",
        "output_overlaps_source",
        "private_output_parent_required",
        "private_physical_path_required",
        "private_source_required",
        "source_changed",
        "source_invalid",
    }
)


class CalendarClientError(RuntimeError):
    """A safe, fixed category with no task, model, or account data."""


def _child_env(home: Path) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "TMPDIR": str(home),
        "PATH": _PATH,
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "HARBOR_TELEMETRY": "0",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if (host := os.environ.get("DOCKER_HOST")) is not None:
        if not host.startswith("unix:///") or any(c in host for c in "\r\n\0%?#"):
            raise CalendarClientError("local_docker_socket_required")
        env["DOCKER_HOST"] = host
    return env


def _constant(_value: str) -> object:
    raise ValueError("invalid constant")


def _record(source: BinaryIO) -> dict[str, Any]:
    try:
        line = read_frame(source, monotonic() + _IDLE_TIMEOUT)
    except TimeoutError:
        raise CalendarClientError("replica_timeout") from None
    except ValueError:
        raise CalendarClientError("invalid_replica_frame") from None
    try:
        value = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=calendar_protocol._unique_object,
            parse_constant=_constant,
        )
    except (ValueError, RecursionError):
        raise CalendarClientError("invalid_replica_frame") from None
    if not isinstance(value, dict):
        raise CalendarClientError("invalid_replica_frame")
    return value


def _text_bytes(value: object, error: str) -> bytes:
    if type(value) is not str or len(value) > MAX_TEXT_BYTES:
        raise CalendarClientError(error)
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise CalendarClientError(error) from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise CalendarClientError(error)
    return encoded


def _request_plan(block: calendar_protocol.Block) -> deque[str]:
    if (
        type(block.world) is not int
        or type(block.direction) is not int
        or type(block.repetition) is not int
        or block not in calendar_protocol.blocks()
    ):
        raise CalendarClientError("invalid_block")
    run = f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}"
    return deque(
        [f"{run}:cal_initial_p1", f"{run}:cal_initial_p2"]
        + [
            f"{run}-{arm}:{role}"
            for arm in block.arms
            for role in ("cal_recheck", "cal_decide")
        ]
    )


def _exchange(
    source: BinaryIO,
    sink: BinaryIO,
    pending: deque[str],
    respond: Callable[[str, str], str],
) -> str:
    expected_status = "complete"
    while True:
        record = _record(source)
        if record.get("type") == "end":
            status = record.get("status")
            if status == "failed":
                error = record.get("error")
                if record.keys() == {"type", "status", "error"} and (
                    isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error)
                ):
                    category = (
                        error
                        if error in _ACTIONABLE_NATIVE_ERRORS
                        else "replica_failed"
                    )
                    raise CalendarClientError(category)
            elif (
                record.keys() == {"type", "status"}
                and not pending
                and status == expected_status
            ):
                return expected_status
            raise CalendarClientError("invalid_replica_end")

        if (
            record.keys() != {"type", "id", "prompt"}
            or record["type"] != "turn"
            or not pending
            or record["id"] != pending[0]
        ):
            raise CalendarClientError("invalid_replica_turn")
        prompt = record["prompt"]
        if not _text_bytes(prompt, "invalid_replica_prompt"):
            raise CalendarClientError("invalid_replica_prompt")
        identifier = pending.popleft()
        # A caller can raise any exception, including private provider data.
        try:
            text = respond(identifier, prompt)
        except Exception:  # noqa: BLE001
            raise CalendarClientError("model_callback_failed") from None
        _text_bytes(text, "invalid_callback_text")
        reply = (
            json.dumps(
                {"id": identifier, "text": text},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(reply) > MAX_FRAME_BYTES:
            raise CalendarClientError("invalid_callback_text")
        sink.write(reply)
        sink.flush()

        # Forward grammar-invalid text unchanged. Mirror only its effect on the
        # remaining turn IDs, not the model action or the stock verifier result.
        role = identifier.split(":", 1)[1]
        if role != "cal_decide":
            try:
                calendar_protocol.decode_list(text)
            except ValueError:
                if role == "cal_recheck":
                    pending.popleft()  # This arm has no decision turn.
                else:
                    expected_status = "invalid_seed"
            if role == "cal_initial_p2" and expected_status == "invalid_seed":
                pending.clear()


def _close_input(child: subprocess.Popen[bytes]) -> None:
    if child.stdin is not None:
        try:
            child.stdin.close()
        except BrokenPipeError:
            pass


def _shutdown(child: subprocess.Popen[bytes]) -> None:
    try:
        _close_input(child)
        # EOF releases an active relay so the stock trial can tear down first.
        try:
            child.wait(timeout=_TEARDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=_EXIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    finally:
        if child.stdout is not None:
            child.stdout.close()


def _drive(
    tasks: Path,
    out: Path,
    block: calendar_protocol.Block,
    respond: Callable[[str, str], str],
) -> str:
    pending = _request_plan(block)
    try:
        command = [
            sys.executable,
            "-B",
            "-m",
            _NATIVE,
            "play",
            "--tasks",
            str(tasks.absolute()),
            "--out",
            str(out.absolute()),
            "--world",
            str(block.world),
            "--direction",
            str(block.direction),
            "--repetition",
            str(block.repetition),
        ]
        with tempfile.TemporaryDirectory(
            prefix="seatlab-calendar-client-", dir="/tmp"
        ) as home:
            child = subprocess.Popen(
                command,
                cwd=_REPO,
                env=_child_env(Path(home)),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                umask=0o077,
            )
            try:
                if child.stdout is None or child.stdin is None:
                    raise CalendarClientError("replica_pipes_unavailable")
                # Popen in binary mode exposes these pipes as IO[bytes].
                status = _exchange(
                    cast(BinaryIO, child.stdout),
                    cast(BinaryIO, child.stdin),
                    pending,
                    respond,
                )
                _close_input(child)
                if child.wait(timeout=_EXIT_TIMEOUT) != 0:
                    raise CalendarClientError("replica_exit_failed")
                if child.stdout.read(1):
                    raise CalendarClientError("trailing_replica_output")
                return status
            finally:
                _shutdown(child)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CalendarClientError("replica_io_failed") from None


def play(
    tasks: Path,
    out: Path,
    block: calendar_protocol.Block,
    respond: Callable[[str], str],
) -> str:
    """Return 'complete' or 'invalid_seed'; raise CalendarClientError on failure.

    Call `respond(prompt)` at most six times. Send its text unchanged, including
    invalid model answers. Do not pass identifiers, retry, or repair answers.
    The callback must return within the native relay's turn deadline.
    """
    return _drive(tasks, out, block, lambda _identifier, prompt: respond(prompt))


def _demo(identifier: str, _prompt: str) -> str:
    return '{"slot":0}' if identifier.endswith(":cal_decide") else '{"starts":[]}'


@contextmanager
def _meter_dir(out: Path, meter: Path) -> Generator[int, None, None]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        out, meter = out.absolute(), meter.absolute()
        parent = out.parent
        if (
            meter.parent != parent
            or meter == out
            or any(
                (ancestor / ".git").exists() for ancestor in (parent, *parent.parents)
            )
            or out.resolve() != out
            or meter.resolve() != meter
            or out.exists()
        ):
            raise CalendarClientError("private_meter_required")
        fd = os.open(parent, flags)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CalendarClientError("private_meter_required")
            os.mkdir(meter.name, mode=0o700, dir_fd=fd)
            directory = os.open(meter.name, flags, dir_fd=fd)
            try:
                info = os.fstat(directory)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise CalendarClientError("private_meter_required")
                yield directory
            finally:
                os.close(directory)
        finally:
            os.close(fd)
    except CalendarClientError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise CalendarClientError("private_meter_required") from None


def _bind_direct() -> None:
    from . import deepseek, spend

    if not os.environ.get("DEEPSEEK_API_KEY", ""):
        raise CalendarClientError("direct_key_required")
    try:
        ledger = Path(os.environ.get("SEATLAB_SPEND_DIR", "")) / "direct-flash.sqlite3"
        if not ledger.is_absolute() or ledger.resolve() != ledger:
            raise ValueError("invalid ledger path")
        info = ledger.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("invalid ledger file")
        spend.bind_host_spend_dir()
    except (OSError, ValueError, RuntimeError):
        raise CalendarClientError("direct_spend_required") from None
    try:
        deepseek.bind_host_key()
    except deepseek.SeatUnavailable:
        raise CalendarClientError("direct_key_required") from None


def _direct_turn(directory: int, identifier: str, prompt: str) -> str:
    """Seal a private, independent attempt before calling the budgeted adapter.

    `<run>_<role>.json` has schema, attempt (always 1), status, run, role,
    prompt_sha256, response_sha256, served_model, input_tokens, cache_tokens,
    and output_tokens. Input includes cache. On failure, the response hash,
    model, and usage are null; consult the spend ledger for any settled call.
    """
    from . import deepseek

    run, role = identifier.split(":", 1)
    receipt = {
        "schema": _METER_SCHEMA,
        "attempt": 1,
        "status": "failed",
        "run": run,
        "role": role,
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "response_sha256": None,
        "served_model": None,
        "input_tokens": None,
        "cache_tokens": None,
        "output_tokens": None,
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    name = f"{run}_{role}.json"
    with os.fdopen(
        os.open(name, flags, 0o600, dir_fd=directory), "w", encoding="utf-8"
    ) as file:
        os.fchmod(file.fileno(), 0o600)
        try:
            turn = deepseek.turn(prompt, run_id=run, role=role)
            response = _text_bytes(turn.text, "invalid_callback_text")
            receipt.update(
                status="success",
                response_sha256=sha256(response).hexdigest(),
                served_model=turn.served_model,
                input_tokens=turn.input_tokens,
                cache_tokens=turn.cache_tokens,
                output_tokens=turn.output_tokens,
            )
        finally:
            file.write(
                json.dumps(receipt, allow_nan=False, separators=(",", ":")) + "\n"
            )
    return turn.text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="mode", required=True)
    for mode in ("demo", "direct"):
        command = commands.add_parser(mode, allow_abbrev=False)
        command.add_argument("--tasks", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--world", type=int, required=True)
        command.add_argument("--direction", type=int, required=True)
        command.add_argument("--repetition", type=int, required=True)
        if mode == "direct":
            command.add_argument("--meter", type=Path, required=True)
    args = parser.parse_args(argv)
    block = calendar_protocol.Block(args.world, args.direction, args.repetition)
    try:
        if args.mode == "demo":
            status = _drive(args.tasks, args.out, block, _demo)
        else:
            _bind_direct()
            with _meter_dir(args.out, args.meter) as directory:
                status = _drive(
                    args.tasks,
                    args.out,
                    block,
                    lambda identifier, prompt: _direct_turn(
                        directory, identifier, prompt
                    ),
                )
    except CalendarClientError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("calendar_client_interrupted", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
