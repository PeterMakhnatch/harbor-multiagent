from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from ..capsule import encode_view, view_hash
from ..evidence import TrialEvidence, read_trial_with_world
from ..export_capsules import reconstructed_capsules
from .packet import parse

_BAYS = ("A", "B", "C", "D")
_BRIEF = (
    "Choose one dock bay for each shipment. Maximize utility among currently "
    "available bays. Each record lists the available bays for one shipment."
)


@dataclass(frozen=True)
class Corpus:
    packet: bytes
    receipt: dict[str, Any]
    worlds: dict[str, dict[str, Any]]
    ids: tuple[int, ...]


class _NativeReport(TypedDict):
    available: list[str]


class _NativeRow(TypedDict):
    id: int
    utilities: dict[str, int]
    before: list[str]
    after: list[str]
    reports: list[_NativeReport]


def _source(
    path: Path, position: int
) -> tuple[TrialEvidence, dict[str, Any], dict[str, Any]]:
    try:
        evidence, dossier = read_trial_with_world(path)
    except (OSError, ValueError, RecursionError):
        raise ValueError(f"Source {position}: invalid scored native trial") from None
    if evidence["condition"] == "pooled":
        raise ValueError(f"Source {position}: pooled trials have no sender reports")
    try:
        capsules = reconstructed_capsules(dossier)
        trial_sha256 = view_hash((evidence, dossier))
    except (ValueError, RecursionError):
        raise ValueError(
            f"Source {position}: invalid acknowledgment or evidence"
        ) from None
    return (
        evidence,
        dossier,
        {
            "condition": evidence["condition"],
            "native_score": {
                field: evidence["score"][field]
                for field in ("episodes", "changed", "feasible", "optimal")
            },
            "trial_sha256": trial_sha256,
            "world_sha256": evidence["fingerprint"],
            "inventory_sha256": capsules["inventory"]["sha256"],
            "inventory_basis": capsules["inventory"]["basis"],
            "dispatch_basis": capsules["dispatch"]["basis"],
        },
    )


def _record(row: _NativeRow, revised: bool) -> dict[str, str]:
    current = row["reports"][1]["available"]
    prior = row["reports"][0]["available"] if revised else current
    utilities = row["utilities"]
    ranks = ", ".join(f"{bay}={utilities[bay]}" for bay in _BAYS)
    return {
        "id": f"S{row['id']}",
        "old_version": "1" if revised else "2",
        "new_version": "2",
        "before": f"Available bays: {', '.join(prior)}.",
        "after": f"Available bays: {', '.join(current)}.",
        "question": f"Utilities: {ranks}. Which available bay has the highest utility?",
        "old_best": max(
            row["before"] if revised else row["after"], key=utilities.__getitem__
        ),
        "best": max(row["after"], key=utilities.__getitem__),
    }


def build(trials: Sequence[Path], ids: Sequence[int]) -> Corpus:
    if not 2 <= len(ids) <= 8 or any(type(row) is not int or row < 0 for row in ids):
        raise ValueError("Select 2 to 8 distinct nonnegative ship IDs")
    selected = tuple(ids)
    if len(set(selected)) != len(selected):
        raise ValueError("Duplicate ship ID")
    if not 1 <= len(trials) * (len(selected) + 1) <= 256:
        raise ValueError("The replay register must contain 1 to 256 cases")

    seen: set[str] = set()
    clusters: dict[str, str] = {}
    cases: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    worlds: dict[str, dict[str, Any]] = {}
    for position, path in enumerate(trials, 1):
        evidence, dossier, receipt = _source(path, position)
        trial_id = evidence["trial_id"]
        if trial_id in seen:
            raise ValueError(f"Source {position}: repeated native trial ID")
        seen.add(trial_id)
        rows = cast("list[_NativeRow]", dossier["cases"])
        if max(selected) >= len(rows):
            raise ValueError(f"Source {position}: ship ID out of range")
        fingerprint = evidence["fingerprint"]
        cluster = f"w{fingerprint[:19]}"
        if clusters.setdefault(cluster, fingerprint) != fingerprint:
            raise ValueError("World cluster fingerprint collision")
        prefix = receipt["trial_sha256"][:16]
        for revised in (*selected, None):
            case_id = f"{prefix}-{revised if revised is not None else 's'}"
            if case_id in worlds:
                raise ValueError("Replay case ID collision")
            worlds[case_id] = dossier
            cases.append(
                {
                    "id": case_id,
                    "cluster": cluster,
                    "records": [_record(rows[row], row == revised) for row in selected],
                }
            )
        sources.append(receipt)

    raw = (
        encode_view(
            {
                "schema": "seatlab.handoff-input/v1",
                "brief": _BRIEF,
                "choices": _BAYS,
                "cases": cases,
            }
        )
        + b"\n"
    )
    packet = parse(raw)
    receipt = {
        "packet_sha256": packet.sha256,
        "registered_cases": len(packet.cases),
        "sources": sources,
    }
    return Corpus(raw, receipt, worlds, selected)
