import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from .core import (
    BOUNDARIES,
    MAX_LOG_BYTES,
    Edge,
    Finding,
    Replay,
    check_identifier,
    frames_from_bytes,
    replay_frames,
)

SCHEMA = "harbor.handoff-measurement-controls/v1"
MAX_TASK_BYTES = 1024 * 1024
MAX_SEATS = 64
MAX_JOURNALS = 256
WORLD_ARTIFACT = "/logs/world/handoffs.jsonl"
STANDARD_DESTINATIONS = (None, "", WORLD_ARTIFACT, WORLD_ARTIFACT.removeprefix("/"))
CASES = ("positive", "contrast")
CAVEAT = (
    "A positive is a sealed journal whose requested boundary precedes a recorded commit; a contrast is "
    "another sealed run of the same full topology and acknowledgement capability whose commit is recorded "
    "and whose boundary follows it or is absent from the closed journal. Both must fall in one shape. Open, "
    "torn, uncommitted, unsupported or foreign journals never count, and unknown is not a negative. The task "
    "roster names roles only and journal v1 carries no task digest, so the binding is operator supplied. "
    "Grades and rewards are not read or reported."
)


def read_bounded(path: Path, limit: int, name: str) -> bytes:
    assert limit > 0
    if not path.is_file():
        raise ValueError(f"{name} is missing or not a regular file")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{name} exceeds {limit} bytes")
    return data


def main_service(service: object) -> bool:
    return service is None or (isinstance(service, str) and service.strip() == "main")


def world_artifact_exports(artifacts: object) -> tuple[bool, bool]:
    declared = standard = False
    for item in artifacts if isinstance(artifacts, list) else []:
        if item == WORLD_ARTIFACT:
            declared = standard = True
        elif isinstance(item, dict) and item.get("source") == WORLD_ARTIFACT:
            declared = True
            standard = standard or (
                item.get("destination") in STANDARD_DESTINATIONS
                and main_service(item.get("service"))
            )
    assert declared or not standard
    return declared, standard


def read_roster(task_dir: Path) -> tuple[list[str], tuple[bool, bool]]:
    if not task_dir.is_dir():
        raise ValueError("task directory is missing or not a directory")
    data = read_bounded(task_dir / "task.toml", MAX_TASK_BYTES, "task.toml")
    try:
        config = tomllib.loads(data.decode("utf-8"))
    except ValueError as error:
        raise ValueError("task.toml is not valid UTF-8 TOML") from error
    agents = config.get("agents")
    if not isinstance(agents, dict) or not 1 <= len(agents) <= MAX_SEATS:
        raise ValueError(f"task.toml needs 1 to {MAX_SEATS} [agents.<seat>] tables")
    for seat, table in agents.items():
        check_identifier(seat, "task seat")
        if not isinstance(table, dict):
            raise TypeError("task seat is not a table")
    return sorted(agents), world_artifact_exports(config.get("artifacts"))


def check_edge(values: list[str], roster: list[str]) -> Edge:
    assert len(values) == 3
    sender, message, receiver = values
    edge = Edge(
        message=check_identifier(message, "edge message"),
        sender=check_identifier(sender, "edge sender"),
        receiver=check_identifier(receiver, "edge receiver"),
    )
    if edge.sender == edge.receiver:
        raise ValueError("edge sender and receiver must differ")
    for role in (edge.sender, edge.receiver):
        if role not in roster:
            raise ValueError(f"edge role {role} is not declared in the task roster")
    return edge


def status_of(
    replay: Replay, finding: Finding | None, boundary: str, roster: list[str]
) -> str:
    roles = {e.sender for e in replay.edges} | {e.receiver for e in replay.edges}
    if not roles <= set(roster):
        return "roster_mismatch"
    if finding is None:
        return "edge_not_declared"
    if boundary == "acknowledged" and not replay.acks:
        return "ack_unsupported"
    if not replay.closed:
        return "open"
    assert finding.receipt in ("confirmed", "absent")
    if finding.commit != "recorded":
        return "no_commit"
    if finding.timing == "before":
        return "positive"
    assert finding.timing == "after" or finding.receipt == "absent"
    return "contrast"


def classify(
    data: bytes, edge: Edge, boundary: str, roster: list[str]
) -> dict[str, Any]:
    frames, torn = frames_from_bytes(data)
    replay = replay_frames(frames)
    if torn and replay.closed:
        raise ValueError("data follows the closed record")
    finding = replay.finding(edge, boundary) if edge in replay.edges else None
    topology = sorted([e.sender, e.message, e.receiver] for e in replay.edges)
    assert 1 <= len(topology) == len(set(map(tuple, topology)))
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "run": replay.run,
        "closed": replay.closed,
        "torn": torn,
        "acknowledgements": replay.acks,
        "topology": topology,
        "receipt": None if finding is None else finding.receipt,
        "timing": None if finding is None else finding.timing,
        "commit": None if finding is None else finding.commit,
        "basis": None if finding is None else finding.basis,
        "status": status_of(replay, finding, boundary, roster),
    }


