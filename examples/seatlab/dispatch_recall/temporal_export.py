import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ...native_study import require, write_exclusive
from ...seat_checkpoints.materialize import dir_mode, read_plain, sync_directory
from ...ship_inflight.materialize import publish
from ..decision_boundary.core import World, analyze
from ..decision_boundary.inputs import SCHEMA, census
from ..decision_boundary.pair import partition
from ..exhibit_enroll import target
from . import certificate
from .certificate import (
    _EXPECTED_DECISION,
    _HINDSIGHT_SPLIT,
    SENDERS,
    TRANSITIONS,
    _transition_row,
    phase_tickets,
)
from .native import ENVIRONMENT

EXPORT_SCHEMA = "seatlab.dispatch-recall.temporal-export/v1"
ACCESS = "private_benchmark_author"
SCOPE = (
    "Author-only answer tables for one held seed and one fixed depot sender rule. "
    "Each file holds the two equally likely transitions with the exact loader engine "
    "reply bytes at one moment and the native physical score of every legal commit. "
    "Commit files end at the commit window view; close files add the commit ack, "
    "close window view and close ack. Close files are hindsight: the loader cannot "
    "revise after commit, so their scores are the commit scores, not a new choice."
)
PRIVACY = (
    "Files contain world names, answer scores and seed-derived phase tickets; keep "
    "them away from participants, task images and checkouts. Bytes cover engine "
    "replies only, not role instructions, host or client request bytes, provider "
    "text, tokenization or wall-clock timing. No model or Harbor trial ran."
)
STAGE_PREFIX = ".dispatch-recall-temporal-"
PRIVATE_FILE = 0o600
PRIVATE_DIR = 0o700
SCALE = 1
PHASES = (("early", "immediate"), ("late", "deferred"))
MOMENTS = (("commit", "decision"), ("close", "hindsight"))
FILES = tuple(f"{phase}-{moment}.json" for phase, _ in PHASES for moment, _ in MOMENTS)
SCHEDULE = ("view", "stage", "view", "view")
AFTER_COMMIT_LINES = 3
PINNED = {
    "recall_world.py": ENVIRONMENT / "recall_world.py",
    "recall_server.py": ENVIRONMENT / "recall_server.py",
    "certificate.py": Path(certificate.__file__),
}


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _winner(scores: dict[str, int]) -> str:
    winners = [bay for bay, score in scores.items() if score == 1]
    require(len(winners) == 1, "score table does not have one physical winner")
    return winners[0]


def _conserve(rows: dict[str, dict[str, Any]]) -> None:
    stay, move = rows["stay"], rows["move"]
    require(stay["menu"] == move["menu"], "legal menu differs across transitions")
    require(stay["requests"] == move["requests"], "loader requests depend on the arm")
    require(
        tuple(request["op"] for request in stay["requests"]) == SCHEDULE,
        "loader request schedule differs from the fixed protocol",
    )
    initial = stay["facts"]["initial"]
    require(move["facts"]["initial"] == initial, "initial bay depends on the arm")
    other = next(bay for bay in stay["menu"] if bay != initial)
    require(
        _winner(stay["scores"]) == initial and _winner(move["scores"]) == other,
        "score winners are not the physical stay and move bays",
    )
    for name, row in rows.items():
        decision, hindsight = row["decision"], row["hindsight"]
        require(
            tuple(row["scores"]) == row["menu"], f"{name} scores differ from the menu"
        )
        require(
            len(hindsight) > len(decision) and hindsight.startswith(decision),
            f"{name} close bytes do not extend the commit bytes",
        )
        require(
            decision.count(b"\n") == len(SCHEDULE)
            and hindsight.count(b"\n") == len(SCHEDULE) + AFTER_COMMIT_LINES,
            f"{name} wire line count differs from the protocol",
        )
        for word in (b"current_bay", b"transition", b"timing", b"stay", b"move"):
            require(word not in hindsight, f"{name} loader bytes leak the arm")
    for key in ("decision", "hindsight"):
        require(
            len(stay[key]) == len(move[key]),
            "fixed-width wire lengths depend on the transition",
        )


