"""Match native role turns to the originally served model, preserving multiplicity."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_ROLES = ("sender", "receiver")
_USAGE = ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")
_HASH = re.compile(r"[0-9a-f]{64}")


def _hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("invalid model turn commitment")
    return value


def _coordinates(row: dict[str, Any]) -> tuple[int, int]:
    world, direction = row["world"], row["direction"]
    if type(world) is not int or type(direction) is not int or direction not in (0, 1):
        raise ValueError("invalid service coordinates")
    return world, direction


def verify(
    rows: list[dict[str, Any]], receipts: list[dict[str, Any]], models: dict[str, str]
) -> dict[str, Any]:
    """Check each (block, role, input, reply) exactly once, never by list position."""
    if set(models) != set(_ROLES) or not all(
        isinstance(v, str) and v for v in models.values()
    ):
        raise ValueError("give both expected service models")
    wanted: Counter[tuple[int, int, str, str, str]] = Counter()
    seen: set[tuple[int, int]] = set()
    for row in rows:
        world, direction = _coordinates(row)
        if (
            (world, direction) in seen
            or type(row["repetition"]) is not int
            or row["repetition"] != 0
        ):
            raise ValueError("duplicate or unregistered native block")
        seen.add((world, direction))
        for turn in row["turns"]:
            role = turn["role"]
            if role not in _ROLES:
                raise ValueError("unknown native role")
            wanted[
                (
                    world,
                    direction,
                    role,
                    _hash(turn["prompt_sha256"]),
                    _hash(turn["response_sha256"]),
                )
            ] += 1
    result: dict[str, dict[str, Any]] = {
        role: {"served_model": name, "matched": 0, **dict.fromkeys(_USAGE, 0)}
        for role, name in models.items()
    }
    for envelope in receipts:
        world, direction = _coordinates(envelope)
        role, receipt = envelope["role"], envelope["receipt"]
        if (
            set(envelope) != {"world", "direction", "role", "receipt"}
            or role not in models
        ):
            raise ValueError("unknown service envelope")
        if (
            set(receipt)
            != {"schema", "prompt_sha256", "reply_sha256", "served_model", "usage"}
            or receipt["schema"] != "seatlab.omp-seat-receipt/v1"
            or receipt["served_model"] != models[role]
        ):
            raise ValueError("wrong service identity")
        key = (
            world,
            direction,
            role,
            _hash(receipt["prompt_sha256"]),
            _hash(receipt["reply_sha256"]),
        )
        if wanted[key] <= 0:
            raise ValueError("unmatched or repeated service receipt")
        usage = receipt["usage"]
        if (
            set(usage) != set(_USAGE)
            or any(type(usage[k]) is not int or usage[k] < 0 for k in _USAGE)
            or usage["total_tokens"]
            != usage["prompt_tokens"] + usage["completion_tokens"]
            or usage["cached_tokens"] > usage["prompt_tokens"]
        ):
            raise ValueError("invalid service token counts")
        wanted[key] -= 1
        result[role]["matched"] += 1
        for k in _USAGE:
            result[role][k] += usage[k]
    if any(wanted.values()):
        raise ValueError("native model turns are missing service receipts")
    return {
        "schema": "seatlab.calendar-query-service/v1",
        "blocks": len(seen),
        "roles": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native", type=Path, default=_RESULTS / "calendar-query-native.json"
    )
    parser.add_argument(
        "--receipts",
        type=Path,
        default=_RESULTS / "calendar-query-service-receipts.json",
    )
    parser.add_argument("--sender", default="openai-codex/gpt-6.2-sol")
    parser.add_argument("--receiver", default="openai-codex/gpt-6.2-astra")
    args = parser.parse_args(argv)
    try:
        native = json.loads(args.native.read_bytes())
        services = json.loads(args.receipts.read_bytes())
        if (
            native["schema"] != "seatlab.calendar-query-archive/v1"
            or services["schema"] != "seatlab.calendar-query-service-receipts/v1"
        ):
            raise ValueError("wrong archive kind")
        result = verify(
            native["rows"],
            services["receipts"],
            {"sender": args.sender, "receiver": args.receiver},
        )
    except (KeyError, OSError, TypeError, ValueError, RecursionError):
        print(
            "The native turns and model service receipts do not match.", file=sys.stderr
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
