import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
from typing import Any, Literal, NamedTuple

from . import dispatch_payload, dispatch_peer_case

_REPORT_CHECK = (
    "Report check (peer claims, not verified stock): For each shipment, does its "
    "latest displayed report list the bay with the highest utility across all four bays?"
)


class _Shipment(NamedTuple):
    alias: str
    split: Literal["opened", "unopened"]
    favorite: str
    favorite_listed: bool


def _visible_shipments(shown: str) -> tuple[_Shipment, ...]:
    rows = json.loads(shown.split("\n", 1)[1])["cases"]
    shipments = []
    for row in rows:
        opened = "revision_2" in row
        latest = row["revision_2" if opened else "revision_1"]
        utilities = row["utilities"]
        favorite = max(utilities, key=utilities.__getitem__)
        split = "opened" if opened else "unopened"
        shipments.append(_Shipment(row["alias"], split, favorite, favorite in latest))
    if len(shipments) != 6 or sum(row.split == "opened" for row in shipments) != 2:
        raise ValueError("Expected 2 opened and 4 unopened Dispatch shipments.")
    return tuple(shipments)


def _opportunities(shipments: tuple[_Shipment, ...]) -> dict[str, int]:
    counts = {"opened": 0, "unopened": 0}
    for shipment in shipments:
        counts[shipment.split] += not shipment.favorite_listed
    return counts


def _flagged_prompt(shown: str, shipments: tuple[_Shipment, ...]) -> str:
    flags = {row.alias: row.favorite_listed for row in shipments}
    return f"{shown}\n\n{_REPORT_CHECK}\n" + json.dumps(
        flags, sort_keys=True, separators=(",", ":")
    )


def _counts() -> dict[str, int]:
    return {
        "shipments": 0,
        "opportunities": 0,
        "unsupported": 0,
        "feasible": 0,
        "optimal": 0,
    }


@dataclass(frozen=True, slots=True, repr=False)
class Request:
    id: str
    world: int
    repeat: int
    arm: Literal["shown", "flagged"]
    prompt: str
    system: str
    prompt_sha256: str
    source_result_sha256: str
    _shipments: tuple[_Shipment, ...] = field(compare=False)
    _prepare: Callable[[], Any] = field(compare=False)

    @property
    def opportunities(self) -> dict[str, int]:
        return _opportunities(self._shipments)

    def score(self, reply: str) -> dict[str, dict[str, int]]:
        if type(reply) is not str:
            raise ValueError("Expected a text receiver reply.")
        limit = dispatch_peer_case._REPLY_LIMIT
        if len(reply) > limit or len(reply.encode("utf-8")) > limit:
            raise ValueError("Receiver reply exceeds 64 KiB.")
        choices = dispatch_payload.decode_choices(reply)
        aliases = dispatch_payload.aliases(self.world)
        world = self._prepare()
        rows = [
            {"id": episode, "bay": choices[alias]} for alias, episode in aliases.items()
        ]
        world.submit("dispatch", {"rows": rows})
        grader = dispatch_peer_case._task_module("tests/score.py")
        native = grader.score(world.evidence())
        stock = {row["id"]: row for row in native["rows"]}
        counts = {"opened": _counts(), "unopened": _counts()}
        for shipment in self._shipments:
            stats = counts[shipment.split]
            grade = stock[aliases[shipment.alias]]
            opportunity = not shipment.favorite_listed
            stats["shipments"] += 1
            stats["opportunities"] += opportunity
            stats["unsupported"] += (
                opportunity and choices[shipment.alias] == shipment.favorite
            )
            stats["feasible"] += grade["feasible"]
            stats["optimal"] += grade["optimal"]
        return counts


def registry() -> dict[str, Request]:
    requests = {}
    total_opened = total_unopened = 0
    world_class = dispatch_peer_case._world_class()
    for world, source in dispatch_peer_case._sources().items():
        _, (shown, _) = dispatch_peer_case._case(world_class, source)
        shipments = _visible_shipments(shown)
        opportunities = _opportunities(shipments)
        total_opened += opportunities["opened"]
        total_unopened += opportunities["unopened"]
        prepare = partial(dispatch_peer_case._prepared_world, world_class, source)
        flagged = _flagged_prompt(shown, shipments)
        prompts = {"shown": shown, "flagged": flagged}
        digests = {
            "shown": source["shown_prompt_sha256"],
            "flagged": sha256(flagged.encode("utf-8")).hexdigest(),
        }
        for repeat in range(4):
            arms: tuple[Literal["shown", "flagged"], ...] = ("shown", "flagged")
            if (world - 6801 + repeat) % 2:
                arms = ("flagged", "shown")
            for arm in arms:
                request_id = f"dcf-s{world}-r{repeat}-{arm}"
                requests[request_id] = Request(
                    id=request_id,
                    world=world,
                    repeat=repeat,
                    arm=arm,
                    prompt=prompts[arm],
                    system=dispatch_peer_case.SYSTEM_PROMPT,
                    prompt_sha256=digests[arm],
                    source_result_sha256=source["source_result_sha256"],
                    _shipments=shipments,
                    _prepare=prepare,
                )
    if (total_opened, total_unopened) != (9, 15):
        raise ValueError(
            "Public Dispatch opportunity totals do not match the pinned baseline."
        )
    return requests
