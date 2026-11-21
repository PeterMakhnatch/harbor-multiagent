from hashlib import sha256
from typing import Any

from . import protocol

RUN_PREFIX = "attrank-"
PLAN_SCHEMA = "seatlab.native-attention-rank-plan/v1"
REPORT_SCHEMA = "seatlab.native-attention-rank-report/v1"
AGENT_IMPORT = "examples.seatlab.attention.ranked_agent:RankedSeat"

_SEEDS = tuple(range(517301, 517317))
_ARMS = ("shown", "ranked")
_BAYS = frozenset(protocol._BAYS)
_ALIASES = frozenset(protocol._ALIASES)
_UTILITIES = frozenset(range(1, len(_BAYS) + 1))


def grid() -> tuple[dict[str, Any], ...]:
    ranked_first = set(
        sorted(
            _SEEDS,
            key=lambda seed: sha256(
                f"seatlab-dispatch-rank-order-v1:{seed}".encode("ascii")
            ).digest(),
        )[: len(_SEEDS) // 2]
    )
    return tuple(
        {
            "cell_id": f"{RUN_PREFIX}s{seed}-r0-{arm}",
            "seed": seed,
            "repetition": 0,
            "condition": "reordered",
            "pairing": "MM",
            "arm": arm,
        }
        for seed in _SEEDS
        for arm in (_ARMS[::-1] if seed in ranked_first else _ARMS)
    )


def _rank_visible_rows(rows: object) -> dict[str, list[str]]:
    if not isinstance(rows, list) or len(rows) != protocol.EPISODES:
        raise ValueError("Expected all registered final prompt cases")
    ranked: dict[str, list[str]] = {}
    opened: list[str] = []
    required = {"alias", "utilities", "revision_1"}
    with_revision_2 = required | {"revision_2"}
    for row in rows:
        if not isinstance(row, dict) or row.keys() not in (required, with_revision_2):
            raise ValueError("Invalid final prompt case")
        alias, utilities = row["alias"], row["utilities"]
        if (
            not isinstance(alias, str)
            or alias not in _ALIASES
            or alias in ranked
            or not isinstance(utilities, dict)
            or utilities.keys() != _BAYS
            or any(type(value) is not int for value in utilities.values())
            or set(utilities.values()) != _UTILITIES
        ):
            raise ValueError("Invalid alias or utility in final prompt case")
        if "revision_2" in row:
            opened.append(alias)
            latest = row["revision_2"]
        else:
            latest = row["revision_1"]
        if (
            not isinstance(latest, list)
            or not 1 <= len(latest) <= len(_BAYS)
            or any(not isinstance(bay, str) or bay not in _BAYS for bay in latest)
            or len(set(latest)) != len(latest)
        ):
            raise ValueError("Invalid latest report in final prompt case")
        ranked[alias] = sorted(latest, key=utilities.__getitem__, reverse=True)
    protocol._open_pair(opened)
    return ranked


def final_prompt(shown: str, arm: str) -> str:
    if arm == "shown":
        return shown
    if arm != "ranked":
        raise ValueError("Unknown ranking arm")
    _, separator, payload = shown.partition("\n")
    if not separator:
        raise ValueError("Missing final prompt data")
    ranked = _rank_visible_rows(protocol._reply(payload, "cases"))
    return protocol._prompt(
        shown, {"latest_peer_reported_bays_by_decreasing_utility": ranked}
    )
