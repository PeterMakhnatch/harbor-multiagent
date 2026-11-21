import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import analysis, external_audit, packet

_MAX_PACKET = 4 * 1024 * 1024


def _packet(path: Path, sha256: str) -> packet.Packet:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_PACKET + 1)
    if len(raw) > _MAX_PACKET:
        raise ValueError("oversized handoff packet")
    return packet.parse(raw, sha256)


def _register(
    blocks: tuple[packet.Block, ...], indices: Sequence[int], report: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = report.get("outcomes")
    if (
        report.get("schema") != "seatlab.handoff-report/v1"
        or report.get("packet_sha256") != blocks[0].fingerprint
        or not isinstance(rows, list)
        or not 0 < len(indices) <= len(blocks)
        or any(type(i) is not int or not 0 <= i < len(blocks) for i in indices)
        or any(left >= right for left, right in pairwise(indices))
    ):
        raise ValueError("invalid handoff cohort register or indices")
    analysis._register(blocks, rows)
    if any(row["block"] != block.key for block, row in zip(blocks, rows, strict=True)):
        raise ValueError("handoff cohort rows are out of register order")
    external_audit._selected(rows, set(indices), blocks[0].fingerprint)
    return rows


def _comparison(groups: dict[str, list[analysis._Pair]]) -> dict[str, Any]:
    result = analysis._comparison(groups)
    result["by_world"] = result.pop("by_cluster")
    result["worlds_with_pairs"] = result.pop("clusters_with_pairs")
    result["enrolled_worlds"] = result.pop("enrolled_clusters")
    return result


def summarize(
    subject: packet.Packet, indices: Sequence[int], report: dict[str, Any]
) -> dict[str, Any]:
    blocks = packet.grid(subject)
    rows = _register(blocks, indices, report)
    enrolled = set(indices)
    lookup_pairs: dict[str, list[analysis._Pair]] = defaultdict(list)
    stock_pairs: dict[str, list[analysis._Pair]] = defaultdict(list)
    by_arm: dict[str, dict[str, Any]] = {
        policy: {
            "statuses": dict.fromkeys(analysis._ARM_STATUSES, 0),
            "valid_lookups": 0,
            "changed_selections": 0,
            "first_record_selections": 0,
        }
        for policy in analysis._POLICIES
    }
    stock = {
        policy: dict(enrolled=0, observed=0, correct=0) for policy in analysis._POLICIES
    }
    shared_final_complete = 0
    for index, (block, row) in enumerate(zip(blocks, rows, strict=True)):
        _, arms, shared = analysis._arms(block, row["outcome"])
        if index not in enrolled:
            continue
        first = block.case.records[0]
        revised = [r for r in block.case.records if r.old_version != r.new_version]
        if len(revised) != 1 or revised[0].id == first.id:
            raise ValueError("selected case needs exactly one non-first revised record")
        target = revised[0]
        sensitive = target.old_best != target.best
        shared_complete = shared and arms["bound"]["status"] == "complete"
        shared_final_complete += shared_complete
        lookups: list[float | None] = []
        scores: list[float | None] = []
        for policy in analysis._POLICIES:
            arm = arms[policy]
            lookup = arm["lookup"]
            counts = by_arm[policy]
            counts["statuses"][arm["status"]] += 1
            counts["valid_lookups"] += lookup is not None
            counts["changed_selections"] += lookup == target.id
            counts["first_record_selections"] += lookup == first.id
            lookups.append(None if lookup is None else float(lookup == target.id))
            if sensitive:
                choices = arm["choices"]
                score = (
                    None
                    if choices is None
                    else float(choices[target.id] == target.best)
                )
                scores.append(score)
                stock[policy]["enrolled"] += 1
                stock[policy]["observed"] += score is not None
                stock[policy]["correct"] += score == 1.0
        world = block.case.cluster
        lookup_pairs[world].append((lookups[0], lookups[1], False))
        if sensitive:
            stock_pairs[world].append((scores[0], scores[1], shared_complete))
    return {
        "schema": "seatlab.handoff-lookup-cohort/v1",
        "packet_sha256": subject.sha256,
        "indices": list(indices),
        "by_arm": by_arm,
        "primary": _comparison(lookup_pairs),
        "secondary_stock_sensitive": {"by_arm": stock, **_comparison(stock_pairs)},
        "shared_final_complete": shared_final_complete,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = external_audit._Arguments(
        description=(
            "Compare first-attempt lookups in registered non-first revision frames."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument(
        "--indices",
        type=int,
        nargs="+",
        required=True,
        help="Sorted registered indices.",
    )
    parser.add_argument("--report", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        result = summarize(
            _packet(args.packet, args.sha256),
            args.indices,
            external_audit._report(args.report),
        )
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        print("Cannot verify handoff lookup cohort.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