def read_journals(
    paths: list[Path], edge: Edge, boundary: str, roster: list[str]
) -> list[dict[str, Any]]:
    if len(paths) > MAX_JOURNALS:
        raise ValueError(f"at most {MAX_JOURNALS} journals are accepted")
    seen_paths: set[Path] = set()
    seen_runs: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, path in enumerate(paths):
        resolved = path.resolve()
        if resolved in seen_paths:
            raise ValueError(f"journal {index} repeats a supplied file")
        seen_paths.add(resolved)
        try:
            row = classify(
                read_bounded(path, MAX_LOG_BYTES, "journal"), edge, boundary, roster
            )
        except (OSError, ValueError) as error:
            raise ValueError(f"journal {index}: {error}") from error
        if row["run"] in seen_runs:
            raise ValueError(f"journal {index} repeats world run {row['run']}")
        seen_runs.add(row["run"])
        rows.append(row)
    assert len(rows) == len(paths) == len(seen_runs)
    return rows


def control_groups(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    index: dict[tuple[tuple[tuple[str, ...], ...], bool], int] = {}
    shapes: list[dict[str, Any]] = []
    for row in rows:
        assert type(row["acknowledgements"]) is bool and row["topology"]
        key = (tuple(tuple(edge) for edge in row["topology"]), row["acknowledgements"])
        if key not in index:
            index[key] = len(shapes)
            shapes.append(
                {"topology": row["topology"], "acknowledgements": key[1]}
                | dict.fromkeys(CASES, 0)
            )
            shapes[-1]["members"] = 0
        shape = shapes[index[key]]
        shape["members"] += 1
        if row["status"] in CASES:
            shape[row["status"]] += 1
        row["shape"] = index[key]
    for shape in shapes:
        shape["missing"] = [case for case in CASES if shape[case] == 0]
    assert sum(s["members"] for s in shapes) == len(rows) and len(index) == len(shapes)
    if any(not s["missing"] for s in shapes):
        return shapes, []
    measured = [s for s in shapes if s["positive"] or s["contrast"]]
    missing = [
        case
        for case in CASES
        if not measured or any(case in s["missing"] for s in measured)
    ]
    assert missing
    return shapes, missing


def qualify(
    task_dir: Path, edge_values: list[str], boundary: str, paths: list[Path]
) -> dict[str, Any]:
    assert boundary in BOUNDARIES
    roster, (declared, standard) = read_roster(task_dir)
    edge = check_edge(edge_values, roster)
    rows = read_journals(paths, edge, boundary, roster)
    shapes, missing = control_groups(rows)
    distinguishable = not missing
    return {
        "schema": SCHEMA,
        "task_binding": "operator_supplied",
        "roster": roster,
        "world_artifact_declared": declared,
        "standard_world_export": standard,
        "edge": {
            "sender": edge.sender,
            "message": edge.message,
            "receiver": edge.receiver,
        },
        "boundary": boundary,
        "journals": rows,
        "shapes": shapes,
        "distinguishable": distinguishable,
        "missing": missing,
        "caveat": CAVEAT,
    }


def text(report: dict[str, Any]) -> list[str]:
    edge = report["edge"]
    lines = [
        f"edge {edge['sender']} -> {edge['message']} -> {edge['receiver']} at {report['boundary']}",
        f"roster: {' '.join(report['roster'])}   task binding: {report['task_binding']}",
        (
            f"world artifact source declared: {json.dumps(report['world_artifact_declared'])}   "
            f"standard export: {json.dumps(report['standard_world_export'])}"
        ),
    ]
    for row in report["journals"]:
        lines.append(
            f"journal {row['run']} sha256 {row['sha256']} shape {row['shape']}: {row['status']} "
            f"(closed {json.dumps(row['closed'])}, receipt {row['receipt']}, timing {row['timing']}, "
            f"commit {row['commit']})"
        )
    for index, shape in enumerate(report["shapes"]):
        lines.append(
            f"shape {index}: {shape['members']} journals, positive {shape['positive']}, "
            f"contrast {shape['contrast']}, missing {' '.join(shape['missing']) or 'none'}"
        )
    verdict = (
        "distinguishable"
        if report["distinguishable"]
        else f"missing {' '.join(report['missing'])}"
    )
    return [*lines, verdict, report["caveat"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qualify", allow_abbrev=False)
    parser.add_argument("task_dir", type=Path)
    parser.add_argument(
        "--edge", nargs=3, required=True, metavar=("SENDER", "MESSAGE", "RECEIVER")
    )
    parser.add_argument("--boundary", choices=BOUNDARIES, required=True)
    parser.add_argument("--journal", action="extend", nargs="+", type=Path, default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = qualify(args.task_dir, args.edge, args.boundary, args.journal)
    except (OSError, TypeError, ValueError) as error:
        print(f"Cannot qualify controls: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    else:
        print("\n".join(text(report)))
    return 0 if report["distinguishable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
