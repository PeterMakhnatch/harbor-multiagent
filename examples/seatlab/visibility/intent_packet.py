"""Export the three-seat histories at the registered box receiver cutoffs."""

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import boxes_choice
from . import boxes_packet as bp

PACKET = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "muster-box-intentions-packet.json"
)
PACKET_SHA256 = "a633e7fc6673036bca0989957396250f8db8b77f816be64b4f084dc210423e34"
_SCHEMA = "seatlab.box-intentions.packet/v1"
_ELIGIBLE = (1, 2, 6)
_COVERAGE = {"eligible": 3, "no_budget": 5, "no_prior_own": 2}


@dataclass(frozen=True, slots=True)
class World:
    instance: int
    seats: tuple[bp.Case, bp.Case, bp.Case]


def _status(counts: tuple[int, int, int]) -> str:
    if 0 in counts:
        if bp._BUDGET in counts:
            raise ValueError("conflicting intentions packet exclusion reasons")
        return "no_prior_own"
    if bp._BUDGET in counts:
        return "no_budget"
    return "eligible"


def _case(instance: int, row: dict[str, Any]) -> bp.Case:
    if not bp._object(row, {"own", "peers", "budget_left", "earlier_observation"}):
        raise ValueError("invalid intentions packet seat fields")
    own, peers, budget = row["own"], row["peers"], row["budget_left"]
    if (
        not isinstance(own, list)
        or not 1 <= len(own) < bp._BUDGET
        or any(
            not isinstance(item, list) or len(item) != 3 or not bp._observation(*item)
            for item in own
        )
        or not isinstance(peers, list)
        or not 1 <= len(peers) <= 2 * (bp._BUDGET - 1)
        or not all(bp._box(box) for box in peers)
        or sorted(set(peers)) != peers
        or not bp._integer(budget, bp._BUDGET - len(own), bp._BUDGET - len(own))
        or row["earlier_observation"] is not True
    ):
        raise ValueError("invalid intentions packet seat evidence")
    return bp.Case(
        instance,
        tuple((box, room, flag) for box, room, flag in own),
        tuple(peers),
        budget,
        True,
    )


def _excluded_opens(row: dict[str, Any]) -> tuple[int, ...]:
    if not bp._object(row, {"own_boxes", "budget_left"}):
        raise ValueError("invalid intentions packet excluded seat fields")
    own = row["own_boxes"]
    if (
        not isinstance(own, list)
        or len(own) > bp._BUDGET
        or not all(bp._box(box) for box in own)
        or not bp._integer(
            row["budget_left"], bp._BUDGET - len(own), bp._BUDGET - len(own)
        )
    ):
        raise ValueError("invalid intentions packet excluded seat evidence")
    return tuple(cast("list[int]", own))


def _public(raw: bytes, receiver: dict[int, bp.Case | None]) -> dict[int, World]:
    packet = bp._decode(raw)
    if (
        not bp._object(packet, {"schema", "receiver_packet_sha256", "worlds"})
        or packet["schema"] != _SCHEMA
        or packet["receiver_packet_sha256"] != boxes_choice.PACKET_SHA256
        or not isinstance(packet["worlds"], list)
        or len(packet["worlds"]) != len(bp._INSTANCES)
        or bp._encode(packet) != raw
    ):
        raise ValueError("invalid canonical intentions packet")

    worlds: dict[int, World] = {}
    coverage: Counter[str] = Counter()
    for instance, row in zip(bp._INSTANCES, packet["worlds"], strict=True):
        if (
            not bp._object(row, {"instance", "status", "seats"})
            or not bp._integer(row["instance"], instance, instance)
            or not isinstance(row["status"], str)
            or row["status"] not in _COVERAGE
            or not isinstance(row["seats"], list)
            or len(row["seats"]) != len(bp._SEATS)
        ):
            raise ValueError("invalid intentions packet world or seat roster")
        status = row["status"]
        coverage[status] += 1
        seats = None
        if status == "eligible":
            seats = (
                _case(instance, row["seats"][0]),
                _case(instance, row["seats"][1]),
                _case(instance, row["seats"][2]),
            )
            bp._room_flags((room, flag) for case in seats for _, room, flag in case.own)
            opened = tuple(tuple(box for box, _, _ in case.own) for case in seats)
        else:
            opened = tuple(_excluded_opens(seat) for seat in row["seats"])

        if (
            _status((len(opened[0]), len(opened[1]), len(opened[2]))) != status
            or bp._BOX_COUNT - len(set().union(*opened)) < 3
        ):
            raise ValueError("invalid intentions packet eligibility evidence")
        peers = tuple(
            tuple(sorted(set(opened[(i + 1) % 3]) | set(opened[(i + 2) % 3])))
            for i in range(3)
        )
        if seats is not None and any(
            case.peers != peers[i] for i, case in enumerate(seats)
        ):
            raise ValueError("intentions packet peer boxes disagree across seats")

        prior = receiver[instance]
        target = (instance - 1) % len(bp._SEATS)
        if (
            prior is None
            or tuple(box for box, _, _ in prior.own) != opened[target]
            or prior.peers != peers[target]
            or prior.budget_left != bp._BUDGET - len(opened[target])
            or (seats is not None and prior.own != seats[target].own)
        ):
            raise ValueError(
                "intentions packet disagrees with the registered receiver cutoff"
            )
        # The receiver's witness bit also required a later anchor reply. The
        # intentions witness is independent and requires only pre-cut replies.
        if seats is not None:
            worlds[instance] = World(instance, seats)

    if tuple(worlds) != _ELIGIBLE or coverage != _COVERAGE:
        raise ValueError("intentions packet does not match the registered coverage")
    return worlds


