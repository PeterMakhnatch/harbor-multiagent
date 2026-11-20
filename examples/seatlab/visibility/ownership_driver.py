"""Restore one original box game, collect three independent decisions, and play them.

Run this module in a dedicated host process. The demo uses fixed replies without
calling a provider. Only the explicit direct mode binds the direct model key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import stat
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    boxes_run,
    deepseek,
    intent_packet,
    intent_protocol,
    ownership_protocol,
    spend,
)

if TYPE_CHECKING:
    from .ownership_source import Cut

SEATS = ("p1", "p2", "p3")
AGENT = "examples.seatlab.visibility.ownership_seat:OwnershipSeat"
ROLE = "box_open"
METER = "seatlab.box-ownership-direct/v1"
RECEIPT = "seatlab.box-ownership-turn/v1"
_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_PLUGINS = Path("/Applications/Docker.app/Contents/Resources/cli-plugins")


class _Rejected(ValueError):
    """A fixed safe category."""


def _seal(path: Path, record: object) -> None:
    data = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _claim(out: Path, archive: Path, tasks: Path) -> None:
    boxes_run._require_absolute(out)
    for source in (archive, tasks):
        if not source.is_absolute() or source.resolve(strict=True) != source:
            raise _Rejected("physical_source_required")
        if out.is_relative_to(source) or source.is_relative_to(out):
            raise _Rejected("output_source_overlap")
    with boxes_run._directory(out.parent) as parent:
        boxes_run._require_private(os.fstat(parent), directory=True)
        try:
            os.mkdir(out.name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            raise _Rejected("attempt_exists") from None


def _protect_events(trial: Path) -> None:
    # Docker copies the stock event stream with the container's mode. Its
    # private directory already blocks access; restrict the file itself before
    # the external meter reads it. Do not rewrite the verifier's bytes.
    with boxes_run._directory(trial / "verifier") as directory:
        boxes_run._require_private(os.fstat(directory), directory=True)
        fd = os.open(
            "events.jsonl",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_mode & 0o022
            ):
                raise _Rejected("unsafe_native_events")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)


@contextmanager
def _private_log(path: Path) -> Generator[None, None, None]:
    # Native subprocesses inherit OS descriptors, not Python redirections.
    with os.fdopen(
        os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), "wb"
    ) as f:
        sys.stdout.flush()
        sys.stderr.flush()
        saved = os.dup(1), os.dup(2)
        try:
            os.dup2(f.fileno(), 1)
            os.dup2(f.fileno(), 2)
            yield
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                for fd, old in enumerate(saved, 1):
                    os.dup2(old, fd)
                    os.close(old)


def _configure_host(out: Path, direct: bool) -> None:
    host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if direct:
        deepseek.bind_host_key()
        spend.bind_host_spend_dir()
    os.environ.clear()
    if not host.startswith("unix:///") or any(c in host for c in "\r\n\0%?#"):
        raise _Rejected("local_docker_required")
    sock = Path(host.removeprefix("unix://")).resolve(strict=True)
    if (
        not stat.S_ISSOCK(sock.stat().st_mode)
        or shutil.which("docker", path=_PATH) is None
    ):
        raise _Rejected("local_docker_required")
    home, tmp = out / "home", out / "tmp"
    home.mkdir(mode=0o700)
    tmp.mkdir(mode=0o700)
    if (_PLUGINS / "docker-compose").is_file():
        (home / ".docker").mkdir(mode=0o700)
        _seal(home / ".docker/config.json", {"cliPluginsExtraDirs": [str(_PLUGINS)]})
    os.environ.update(
        PATH=_PATH,
        HOME=str(home),
        TMPDIR=str(tmp),
        DOCKER_HOST=f"unix://{sock}",
        COMPOSE_BAKE="false",
        COMPOSE_DISABLE_ENV_FILE="1",
        HARBOR_TELEMETRY="0",
        LITELLM_LOCAL_MODEL_COST_MAP="true",
        PYTHON_DOTENV_DISABLED="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
    )
    sys.dont_write_bytecode = True


def _respond(
    out: Path,
    block: intent_protocol.Block,
    arm: str,
    proposals: intent_protocol.Choices,
    callback: Callable[[str, str], str],
) -> Callable[[str, str], str]:
    run = ownership_protocol.run_id(block, arm)
    world = intent_packet.load()[block.instance]
    pending = iter(
        (
            SEATS[block.seat_index(label)],
            ownership_protocol.prompt(world, block, label, arm, proposals),
        )
        for label in range(3)
    )
    (out / "turns").mkdir(mode=0o700)
    closed = False

    def reply(seat: str, prompt: str) -> str:
        nonlocal closed
        was_closed, closed = closed, True
        if was_closed or next(pending, None) != (seat, prompt):
            raise _Rejected("unregistered_model_request")
        text = callback(seat, prompt)
        if (
            not isinstance(text, str)
            or len(text.encode()) > boxes_run.MAX_RESPONSE_BYTES
        ):
            raise _Rejected("response_unavailable")
        _seal(
            out / "turns" / f"{seat}.json",
            {
                "schema": RECEIPT,
                "run": run,
                "seat": seat,
                "prompt_sha256": _digest(prompt),
                "response_text": text,
                "response_sha256": _digest(text),
            },
        )
        closed = False
        return text

    return reply


def _direct(out: Path, run: str) -> Callable[[str, str], str]:
    (out / "meter").mkdir(mode=0o700)

    def reply(seat: str, prompt: str) -> str:
        call_id = f"{run}-{seat}"
        turn = deepseek.turn(prompt, run_id=call_id, role=ROLE)
        _seal(
            out / "meter" / f"{seat}.json",
            {
                "schema": METER,
                "run": call_id,
                "role": ROLE,
                "seat": seat,
                "prompt_sha256": _digest(prompt),
                "response_sha256": _digest(turn.text),
                "served_model": turn.served_model,
                "input": turn.input_tokens,
                "cached": turn.cache_tokens,
                "output": turn.output_tokens,
            },
        )
        return turn.text

    return reply


def _trial_config(out: Path, task: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(task)},
        "trial_name": out.name,
        "trials_dir": str(out / "trials"),
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": [
            {"seat": s, "import_path": AGENT, "kwargs": {"seat": s}} for s in SEATS
        ],
    }


async def _play(
    out: Path,
    cut: Cut,
    block: intent_protocol.Block,
    arm: str,
    proposals: intent_protocol.Choices,
    callback: Callable[[str, str], str],
) -> None:
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    from . import ownership_seat

    with ownership_seat.bind(
        cut, block, arm, proposals, _respond(out, block, arm, proposals, callback)
    ):
        trial = await Trial.create(
            TrialConfig.model_validate(_trial_config(out, cut.task))
        )
        if trial.task.task_dir != cut.task or trial.task.checksum != cut.task_checksum:
            raise _Rejected("stock_task_mismatch")
        result = await trial.run()
    if (
        result.recorded_exceptions()
        or not result.reward_valid
        or not result.finished_at
    ):
        raise _Rejected("native_trial_failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("demo", "direct"))
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="new arm directory below a private parent",
    )
    blocks = {b.id: b for b in intent_protocol.blocks()}
    parser.add_argument("--block", choices=blocks, required=True)
    parser.add_argument("--arm", choices=("neutral", "owner"), required=True)
    args = parser.parse_args(argv)
    block, arm, out = blocks[args.block], args.arm, args.out
    run = ownership_protocol.run_id(block, arm)
    direct = args.mode == "direct"
    claimed = False
    stage = "claim"
    try:
        if out.name != run:
            raise _Rejected("output_must_match_registered_arm")
        os.umask(0o077)
        _claim(out, args.archive, args.tasks)
        claimed = True
        stage = "host_configuration"
        _configure_host(out, direct)
        with _private_log(out / "native.log"):
            from . import ownership_evidence, ownership_source

            stage = "source_binding"

            cut = ownership_source.prepare(args.archive, args.tasks, block.instance)
            proposals = ownership_protocol.seeds()[block.id]
            if direct:
                deepseek.preflight()
            stage = "native_trial"
            callback = (
                _direct(out, run) if direct else lambda _seat, _prompt: '{"box":1}'
            )
            asyncio.run(_play(out, cut, block, arm, proposals, callback))
            stage = "native_readback"
            _protect_events(out / "trials" / run)
            result = ownership_evidence.read_arm(out, cut, block, arm, proposals)
        if result["status"] not in {"complete", "model_invalid"}:
            raise _Rejected("native_evidence_unavailable")
        print(json.dumps({"run": run, "mode": args.mode, "status": result["status"]}))
        return 0
    except _Rejected as exc:
        print(f"ownership: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - Never export private messages or response text.
        if claimed:
            # Only the exception type and call sites enter this private aid. Do
            # not log exception arguments, which can contain a response body.
            locations = []
            trace = exc.__traceback__
            while trace is not None:
                frame = trace.tb_frame.f_code
                locations.append([frame.co_name, trace.tb_lineno])
                trace = trace.tb_next
            _seal(
                out / "failure.json",
                {"stage": stage, "type": type(exc).__name__, "locations": locations},
            )
        print("ownership: source_or_transport_unavailable", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
