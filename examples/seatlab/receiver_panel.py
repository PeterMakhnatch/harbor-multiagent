from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Literal, NoReturn, TypedDict

from . import panel_sources

_MAX_CASES = 32
_MAX_REPLY_BYTES = 64 * 1024
_MAX_LINE_BYTES = 6 * _MAX_REPLY_BYTES + 1024
_WORKER_FIELDS = {"id", "prompt_sha256", "reply"}


class _Row(TypedDict):
    id: str
    task: str
    world: int
    trial_sha256: str
    prompt_sha256: str
    state: Literal["missing", "invalid_reply", "valid"]
    reply_sha256: str | None
    correct: int | None
    units: int


def _shard(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", value)
    if match is not None:
        index, count = map(int, match.groups())
        if 0 <= index < count <= _MAX_CASES:
            return index, count
    raise argparse.ArgumentTypeError(
        "expected INDEX/COUNT with 0 <= INDEX < COUNT <= 32"
    )


def _print_json(value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def _emit(cases: dict[str, panel_sources.Case], shard: tuple[int, int]) -> None:
    index, count = shard
    for position, case in enumerate(cases.values()):
        if position % count == index:
            _print_json(
                {
                    "id": case.id,
                    "task": case.task,
                    "world": case.world,
                    "trial_sha256": case.trial_sha256,
                    "prompt_sha256": case.prompt_sha256,
                    "messages": [
                        {"role": "system", "content": case.system},
                        {"role": "user", "content": case.prompt},
                    ],
                }
            )


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _worker_line(line: bytes) -> tuple[str, str, str]:
    if len(line) > _MAX_LINE_BYTES:
        raise ValueError("worker line exceeds the byte limit")
    if not line.endswith(b"\n"):
        raise ValueError("worker line must end with a newline")
    line = line[:-2] if line.endswith(b"\r\n") else line[:-1]
    if b"\r" in line:
        raise ValueError("carriage return inside a worker line")
    record = json.loads(
        line.decode("utf-8"),
        object_pairs_hook=_unique_keys,
        parse_constant=_reject_constant,
    )
    if not isinstance(record, dict) or record.keys() != _WORKER_FIELDS:
        raise ValueError("expected exactly the id, prompt_sha256, and reply fields")
    case_id, commitment, reply = record["id"], record["prompt_sha256"], record["reply"]
    if not all(isinstance(value, str) for value in (case_id, commitment, reply)):
        raise ValueError("id, prompt_sha256, and reply must be strings")
    return case_id, commitment, reply


def _open_nonblocking(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NONBLOCK)


def _read_replies(
    files: list[Path], cases: dict[str, panel_sources.Case]
) -> dict[str, tuple[str, str]]:
    replies: dict[str, tuple[str, str]] = {}
    for path in files:
        with open(path, "rb", opener=_open_nonblocking) as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError(f"{str(path)!r}: expected a regular file")
            for number in range(1, len(cases) + 2):
                line = source.readline(_MAX_LINE_BYTES + 1)
                if not line:
                    break
                try:
                    case_id, commitment, reply = _worker_line(line)
                    if case_id not in cases:
                        raise ValueError("unknown case ID")
                    if case_id in replies:
                        raise ValueError("duplicate case ID")
                    if commitment != cases[case_id].prompt_sha256:
                        raise ValueError("prompt SHA-256 does not match the case")
                    encoded = reply.encode("utf-8")
                    if len(encoded) > _MAX_REPLY_BYTES:
                        raise ValueError("reply exceeds 64 KiB in UTF-8")
                except (ValueError, RecursionError) as exc:
                    raise ValueError(f"{str(path)!r}:{number}: {exc}") from exc
                replies[case_id] = (reply, hashlib.sha256(encoded).hexdigest())
    return replies


def _score_row(case: panel_sources.Case, observed: tuple[str, str] | None) -> _Row:
    row: _Row = {
        "id": case.id,
        "task": case.task,
        "world": case.world,
        "trial_sha256": case.trial_sha256,
        "prompt_sha256": case.prompt_sha256,
        "state": "missing",
        "reply_sha256": None,
        "correct": None,
        "units": case.units,
    }
    if observed is not None:
        reply, digest = observed
        row["reply_sha256"] = digest
        try:
            correct = case.score(reply)
        except (ValueError, RecursionError):
            row["state"] = "invalid_reply"
        else:
            if type(correct) is not int or not 0 <= correct <= case.units:
                raise ValueError(
                    "source scorer returned a result outside the registered units"
                )
            row["state"] = "valid"
            row["correct"] = correct
    return row


def _task_summary(rows: list[_Row]) -> dict[str, int | None]:
    valid = invalid = correct = scored_units = 0
    for row in rows:
        score = row["correct"]
        if score is not None:
            valid += 1
            correct += score
            scored_units += row["units"]
        elif row["state"] == "invalid_reply":
            invalid += 1
    return {
        "worlds": len({row["world"] for row in rows}),
        "registered": len(rows),
        "valid": valid,
        "invalid": invalid,
        "missing": len(rows) - valid - invalid,
        "correct": correct if valid else None,
        "scored_units": scored_units if valid else None,
    }


def _score(files: list[Path], cases: dict[str, panel_sources.Case]) -> None:
    replies = _read_replies(files, cases)
    rows = [_score_row(case, replies.get(case.id)) for case in cases.values()]
    _print_json(
        {
            "schema": "seatlab.public-receiver-panel/v1",
            "scope": "Conditional receiver replies on fixed factual inputs only; not native team rewards.",
            "tasks": {
                task: _task_summary([row for row in rows if row["task"] == task])
                for task in ("calendar", "dispatch")
            },
            "rows": rows,
        }
    )


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit or score the public receiver panel."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    emit = commands.add_parser(
        "emit", help="write factual inputs as NDJSON", allow_abbrev=False
    )
    emit.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    score = commands.add_parser(
        "score",
        help="score worker files without sending requests",
        description=(
            "Read up to 32 UTF-8 NDJSON files. Each record needs a newline and blank lines "
            "are not allowed. Exit 0 writes a report, even with missing or invalid replies. "
            "Exit 2 indicates an input or usage error and writes no report."
        ),
    )
    score.add_argument("files", nargs="+", type=Path, metavar="FILE")
    args = parser.parse_args()
    if args.command == "score" and len(args.files) > _MAX_CASES:
        score.error("expected at most 32 worker files")
    try:
        cases = dict(sorted(panel_sources.registry().items()))
        if not 1 <= len(cases) <= _MAX_CASES:
            raise ValueError("receiver panel registry size is outside 1 to 32 cases")
        if args.command == "emit":
            _emit(cases, args.shard)
        else:
            _score(args.files, cases)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
