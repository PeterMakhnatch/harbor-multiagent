"""Plan, project, and decode the fixed-seed box ownership continuation offline."""

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

from . import boxes_choice, intent_packet, intent_protocol
from .intent_protocol import LABELS, Block, Choices

RESULTS = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "muster-box-intentions-results.json"
)
RESULTS_SHA256 = "8b80f2330fcf0f93cd877226cc2ce30087b8cf48cefc88fc52cffdeecc312bc2"
_RESULTS_BYTES = 92_678
_COORDINATES = ("instance", "numbering", "rotation", "repetition")
_RULES = {
    "neutral": "Each proposal is a suggestion. No label has priority. You may keep or change your box.",
    "owner": (
        "Each proposed box belongs to its proposer with the earliest label (A before B before C). "
        "Keep your proposal if you own it. Otherwise choose a box no seat proposed."
    ),
}


def _map_choices(choices: Choices, numbering: int) -> Choices:
    a, b, c = choices
    return (
        boxes_choice.map_box(a, numbering),
        boxes_choice.map_box(b, numbering),
        boxes_choice.map_box(c, numbering),
    )


def _seed_choices(report: object) -> dict[str, Choices]:
    registered = {block.id: block for block in intent_protocol.blocks()}
    if (
        not isinstance(report, dict)
        or report.get("schema") != "seatlab.box-intentions-report/v1"
        or report.get("packet_sha256") != intent_packet.PACKET_SHA256
    ):
        raise ValueError("wrong intentions results schema or packet")
    rows = report.get("blocks")
    if not isinstance(rows, list) or len(rows) != len(registered):
        raise ValueError("wrong number of registered proposal blocks")

    choices: dict[str, Choices] = {}
    for row in rows:
        block_id = row.get("id") if isinstance(row, dict) else None
        if (
            not isinstance(block_id, str)
            or block_id not in registered
            or block_id in choices
        ):
            raise ValueError("unknown or duplicate proposal block")
        block = registered[block_id]
        for name in _COORDINATES:
            value = row.get(name)
            if type(value) is not int or value != getattr(block, name):
                raise ValueError("proposal block coordinates do not match its ID")
        if row.get("proposal_barrier") != "verified":
            raise ValueError("proposal barrier is not verified")
        calls = row.get("calls")
        proposed = calls.get("proposal") if isinstance(calls, dict) else None
        if not isinstance(proposed, dict) or proposed.keys() != set(LABELS):
            raise ValueError("missing proposal call evidence")
        for index, label in enumerate(LABELS):
            call = proposed[label]
            if (
                not isinstance(call, dict)
                or call.get("id") != intent_protocol.call_id(block, index, "proposal")
                or call.get("status") != "accepted"
                or call.get("receipt_status") != "accepted"
                or call.get("spend") != "settled"
                or call.get("receipt_present") is not True
                or call.get("claim_present") is not True
            ):
                raise ValueError(
                    "proposal call is not accepted and settled for its label"
                )
        proposal = row.get("proposals")
        value = proposal.get("choices") if isinstance(proposal, dict) else None
        if not isinstance(value, list) or len(value) != len(LABELS):
            raise ValueError("expected three original box proposals")
        a, b, c = value
        choices[block_id] = _map_choices((a, b, c), 0)
    return choices


def seeds() -> dict[str, Choices]:
    """Load only the sealed original numbers, in A, B, C order for each block."""
    with RESULTS.open("rb") as report:
        raw = report.read(_RESULTS_BYTES + 1)
    if len(raw) != _RESULTS_BYTES or sha256(raw).hexdigest() != RESULTS_SHA256:
        raise ValueError("wrong size or SHA-256 for intentions results")
    return _seed_choices(json.loads(raw))


def arms(block: Block) -> tuple[str, str]:
    """Alternate policy order by the registered block parity."""
    return ("neutral", "owner") if block.arms[0] == "masked" else ("owner", "neutral")


def run_id(block: Block, arm: str) -> str:
    if arm not in _RULES:
        raise ValueError("invalid ownership policy")
    return f"bo-{block.id.removeprefix('bi-')}-{arm}"


def prompt(
    world: intent_packet.World, block: Block, label: int, arm: str, proposals: Choices
) -> str:
    """Project one role's history and the three sealed proposals into displayed numbers."""
    rule = _RULES.get(arm)
    if rule is None:
        raise ValueError("invalid ownership policy")
    case = world.seats[block.seat_index(label)]
    if world.instance != block.instance or case.instance != block.instance:
        raise ValueError("wrong world for block")
    context = boxes_choice.search_context(case, block.numbering, show_peer_boxes=True)
    shown = dict(zip(LABELS, _map_choices(proposals, block.numbering), strict=True))
    return "\n".join(
        (
            *context,
            "Opening a box again also uses 1 open.",
            f"Your label: {LABELS[label]}",
            "Sealed proposals: " + json.dumps(shown, separators=(",", ":")),
            rule,
            "This is a team convention, not a service restriction. All legal boxes remain available.",
            "Choose exactly 1 box to open next, at the same time as your teammates.",
            'Return JSON with only the integer field "box".',
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    runs = {
        run_id(block, arm): (block, arm)
        for block in intent_protocol.blocks()
        for arm in arms(block)
    }
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="list the paired run IDs and public input hashes")
    render = commands.add_parser("prompt", help="write one labeled seat's exact text")
    render.add_argument("run", choices=runs, metavar="RUN")
    render.add_argument("--seat", choices=LABELS, required=True)
    decode = commands.add_parser(
        "decode", help="undo the run's number map for a JSON action on stdin"
    )
    decode.add_argument("run", choices=runs, metavar="RUN")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            seeds()
            print(
                json.dumps(
                    {
                        "packet_sha256": intent_packet.PACKET_SHA256,
                        "results_sha256": RESULTS_SHA256,
                        "runs": list(runs),
                    },
                    separators=(",", ":"),
                )
            )
        elif args.command == "prompt":
            block, arm = runs[args.run]
            proposals = seeds()[block.id]
            world = intent_packet.load()[block.instance]
            sys.stdout.write(
                prompt(world, block, LABELS.index(args.seat), arm, proposals)
            )
        else:
            block, _ = runs[args.run]
            raw = sys.stdin.buffer.read(8193)
            if len(raw) > 8192:
                raise ValueError("invalid response size")
            box = boxes_choice.decode(raw.decode("utf-8"), block.numbering)
            print(json.dumps({"box": box}, separators=(",", ":")))
    except (KeyError, OSError, RecursionError, TypeError, ValueError):
        print("ownership protocol: invalid public input or action", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
