import argparse
import json
from collections.abc import Iterable
from fractions import Fraction
from hashlib import sha256
from itertools import product
from pathlib import Path
from typing import Any, NamedTuple

from ..visibility import calendar_packet, calendar_protocol
from .protocol import ARMS, SEATS, WORLDS, Cell

_SCHEMA = "seatlab.calendar-presentation/v1"
_RESULTS = Path(__file__).resolve().parents[1] / "results"
_DEFAULT = _RESULTS / "calendar-presentation-replay.json"
_SOURCE = _RESULTS / "calendar-notice-live.json"
_SOURCE_SHA256 = "d3963c4a972fec850f9846f91b7e1427ed59d7b0779adf91948f37b0e5088856"
_PRESENTATIONS = ("raw", "list")
_CONTEXTS = tuple(product(WORLDS, ARMS))
_CELL_COUNT = len(_CONTEXTS) * len(_PRESENTATIONS)
_CELL_FIELDS = {
    "world",
    "notice",
    "presentation",
    "source_uuid",
    "source_result_sha256",
    "prompt_sha256",
    "status",
    "response_sha256",
    "slot",
    "served_model",
}
_ALPHA = Fraction(1, 20)
_Coordinate = tuple[str, str, str]


class _Replay(NamedTuple):
    prompt_sha256: str
    status: str
    slot: int | None


class _Score(NamedTuple):
    local: int | None
    joint: int | None


def _source_identities() -> dict[tuple[str, str], tuple[str, str]]:
    data = _SOURCE.read_bytes()
    if sha256(data).hexdigest() != _SOURCE_SHA256:
        raise ValueError(
            "native weekday-notice receipt does not match its pinned digest"
        )
    return {
        (row["world"], row["arm"]): (row["trial_uuid"], row["result_sha256"])
        for row in calendar_packet._json(data)["cells"]
    }


def _outcome(row: dict[str, Any], where: str) -> _Replay:
    status = row["status"]
    response = row["response_sha256"]
    slot = row["slot"]
    model = row["served_model"]
    if type(status) is not str or status not in ("accepted", "invalid", "unobserved"):
        raise ValueError(f"{where}: invalid status")
    if model is None:
        if status != "unobserved":
            raise ValueError(f"{where}: observed cell requires a served model")
    elif type(model) is not str or model.casefold() not in (
        "deepseek-flash",
        "deepseek-v4.1-flash-1017",
    ):
        raise ValueError(f"{where}: unexpected direct model")
    if status == "unobserved":
        if response is not None:
            raise ValueError(f"{where}: unobserved cell cannot have a response digest")
    else:
        calendar_packet._check_digest(response)
    if status == "accepted":
        slot = calendar_protocol._checked_slot(slot)
    elif slot is not None:
        raise ValueError(f"{where}: only accepted cells can have a slot")
    return _Replay(calendar_packet._check_digest(row["prompt_sha256"]), status, slot)


def _grid(receipt: dict[str, Any]) -> dict[_Coordinate, _Replay]:
    if not isinstance(receipt, dict) or receipt.get("schema") != _SCHEMA:
        raise ValueError("not a calendar presentation replay receipt")
    rows = receipt.get("cells")
    if not isinstance(rows, list) or len(rows) != _CELL_COUNT:
        raise ValueError(f"expected exactly {_CELL_COUNT} registered replay cells")
    sources = _source_identities()
    grid: dict[_Coordinate, _Replay] = {}
    for index, item in enumerate(rows):
        row = calendar_packet._object(item)
        if not _CELL_FIELDS <= row.keys():
            raise ValueError(f"cell {index}: missing required replay fields")
        world, notice, arm = row["world"], row["notice"], row["presentation"]
        if (
            any(type(value) is not str for value in (world, notice, arm))
            or world not in WORLDS
            or notice not in ARMS
            or arm not in _PRESENTATIONS
        ):
            raise ValueError(f"cell {index}: unregistered replay coordinate")
        coordinate = (world, notice, arm)
        where = f"{world}/{notice}/{arm}"
        if coordinate in grid:
            raise ValueError(f"{where}: duplicate replay coordinate")
        source = (row["source_uuid"], row["source_result_sha256"])
        expected = sources[world, notice]
        if any(type(value) is not str for value in source) or source != expected:
            raise ValueError(
                f"{where}: source identity differs from the native receipt"
            )
        grid[coordinate] = _outcome(row, where)
    for world, notice in _CONTEXTS:
        raw, listed = (grid[world, notice, arm] for arm in _PRESENTATIONS)
        if raw.prompt_sha256 == listed.prompt_sha256:
            raise ValueError(f"{world}/{notice}: raw and list prompts must differ")
    return grid


def _scores(grid: dict[_Coordinate, _Replay]) -> dict[_Coordinate, _Score]:
    worlds = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)
    starts = tuple(
        {
            seat: calendar_packet.starts(table)
            for seat, table in zip(SEATS, world.tables, strict=True)
        }
        for world in worlds
    )
    scores = {}
    for coordinate, replay in grid.items():
        world, notice, _ = coordinate
        if replay.slot is not None:
            cell = Cell(world, notice)
            local = replay.slot in starts[cell.index][cell.receiver]
            joint = local and replay.slot in starts[cell.index][cell.sender]
            score = _Score(int(local), int(joint))
        else:
            value = 0 if replay.status == "invalid" else None
            score = _Score(value, value)
        scores[coordinate] = score
    return scores


