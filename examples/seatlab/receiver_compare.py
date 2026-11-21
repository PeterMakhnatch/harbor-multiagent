from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Literal, Never, TypedDict, override

_SCHEMA = "seatlab.dispatch-receiver-comparison/v1"
_SCOPE = "conditional canonical receiver replay, not team reward"
_MAX_COMMAND_CONFIG_BYTES = 64 * 1024


class _Receiver(TypedDict):
    state: Literal["valid", "timeout", "command_failure", "invalid_decision"]
    response_sha256: str | None
    counts: dict[str, int] | None


def _record(turn: Future[str], score: Callable[[str], dict[str, int]]) -> _Receiver:
    try:
        text = turn.result()
    except TimeoutError:
        return {"state": "timeout", "response_sha256": None, "counts": None}
    except (OSError, ValueError):
        return {"state": "command_failure", "response_sha256": None, "counts": None}

    digest = sha256(text.encode("utf-8")).hexdigest()
    try:
        counts = score(text)
    except ValueError:
        return {"state": "invalid_decision", "response_sha256": digest, "counts": None}
    return {"state": "valid", "response_sha256": digest, "counts": counts}


def _publish(directory: int, result: object) -> None:
    data = (
        json.dumps(result, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = ".result.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
        dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o400)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(
            temporary,
            "result.json",
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
    finally:
        os.unlink(temporary, dir_fd=directory)


def compare(trial: Path, config: Path, out: Path) -> bool:
    from .receiver_input import load
    from .visibility.seat_command import parse_commands

    out.mkdir(mode=0o700)
    directory = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(directory, 0o700)
        source = load(trial)
        with config.open("rb") as stream:
            raw_config = stream.read(_MAX_COMMAND_CONFIG_BYTES + 1)
        if len(raw_config) > _MAX_COMMAND_CONFIG_BYTES:
            raise ValueError("seat command config is too large")
        reviewer, decider = parse_commands(raw_config)
        prompt = source.prompt
        with ThreadPoolExecutor(max_workers=2) as pool:
            reviewer_turn = pool.submit(reviewer, prompt)
            decider_turn = pool.submit(decider, prompt)
        reviewer_result = _record(reviewer_turn, source.score)
        decider_result = _record(decider_turn, source.score)
        reviewer_counts = reviewer_result["counts"]
        decider_counts = decider_result["counts"]
        difference = (
            decider_counts["optimal"] - reviewer_counts["optimal"]
            if reviewer_counts is not None and decider_counts is not None
            else None
        )
        _publish(
            directory,
            {
                "schema": _SCHEMA,
                "scope": _SCOPE,
                "source": source.metadata,
                "command_config_sha256": sha256(raw_config).hexdigest(),
                "receivers": {
                    "reviewer": reviewer_result,
                    "decider": decider_result,
                },
                "difference_optimal": difference,
            },
        )
        return difference is not None
    finally:
        os.close(directory)


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        self.exit(1, "Receiver comparison: invalid arguments.\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m examples.seatlab.receiver_compare",
        description=_SCOPE,
        allow_abbrev=False,
    )
    parser.add_argument("--trial", required=True, type=Path, help="stored native trial")
    parser.add_argument(
        "--config", required=True, type=Path, help="trusted command config"
    )
    parser.add_argument("--out", required=True, type=Path, help="new private directory")
    try:
        args = parser.parse_args(argv)
        complete = compare(args.trial, args.config, args.out)
        print(
            "Receiver comparison complete."
            if complete
            else "Receiver comparison incomplete."
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, KeyboardInterrupt):
        print("Receiver comparison failed.", file=sys.stderr)
        return 1
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
