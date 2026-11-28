import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Never, override

from ..decision_boundary.inputs import LIMIT, read_input
from ..decision_boundary.revised_temporal import MAX_SEED, SCALE
from .model import POLICIES, Episode, Selector, packet, qualify
from .revised import SELECTOR, cases

SCHEMA = "seatlab.receiver-memory/v1"
CASES_SCHEMA = "seatlab.receiver-memory.cases/v1"
WORLD_NAMES = ("confirm", "replace")
SENDER_NAMES = ("truthful", "repeat")
NATIVE_GRADES = 12
SCOPE = (
    "Finite values for scripted receiver inputs and native scores, not model evidence."
)


class InputError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        self.exit(2, "receiver-memory: invalid command arguments\n")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(prog="receiver-memory", description=SCOPE)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("native", "packet", "grade", "export"):
        sub = subcommands.add_parser(command)
        sub.add_argument("--seed", required=True, type=int)
        sub.add_argument("--sender", required=True, choices=SENDER_NAMES)
        if command in ("packet", "grade"):
            sub.add_argument("--world", required=True, choices=WORLD_NAMES)
        if command == "packet":
            sub.add_argument("--policy", required=True, choices=POLICIES)
        elif command == "grade":
            sub.add_argument("--answer", required=True)
        elif command == "export":
            sub.add_argument("--out", required=True)
        else:
            sub.add_argument("--expect-retention", choices=("yes", "no"))
    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("file")
    inspect.add_argument("--expect-retention", choices=("yes", "no"))
    return parser.parse_args(argv)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row = dict(pairs)
    if len(row) != len(pairs):
        raise InputError("duplicate JSON member")
    return row


def _constant(value: str) -> Never:
    raise InputError("nonfinite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise InputError("nonfinite JSON number")
    return number


def _read(path: str) -> Any:
    if path == "-":
        raise InputError("expected a bounded regular file")
    try:
        data = read_input(path)
    except (OSError, ValueError):
        raise InputError("expected a bounded regular file") from None
    if not 0 < len(data) <= LIMIT:
        raise InputError("expected a bounded regular file")
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except InputError:
        raise
    except (ValueError, RecursionError):
        raise InputError("invalid JSON input") from None


def _fields(value: Any, fields: set[str], reason: str) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != fields:
        raise InputError(reason)
    return value


def _frame(value: Any) -> dict[str, Any]:
    frame = _fields(
        value, {"method", "path", "body", "status", "reply"}, "invalid frame fields"
    )
    if (
        type(frame["method"]) is not str
        or type(frame["path"]) is not str
        or type(frame["status"]) is not int
    ):
        raise InputError("invalid frame types")
    return frame


def _selector(value: Any) -> Selector:
    row = _fields(
        value, {"method", "path", "sequence", "field"}, "invalid selector fields"
    )
    if (
        any(type(row[key]) is not str for key in ("method", "path", "field"))
        or type(row["sequence"]) is not list
        or any(type(key) is not str for key in row["sequence"])
    ):
        raise InputError("invalid selector types")
    return Selector(row["method"], row["path"], tuple(row["sequence"]), row["field"])


def _episode(value: Any) -> Episode:
    row = _fields(
        value,
        {"name", "mass", "instruction", "history", "refresh", "scores"},
        "invalid case fields",
    )
    if (
        type(row["name"]) is not str
        or type(row["mass"]) is not int
        or type(row["instruction"]) is not str
        or type(row["history"]) is not list
        or type(row["scores"]) is not dict
        or any(type(score) is not int for score in row["scores"].values())
    ):
        raise InputError("invalid case types")
    return Episode(
        row["name"],
        row["mass"],
        row["instruction"],
        tuple(_frame(frame) for frame in row["history"]),
        _frame(row["refresh"]),
        row["scores"],
    )


def _census(value: Any) -> tuple[list[Episode], int, Selector]:
    root = _fields(
        value, {"schema", "scale", "selector", "cases"}, "invalid cases schema"
    )
    if root["schema"] != CASES_SCHEMA or type(root["scale"]) is not int:
        raise InputError("invalid cases schema")
    rows = root["cases"]
    if type(rows) is not list or not 1 <= len(rows) <= 1024:
        raise InputError("invalid case count")
    return [_episode(row) for row in rows], root["scale"], _selector(root["selector"])


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")


def _export(path: str, episodes: list[Episode], selector: Selector) -> None:
    from ..exhibit_enroll import write_private

    if path == "-":
        raise InputError("expected a new private file")
    data = (
        _json(
            {
                "schema": CASES_SCHEMA,
                "scale": SCALE,
                "selector": asdict(selector),
                "cases": [asdict(case) for case in episodes],
            }
        )
        + b"\n"
    )
    if len(data) > LIMIT:
        raise InputError("export exceeds the file limit")
    write_private(Path(path), data)


def _report(qualification: dict[str, Any], native_grades: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "scope": SCOPE,
        "qualification": qualification,
        "native_grades": native_grades,
        "model_calls": 0,
        "trials": 0,
    }


def _matches(qualification: dict[str, Any], expected: str | None) -> bool:
    return expected is None or qualification["retention_pattern"] == (expected == "yes")


def _score(case: Episode, path: str) -> dict[str, Any]:
    answer = _fields(_read(path), {"slot"}, "invalid answer fields")
    slot = answer["slot"]
    if type(slot) is not str or slot not in case.scores:
        raise InputError("unknown slot choice")
    return {
        "schema": SCHEMA,
        "scope": "Native scripted chosen action score, not a model grade or a new trial.",
        "score": case.scores[slot],
        "model_calls": 0,
        "trials": 0,
    }


def _run(args: argparse.Namespace) -> tuple[bytes, bool]:
    if args.command == "inspect":
        episodes, scale, selector = _census(_read(args.file))
        result = qualify(episodes, scale, selector)
        return _json(_report(result, 0)), _matches(result, args.expect_retention)
    if not 0 <= args.seed <= MAX_SEED:
        raise InputError("invalid seed")
    episodes = cases(args.seed, args.sender)
    if args.command in ("packet", "grade"):
        case = next(case for case in episodes if case.name == args.world)
        if args.command == "packet":
            return packet(case, args.policy, SELECTOR), True
        return _json(_score(case, args.answer)), True
    result = qualify(episodes, SCALE, SELECTOR)
    report = _report(result, NATIVE_GRADES)
    if args.command == "export":
        _export(args.out, episodes, SELECTOR)
        return _json(report), True
    return _json(report), _matches(result, args.expect_retention)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        data, matches = _run(args)
    except InputError as error:
        print(f"receiver-memory: {error}", file=sys.stderr)
        return 2
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        IndexError,
        StopIteration,
        AssertionError,
        OverflowError,
    ):
        print("receiver-memory: invalid or inaccessible input", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(data + b"\n")
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