def _arm_counts(
    grid: dict[_Coordinate, _Replay], scores: dict[_Coordinate, _Score], arm: str
) -> dict[str, int]:
    coordinates = [(world, notice, arm) for world, notice in _CONTEXTS]
    accepted = sum(grid[key].status == "accepted" for key in coordinates)
    invalid = sum(grid[key].status == "invalid" for key in coordinates)
    return {
        "registered": len(coordinates),
        "known": accepted + invalid,
        "valid": accepted,
        "invalid": invalid,
        "unobserved": len(coordinates) - accepted - invalid,
        "local_successes": sum(scores[key].local == 1 for key in coordinates),
        "joint_successes": sum(scores[key].joint == 1 for key in coordinates),
    }


def _pair_counts(differences: Iterable[int | None]) -> dict[str, int]:
    counts = {"wins": 0, "ties": 0, "losses": 0, "unknown": 0}
    for difference in differences:
        if difference is None:
            counts["unknown"] += 1
        elif difference > 0:
            counts["wins"] += 1
        elif difference < 0:
            counts["losses"] += 1
        else:
            counts["ties"] += 1
    return counts


def _exact_test(differences: list[int]) -> dict[str, Any]:
    observed = sum(differences)
    assignments = 2 ** len(differences)
    tail = 0
    for signs in product((-1, 1), repeat=len(differences)):
        flipped = sum(sign * d for sign, d in zip(signs, differences, strict=True))
        tail += flipped >= observed
    p = Fraction(tail, assignments)
    return {
        "sum_world_list_minus_raw": observed,
        "mean_world_list_minus_raw": str(Fraction(observed, len(differences))),
        "one_sided_sign_flip_p": str(p),
        "sign_flip_tail": tail,
        "sign_flip_assignments": assignments,
        "outcome": "pass" if observed > 0 and p <= _ALPHA else "not_established",
    }


def _paired(scores: dict[_Coordinate, _Score], *, joint: bool) -> dict[str, Any]:
    pairs = {}
    for world, notice in _CONTEXTS:
        raw, listed = (scores[world, notice, arm] for arm in _PRESENTATIONS)
        base, treated = (
            (raw.joint, listed.joint) if joint else (raw.local, listed.local)
        )
        pairs[world, notice] = (
            None if base is None or treated is None else treated - base
        )
    worlds = {}
    for world in WORLDS:
        differences = [pairs[world, notice] for notice in ARMS]
        known = [difference for difference in differences if difference is not None]
        worlds[world] = sum(known) if len(known) == len(ARMS) else None
    observed = [difference for difference in worlds.values() if difference is not None]
    return {
        "pair_counts": _pair_counts(pairs.values()),
        "world_differences": worlds,
        "world_counts": _pair_counts(worlds.values()),
        **(
            _exact_test(observed)
            if len(observed) == len(WORLDS)
            else {
                "sum_world_list_minus_raw": None,
                "mean_world_list_minus_raw": None,
                "one_sided_sign_flip_p": None,
                "sign_flip_tail": None,
                "sign_flip_assignments": None,
                "outcome": "inconclusive",
            }
        ),
    }


def _coverage(grid: dict[_Coordinate, _Replay]) -> dict[str, int]:
    pair_coverage = {}
    valid_pairs = 0
    for world, notice in _CONTEXTS:
        raw, listed = (grid[world, notice, arm].status for arm in _PRESENTATIONS)
        pair_coverage[world, notice] = raw != "unobserved" and listed != "unobserved"
        valid_pairs += raw == listed == "accepted"
    return {
        "registered_pairs": len(_CONTEXTS),
        "known_pairs": sum(pair_coverage.values()),
        "valid_pairs": valid_pairs,
        "registered_worlds": len(WORLDS),
        "complete_worlds": sum(
            all(pair_coverage[world, notice] for notice in ARMS) for world in WORLDS
        ),
    }


def summarize(receipt: dict[str, Any]) -> dict[str, Any]:
    grid = _grid(receipt)
    scores = _scores(grid)
    return {
        "arms": {arm: _arm_counts(grid, scores, arm) for arm in _PRESENTATIONS},
        "coverage": _coverage(grid),
        "primary_local_legality": _paired(scores, joint=False),
        "secondary_joint_legality": _paired(scores, joint=True),
    }


def main() -> None:
    cli = argparse.ArgumentParser(
        description="Summarize the calendar presentation replay."
    )
    cli.add_argument("--receipt", type=Path, default=_DEFAULT)
    try:
        receipt = calendar_packet._json(cli.parse_args().receipt.read_bytes())
        result = summarize(receipt)
    except OSError:
        cli.error("cannot read the replay receipt or pinned evidence")
    except ValueError as exc:
        cli.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