def _receiver_cases() -> dict[int, bp.Case | None]:
    return bp.load(boxes_choice.PACKET, boxes_choice.PACKET_SHA256)


def load(path: Path = PACKET, expected_sha256: str = PACKET_SHA256) -> dict[int, World]:
    """Validate the whole packet and expose only the three eligible worlds."""
    if not isinstance(expected_sha256, str) or not bp._SHA256.fullmatch(
        expected_sha256
    ):
        raise ValueError("expected a lowercase SHA-256 digest")
    try:
        raw = bp._read(path, bp._PUBLIC_LIMIT)
    except (OSError, ValueError):
        raise ValueError(
            "cannot read the intentions packet as a bounded regular file"
        ) from None
    if bp._sha(raw) != expected_sha256:
        raise ValueError("intentions packet SHA-256 does not match")
    try:
        return _public(raw, _receiver_cases())
    except RecursionError:
        raise ValueError("intentions packet JSON is too deeply nested") from None


def _export_world(
    source: bp._Snapshot, directory: Path, result: bp._NativeRecord, instance: int
) -> dict[str, object]:
    bp._check_selected_metadata(source, directory, result)
    timing = bp._timing(result)
    opens = bp._service(source.read(directory / "verifier/events.jsonl"), timing)
    _, _, anchor = bp._cut(opens, bp._SEATS[(instance - 1) % len(bp._SEATS)])
    own: dict[str, list[bp._Open]] = {seat: [] for seat in bp._SEATS}
    # Native service order breaks timestamp ties. Never include the anchor or
    # an action after it, even if its timestamp equals the anchor's timestamp.
    for action in opens:
        if action is anchor:
            break
        own[action.seat].append(action)
    status = _status((len(own["p1"]), len(own["p2"]), len(own["p3"])))
    seats: list[dict[str, object]] = []
    for seat, actions in own.items():
        budget = bp._BUDGET - len(actions)
        if status != "eligible":
            seats.append(
                {"own_boxes": [action.box for action in actions], "budget_left": budget}
            )
            continue
        matches = bp._terminal_matches(
            source.read(directory / "seats" / seat / "agent/trajectory.json"),
            timing.seats[seat],
            actions,
            before=anchor.time,
        )
        if len(matches) != len(actions):
            raise ValueError("prior open lacks a pre-cut terminal observation")
        seats.append(
            {
                "own": [action.observation for action in actions],
                "peers": sorted(
                    {
                        action.box
                        for other, past in own.items()
                        if other != seat
                        for action in past
                    }
                ),
                "budget_left": budget,
                "earlier_observation": True,
            }
        )
    return {"instance": instance, "status": status, "seats": seats}


def export(root: Path) -> bytes:
    """Read the same pinned trials and cutoffs as the paid receiver packet."""
    receiver = _receiver_cases()
    source = bp._Snapshot()
    selected = bp._registered_trials(source, root)
    worlds = []
    for instance in bp._INSTANCES:
        try:
            worlds.append(_export_world(source, *selected[instance], instance))
        except (
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            OSError,
            RecursionError,
        ):
            raise ValueError(
                f"cannot verify the registered intentions prefix for world {instance:02}"
            ) from None
    source.check()
    raw = bp._encode(
        {
            "schema": _SCHEMA,
            "receiver_packet_sha256": boxes_choice.PACKET_SHA256,
            "worlds": worlds,
        }
    )
    _public(raw, receiver)
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser("export")
    export_cmd.add_argument("root", type=Path, help="retained team-3 job directory")
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("file", type=Path, nargs="?", default=PACKET)
    verify_cmd.add_argument("--expect-sha256", default=PACKET_SHA256)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            sys.stdout.buffer.write(export(args.root))
        else:
            worlds = load(args.file, args.expect_sha256)
            counts = {"registered": len(bp._INSTANCES), "eligible": list(worlds)}
            print(bp._encode(counts).decode("ascii"))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
