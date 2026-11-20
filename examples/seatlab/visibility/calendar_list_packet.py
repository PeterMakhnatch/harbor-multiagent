"""Bind the frozen final-decision inputs to the 24 native Calendar Feedback trials."""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from . import calendar_packet as ancestor
from . import calendar_protocol as protocol

_RESULTS = Path(__file__).resolve().parents[1] / "results"
PACKET = _RESULTS / "flotilla-calendar-list-packet.json"
_OUTCOMES = _RESULTS / "flotilla-calendar-external-results.json"
# The public outcomes from #56, not the private paid-study directory.
_OUTCOMES_SHA256 = "288aba6efb82dd4f744742757ecdf4de707827a07aa87225009701c6edf3211c"
_SCHEMA = "seatlab-calendar-list-packet-v1"
_ARMS = ("blind", "peer")
_BLOCKS = tuple(
    protocol.Block(w, d, r) for w in (0, 4, 8) for d in range(2) for r in range(2)
)
_Row = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    world: int
    direction: int
    repetition: int
    arm: str
    receiver: tuple[int, ...]
    sender: tuple[int, ...]
    prompt_sha256: str
    trial_id_sha256: str


_CASE_FIELDS = tuple(field.name for field in fields(Case))


def _block_id(block: protocol.Block) -> str:
    return f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}"


def _reports(series: _Row) -> dict[str, _Row]:
    if series["status"] != "complete":
        raise ValueError("incomplete native calendar study")
    rows = series["rows"]
    reports = {row["block"]: row["report"] for row in rows}
    if len(rows) != len(_BLOCKS) or reports.keys() != set(map(_block_id, _BLOCKS)):
        raise ValueError("unexpected native calendar blocks")
    trials = {
        ancestor._check_digest(report["arms"][arm]["trial_id_sha256"])
        for report in reports.values()
        for arm in _ARMS
    }
    if len(trials) != len(_BLOCKS) * len(_ARMS):
        raise ValueError("duplicate native calendar trial")
    return reports


def _historical() -> dict[str, _Row]:
    raw = ancestor._read(_OUTCOMES, 64 * 1024)
    if ancestor._digest(raw) != _OUTCOMES_SHA256:
        raise ValueError("native calendar outcomes do not match their pinned SHA-256")
    return _reports(ancestor._object(ancestor._json(raw)))


def _bind(
    block: protocol.Block,
    arm: str,
    receiver: tuple[int, ...],
    sender: tuple[int, ...],
    table: ancestor.Table,
    outcome: _Row,
) -> Case:
    prompt = protocol.decision_prompt(table, receiver, sender)
    digest = ancestor._digest(prompt.encode())
    case_id = f"{_block_id(block)}-{arm}"
    if digest != outcome["receiver_prompt_sha256"]:
        raise ValueError(f"historical decision prompt mismatch: {case_id}")
    return Case(
        case_id,
        block.world,
        block.direction,
        block.repetition,
        arm,
        receiver,
        sender,
        digest,
        outcome["trial_id_sha256"],
    )


def _canonical(cases: tuple[Case, ...]) -> bytes:
    value = {"schema": _SCHEMA, "cases": [asdict(case) for case in cases]}
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )


def load() -> tuple[Case, ...]:
    """Verify the public packet against the pinned outcomes and ancestor packet."""
    historical = _historical()
    worlds = ancestor.load(ancestor.PACKET, ancestor.PACKET_SHA256)
    raw = ancestor._read(PACKET, 16 * 1024)
    value = ancestor._object(ancestor._json(raw), "schema", "cases")
    rows = ancestor._list(value["cases"])
    if value["schema"] != _SCHEMA or len(rows) != len(_BLOCKS) * len(_ARMS):
        raise ValueError("unexpected calendar list packet schema or case count")
    cases = []
    planned = ((block, arm) for block in _BLOCKS for arm in _ARMS)
    for (block, arm), raw_row in zip(planned, rows, strict=True):
        row = ancestor._object(raw_row, *_CASE_FIELDS)
        cases.append(
            _bind(
                block,
                arm,
                protocol._decode_starts({"starts": row["receiver"]}),
                protocol._decode_starts({"starts": row["sender"]}),
                worlds[block.world].tables[1 - block.direction],
                historical[_block_id(block)]["arms"][arm],
            )
        )
    result = tuple(cases)
    # This compares every input field, including the order, types, and both digests.
    if _canonical(result) != raw:
        raise ValueError(
            "unexpected calendar list case identity or noncanonical packet"
        )
    return result


def _response(turns: Path, known_turns: _Row, run: str, role: str, seat: str) -> str:
    from . import replica_io
    from .calendar_report import _bytes, _object

    known = known_turns[f"{run}:{role}"]
    receipt = _object(_bytes(turns / f"{run}_{role}.json", replica_io.MAX_FRAME_BYTES))
    text = receipt.get("response_text")
    if (
        not isinstance(text, str)
        or receipt
        != {
            "schema": replica_io.TURN_SCHEMA,
            "run": run,
            "role": role,
            "seat": seat,
            "prompt_sha256": known["prompt_sha256"],
            "response_sha256": known["response_sha256"],
            "response_text": text,
        }
        or ancestor._digest(text.encode()) != known["response_sha256"]
    ):
        raise ValueError(f"historical turn receipt mismatch: {run}:{role}")
    return text


def _build(study: Path) -> bytes:
    from . import calendar_replica_series

    historical = _historical()
    worlds = ancestor.load(ancestor.PACKET, ancestor.PACKET_SHA256)
    study = study.absolute()
    # The stock series reader calls calendar_replica_report.analyze for each block.
    # Require its independently validated timelines, trial IDs, and decisions.
    if _reports(calendar_replica_series.analyze(study)) != historical:
        raise ValueError("native calendar study differs from the pinned outcomes")

    cases = []
    for block in _BLOCKS:
        run = _block_id(block)
        report = historical[run]
        turns = study / run / "block" / "turns"
        known_turns = report["turns"]
        seed_role = f"cal_initial_{block.receiver}"

        receiver = protocol.decode_list(
            _response(turns, known_turns, run, seed_role, block.receiver)
        )
        table = worlds[block.world].tables[1 - block.direction]
        for arm in _ARMS:
            name = f"{run}-{arm}"
            outcome = report["arms"][arm]
            sender = protocol.decode_list(
                _response(turns, known_turns, name, "cal_recheck", block.sender)
            )
            slot = protocol.decode_slot(
                _response(turns, known_turns, name, "cal_decide", block.receiver)
            )
            if slot != outcome["slot"]:
                raise ValueError(f"historical receiver decision mismatch: {name}")
            cases.append(_bind(block, arm, receiver, sender, table, outcome))
    return _canonical(tuple(cases))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="write the safe projection to stdout")
    build.add_argument("private_study", metavar="private-study", type=Path)
    subcommands.add_parser("verify", help="verify the packet using only public inputs")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            sys.stdout.buffer.write(_build(args.private_study))
        else:
            print(f"Verified {len(load())} historical final-decision inputs.")
    except (OSError, ValueError, RuntimeError):
        # Never include source paths, raw turn text, or parser exception content.
        print(
            "calendar_list_packet: missing or invalid calendar evidence",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
