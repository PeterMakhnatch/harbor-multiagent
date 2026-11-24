import argparse
import json
import os
import sys
from contextlib import ExitStack
from fractions import Fraction
from pathlib import Path
from typing import Any

from .native_tickets import Survey, check_fingerprint, survey, take

MAX_STUDIES = 64
MAX_DIAGNOSTIC = 512
EXIT_FAILURE = 2
EXIT_NONE_LEFT = 3
BOUND = "match"
PARTITION = (
    "available",
    "reserved_unstarted",
    "directory_without_result",
    "result_file_present",
)
TOTALS = ("assigned", *PARTITION, "started_outside_worklist")
Record = dict[str, Any]
Study = tuple[str, str, str]


class StorageFault(Exception):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _safe(text: str) -> str:
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_DIAGNOSTIC]


def _storage(position: int, error: OSError) -> StorageFault:
    return StorageFault(f"study {position}: {error.strerror or 'failed'}")


def _node(position: int, workspace: Path) -> tuple[int, int]:
    try:
        info = os.stat(workspace)
    except OSError as error:
        raise _storage(position, error) from None
    return info.st_dev, info.st_ino


def _survey_all(stack: ExitStack, studies: list[Study]) -> list[Survey]:
    _require(0 < len(studies) <= MAX_STUDIES, f"give 1..{MAX_STUDIES} --study")
    found: list[Survey] = []
    paths: dict[Path, int] = {}
    nodes: dict[tuple[int, int], int] = {}
    owners: dict[str, int] = {}
    for position, (registration, out, shard) in enumerate(studies, start=1):
        try:
            item = survey(stack, registration, shard, out)
        except ValueError as error:
            raise ValueError(f"study {position}: {error}") from None
        except OSError as error:
            raise _storage(position, error) from None
        assert item.workspace.is_absolute() and len(item.tickets) > 0
        node = _node(position, item.workspace)
        first = paths.get(item.workspace, nodes.get(node))
        _require(first is None, f"study {position} repeats the study {first} workspace")
        paths[item.workspace] = position
        nodes[node] = position
        for ticket in item.tickets:
            other = owners.setdefault(ticket, position)
            _require(
                other == position,
                f"study {position} selects ticket {ticket} of study {other}",
            )
        found.append(item)
    assert len(found) == len(studies)
    return found


def _counts(item: Survey, position: int) -> Record:
    counts = item.status()
    where = f"study {position} status"
    _require(type(counts) is dict, f"{where} is not an object")
    for key in TOTALS:
        value = counts.get(key)
        _require(type(value) is int and value >= 0, f"{where} lacks count {key}")
    _require(counts["assigned"] == len(item.tickets), f"{where} assigned drifts")
    _require(
        sum(counts[key] for key in PARTITION) == counts["assigned"],
        f"{where} counts do not partition assigned tickets",
    )
    _require(
        counts["available"] == len(item.unclaimed()),
        f"{where} available differs from unclaimed tickets",
    )
    _require(
        counts["started_outside_worklist"] <= counts["assigned"],
        f"{where} started_outside_worklist exceeds assigned",
    )
    return counts


def _status(found: list[Survey], studies: list[Study]) -> Record:
    totals = dict.fromkeys(TOTALS, 0)
    worlds: list[Record] = []
    unbound = 0
    for position, (item, study) in enumerate(zip(found, studies, strict=True), 1):
        counts = _counts(item, position)
        for key in TOTALS:
            totals[key] += counts[key]
        unbound += item.task_files != BOUND
        worlds.append(
            {
                "study": position,
                "registration": os.path.abspath(study[0]),
                "registration_sha256": item.registration_sha256,
                "workspace": str(item.workspace),
                "status": counts,
            }
        )
    assert sum(totals[key] for key in PARTITION) == totals["assigned"]
    totals["workspaces"] = len(worlds)
    totals["unbound_workspaces"] = unbound
    return {"worlds": worlds, "total": totals}


def _check_config(config: object, item: Survey, position: int) -> str:
    where = f"study {position} take"
    _require(type(config) is str and config.isprintable(), f"{where} path is odd")
    assert isinstance(config, str)
    path = Path(config)
    _require(path.is_absolute() and str(path) == config, f"{where} path not absolute")
    _require(path.parent == item.workspace / "configs", f"{where} left configs")
    _require(
        path.suffix == ".json" and path.stem in item.unclaimed(),
        f"{where} returned a ticket that was not available",
    )
    return config


def _candidates(found: list[Survey]) -> list[tuple[Fraction, int, Survey]]:
    candidates: list[tuple[Fraction, int, Survey]] = []
    for position, item in enumerate(found, start=1):
        counts = _counts(item, position)
        if counts["available"] == 0:
            continue
        _require(
            item.task_files == BOUND,
            f"study {position} task files are {item.task_files}; "
            "run bind on it first; no ticket was claimed",
        )
        assert counts["assigned"] > 0
        done = Fraction(counts["assigned"] - counts["available"], counts["assigned"])
        candidates.append((done, position, item))
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    return candidates


def _take(stack: ExitStack, found: list[Survey]) -> str | None:
    candidates = _candidates(found)
    for _, position, item in candidates:
        try:
            config = take(stack, item)
        except ValueError as error:
            raise ValueError(f"study {position}: {error}") from None
        except OSError as error:
            raise _storage(position, error) from None
        if config is not None:
            return _check_config(config, item, position)
    return None


def _bind(item: Survey) -> int:
    try:
        result = check_fingerprint(item)
    except ValueError as error:
        raise ValueError(f"study 1: {error}") from None
    except OSError as error:
        raise _storage(1, error) from None
    _require(type(result) is str, "study 1 bind returned no state")
    _require(result == BOUND, f"study 1 task files are {result}; nothing is bound")
    record = {
        "study": 1,
        "registration_sha256": item.registration_sha256,
        "workspace": str(item.workspace),
        "shard": item.shard,
        "task_files": result,
    }
    print(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python3 -BS -m examples.native_worklist")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("status", "take", "bind"):
        command = commands.add_parser(name)
        command.add_argument(
            "--study",
            nargs=3,
            action="append",
            required=True,
            metavar=("MANIFEST", "OUT", "I/N"),
        )
    return root


def run(args: argparse.Namespace) -> int:
    studies: list[Study] = [(study[0], study[1], study[2]) for study in args.study]
    _require(len(studies) <= MAX_STUDIES, f"give at most {MAX_STUDIES} --study")
    _require(
        args.command != "bind" or len(studies) == 1,
        "bind accepts exactly one --study",
    )
    with ExitStack() as stack:
        found = _survey_all(stack, studies)
        if args.command == "status":
            report = _status(found, studies)
            print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
            return 0
        if args.command == "bind":
            return _bind(found[0])
        assert args.command == "take"
        config = _take(stack, found)
        if config is None:
            message = f"no available ticket in {len(found)} workspaces"
            print(message, file=sys.stderr)
            return EXIT_NONE_LEFT
        print(config)
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except ValueError as error:
        print(f"error: invalid: {_safe(str(error))}", file=sys.stderr)
    except StorageFault as error:
        print(f"error: storage: {_safe(str(error))}", file=sys.stderr)
    except OSError as error:
        print(f"error: storage: {_safe(error.strerror or 'failed')}", file=sys.stderr)
    return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