def _expect(bundle: dict[str, list[World]], sender: str) -> None:
    reference = [world.scores for world in bundle[FILES[0]]]
    for rows in bundle.values():
        require(
            [world.scores for world in rows] == reference,
            "score tables differ across files",
        )
    for phase, timing in PHASES:
        commit = analyze(bundle[f"{phase}-commit.json"], SCALE)
        close = analyze(bundle[f"{phase}-close.json"], SCALE)
        require(
            commit["observed"] == _EXPECTED_DECISION[sender][timing],
            f"{phase} commit optimum differs from the design",
        )
        split = _HINDSIGHT_SPLIT[sender][timing]
        require(
            close["observed"] == ("1" if split else "1/2"),
            f"{phase} close optimum differs from the design",
        )


def tables(seed: int, sender: str) -> dict[str, list[World]]:
    require(sender in SENDERS, "unknown sender rule")
    tickets = phase_tickets(seed)
    bundle: dict[str, list[World]] = {}
    for phase, timing in PHASES:
        rows = {
            transition: _transition_row(seed, transition, timing, sender, tickets)
            for transition in TRANSITIONS
        }
        _conserve(rows)
        for moment, key in MOMENTS:
            bundle[f"{phase}-{moment}.json"] = [
                World(name, 1, rows[name][key], dict(rows[name]["scores"]))
                for name in TRANSITIONS
            ]
    require(tuple(bundle) == FILES, "bundle files differ from the fixed order")
    _expect(bundle, sender)
    return bundle


def _record(rows: list[World]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "scale": SCALE,
        "worlds": [
            {
                "name": row.name,
                "mass": row.mass,
                "scores": row.scores,
                "observation_b64": base64.b64encode(row.observation).decode("ascii"),
            }
            for row in rows
        ],
    }


def _staged(path: Path, rows: list[World]) -> str:
    data, mode = read_plain(path)
    require(mode == PRIVATE_FILE, f"{path.name} is not private")
    require(census(data) == (SCALE, rows), f"{path.name} does not round-trip")
    return _digest(data)


def _write(staged: Path, bundle: dict[str, list[World]]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for name in FILES:
        write_exclusive(staged / name, _record(bundle[name]), PRIVATE_FILE)
        digests[name] = _staged(staged / name, bundle[name])
    require(sorted(os.listdir(staged)) == sorted(FILES), "staged files differ")
    require(dir_mode(staged) == PRIVATE_DIR, "staged directory is not private")
    return digests


def pins() -> dict[str, str]:
    return {name: _digest(read_plain(path)[0]) for name, path in PINNED.items()}


def _summary(
    out: Path, seed: int, sender: str, bundle: dict[str, list[World]]
) -> dict[str, Any]:
    analyses = {name: analyze(bundle[name], SCALE) for name in FILES}
    return {
        "schema": EXPORT_SCHEMA,
        "access": ACCESS,
        "scope": SCOPE,
        "privacy": PRIVACY,
        "seed": seed,
        "sender": sender,
        "path": str(out),
        "input_schema": SCHEMA,
        "scale": SCALE,
        "phases": dict(PHASES),
        "worlds_per_file": len(TRANSITIONS),
        "actions_per_world": len(bundle[FILES[0]][0].scores),
        "observed": {name: analyses[name]["observed"] for name in FILES},
        "classes": {name: analyses[name]["observations"] for name in FILES},
        "after_commit": {
            phase: partition(
                bundle[f"{phase}-commit.json"], bundle[f"{phase}-close.json"]
            )[0]
            for phase, _ in PHASES
        },
        "pins": pins(),
        "model_calls": 0,
        "harbor_trials": 0,
    }


def export(out: Path, seed: int, sender: str) -> dict[str, Any]:
    out = target(out)
    bundle = tables(seed, sender)
    with tempfile.TemporaryDirectory(prefix=STAGE_PREFIX, dir=out.parent) as temporary:
        staged = Path(temporary) / "tables"
        staged.mkdir(mode=PRIVATE_DIR)
        digests = _write(staged, bundle)
        sync_directory(staged)
        publish(staged, out)
    sync_directory(out.parent)
    require(dir_mode(out) == PRIVATE_DIR, "published directory is not private")
    return {**_summary(out, seed, sender, bundle), "files": digests}


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write private early/late commit and close decision tables.",
        allow_abbrev=False,
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--sender", choices=SENDERS, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = arguments().parse_args(argv)
    try:
        summary = export(args.out, args.seed, args.sender)
    except (ValueError, TypeError, OSError, RuntimeError) as error:
        print(f"dispatch-recall temporal export: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
