from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from .. import evidence
from ..capsule import encode_view
from .analysis import _comparison, summarize
from .packet import Block, Case, grid, parse

if TYPE_CHECKING:
    from .native_input import Corpus

_ARMS = ("bound", "anonymous")
_BAYS = frozenset("ABCD")
type _Pair = tuple[float | None, float | None, bool]


def _draws(block: Block, row: dict[str, Any]) -> tuple[dict[str, dict[str, str]], bool]:
    arms = row["outcome"]["arms"]
    selected = {name for name in _ARMS if arms[name]["lookup"] is not None}
    shared = len(selected) == 2 and bool(
        arms["bound"]["lookup"] == arms["anonymous"]["lookup"]
    )
    finals = {"both-decide"} if shared else {f"{name}-decide" for name in selected}
    received: set[str] = set()
    for turn in row["turns"]:
        stage = turn["run"].removeprefix(f"{block.key}-")
        if stage.endswith("-decide") and stage not in finals:
            raise ValueError("unexpected handoff final turn")
        if turn["status"] == "received":
            received.add(stage)
    draws: dict[str, dict[str, str]] = {}
    for name in _ARMS:
        arm = arms[name]
        if arm["status"] != "complete":
            continue
        stage = arm["decision_stage"]
        if stage not in received:
            raise ValueError("complete handoff decision has no received final turn")
        draws[stage] = arm["choices"]
    return draws, shared


def _grade(
    original: dict[str, Any],
    case: Case,
    ids: tuple[int, ...],
    choices: dict[str, str],
) -> dict[str, bool]:
    shipments = original["cases"]
    if max(ids) >= len(shipments):
        raise ValueError("packet record has no native shipment")
    rows = shipments.copy()
    for ship, record in zip(ids, case.records, strict=True):
        rows[ship] = shipments[ship] | {"chosen": choices[record.id]}
    scored = evidence._score(original | {"cases": rows})["rows"]
    results: dict[str, bool] = {}
    for ship, record in zip(ids, case.records, strict=True):
        row = scored[ship]
        source = shipments[ship]
        native_best = max(source["after"], key=source["utilities"].__getitem__)
        correct = row["optimal"]
        if (
            row["id"] != ship
            or record.best != native_best
            or correct != (choices[record.id] == record.best)
        ):
            raise ValueError("packet answer does not match the native score")
        results[record.id] = correct
    return results


def _verify_primary(claimed: object, native: dict[str, Any]) -> None:
    try:
        match = encode_view(claimed) == encode_view(native)
    except (TypeError, ValueError):
        match = False
    if not match:
        raise ValueError("registered handoff primary does not match the native score")


def stock(corpus: Corpus, report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema") != "seatlab.handoff-report/v1":
        raise ValueError("invalid handoff report schema")
    packet = parse(corpus.packet)
    if report.get("packet_sha256") != packet.sha256:
        raise ValueError("handoff report packet fingerprint mismatch")
    ids = corpus.ids
    if (
        not 2 <= len(ids) <= 8
        or any(type(ship) is not int or ship < 0 for ship in ids)
        or len(ids) != len(set(ids))
        or not _BAYS.issuperset(packet.choices)
    ):
        raise ValueError("invalid native replay records or choices")
    labels = tuple(f"S{ship}" for ship in ids)
    if corpus.worlds.keys() != {case.id for case in packet.cases} or any(
        tuple(record.id for record in case.records) != labels for case in packet.cases
    ):
        raise ValueError("packet records do not match the native replay corpus")
    blocks = grid(packet)
    public = report.get("outcomes")
    if not isinstance(public, list):
        raise ValueError("invalid handoff report outcomes")
    expected = summarize(blocks, public)["primary"]
    rows = {row["block"]: row for row in public}
    registered = {block.key: _draws(block, rows[block.key]) for block in blocks}
    pairs: dict[str, list[_Pair]] = defaultdict(list)
    observed_worlds: set[str] = set()
    observed_sources: set[int] = set()
    dossiers = shared_dossiers = native_choices = 0
    for block in blocks:
        case = block.case
        world = corpus.worlds[case.id]
        draws, shared = registered[block.key]
        graded = {
            stage: _grade(world, case, ids, choices) for stage, choices in draws.items()
        }
        if graded:
            observed_worlds.add(case.cluster)
            observed_sources.add(id(world))
        dossiers += len(graded)
        native_choices += sum(map(len, graded.values()))
        shared_dossiers += "both-decide" in graded
        for record in case.records:
            if record.old_version == record.new_version:
                continue
            arms = rows[block.key]["outcome"]["arms"]
            results = [graded.get(arms[name]["decision_stage"]) for name in _ARMS]
            bound, anonymous = (
                None if result is None else float(result[record.id])
                for result in results
            )
            pairs[case.cluster].append((bound, anonymous, shared))
    primary = _comparison(pairs)
    _verify_primary(expected, primary)
    _verify_primary(report.get("primary"), primary)
    return {
        "observed_worlds": len(observed_worlds),
        "observed_sources": len(observed_sources),
        "observed_dossiers": dossiers,
        "observed_native_choices": native_choices,
        "shared_dossiers": shared_dossiers,
        "mismatches": 0,
        "primary": primary,
    }
