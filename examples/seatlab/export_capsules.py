"""Export approved Dispatch Handoff observations from validated native evidence.

Each JSONL record contains one capsule and a separate analyst index. Only the
capsule's view is actor input. Old dossiers have no client acknowledgments.
"""

import argparse
from pathlib import Path
from typing import Any, TypedDict

if __package__:
    from .capsule import Capsule, encode_view, make_capsule
    from .evidence import read_trial_with_world
else:
    from capsule import Capsule, encode_view, make_capsule
    from evidence import read_trial_with_world

_BAYS = "ABCD"
_ROLES = ("inventory", "dispatch")


class IndexedCapsule(TypedDict):
    native_trial_id: str
    seed: int
    condition: str
    role: str
    capsule: Capsule


def _bulletin(revision: int, available: list[str]) -> dict[str, object]:
    return {"revision": revision, "available": list(available)}


def reconstructed_views(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reconstruct the active seats' /view payloads from a validated world dossier.

    The returned mappings follow World.view's field order, and list order is
    unchanged. The dossier's sorted object keys do not reflect the field order
    in serve.py's HTTP response. Capsule digests instead use encode_view's
    sorted-key canonical bytes. Neither constitutes a captured response.
    """
    condition = manifest["settings"]["condition"]
    cases = manifest["cases"]
    pooled = condition == "pooled"
    views = {}
    if not pooled:
        views["inventory"] = {
            "role": "inventory",
            "condition": condition,
            "cases": [
                {
                    "id": case["id"],
                    "reports": [
                        _bulletin(1, case["before"]),
                        _bulletin(2, case["after"]),
                    ],
                }
                for case in cases
            ],
        }
    views["dispatch"] = {
        "role": "dispatch",
        "condition": condition,
        "cases": [
            {
                "id": case["id"],
                "utilities": {bay: case["utilities"][bay] for bay in _BAYS},
                "messages": [
                    _bulletin(report["revision"], report["available"])
                    for report in case["visible"]
                ],
                "pooled_available": list(case["after"]) if pooled else None,
            }
            for case in cases
        ],
    }
    return views


def reconstructed_capsules(manifest: dict[str, Any]) -> dict[str, Capsule]:
    """Seal active views; use bound basis only for an explicit matching receipt.

    A trusted producer may record observation_receipts as a mapping from role
    to {"ack_sha256": <canonical view hash>}. Missing roles stay reconstructed.
    Trust in the producer is external to this validation, not gained by hashing.
    """
    views = reconstructed_views(manifest)
    receipts = manifest.get("observation_receipts", {})
    if not isinstance(receipts, dict) or not receipts.keys() <= views.keys():
        raise ValueError("invalid observation receipts by active role")
    capsules = {}
    for role, view in views.items():
        if role not in receipts:
            capsules[role] = make_capsule(role, view)
            continue
        receipt = receipts[role]
        if not isinstance(receipt, dict) or receipt.keys() != {"ack_sha256"}:
            raise ValueError("an observation receipt requires an explicit ack_sha256")
        ack = receipt["ack_sha256"]
        if not isinstance(ack, str):
            raise ValueError("an observation receipt requires a SHA-256 acknowledgment")
        capsules[role] = make_capsule(
            role, view, basis="bound", acknowledged_sha256=ack
        )
    return capsules


def export_trial(path: Path | str) -> list[IndexedCapsule]:
    """Read native evidence, then export only indexed actor observations."""
    trial, manifest = read_trial_with_world(path)
    return [
        {
            "native_trial_id": trial["trial_id"],
            "seed": trial["seed"],
            "condition": trial["condition"],
            "role": role,
            "capsule": capsule,
        }
        for role, capsule in reconstructed_capsules(manifest).items()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_dirs", metavar="TRIAL", type=Path, nargs="+")
    parser.add_argument("--role", choices=_ROLES, help="select one active role")
    args = parser.parse_args()
    rows = []
    trial_ids = set()
    for index, path in enumerate(args.trial_dirs, 1):
        try:
            indexed = export_trial(path)
        except (OSError, ValueError, RecursionError):
            parser.exit(1, f"trial {index}: cannot export validated observations\n")
        trial_id = indexed[0]["native_trial_id"]
        if trial_id in trial_ids:
            parser.exit(1, f"trial {index}: duplicate native trial UUID\n")
        trial_ids.add(trial_id)
        rows.extend(
            row for row in indexed if args.role is None or row["role"] == args.role
        )
    for row in rows:
        print(encode_view(row).decode("ascii"))


if __name__ == "__main__":
    main()
