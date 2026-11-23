import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

PINNED_SOURCE = "dbec43ff757c86c17077e61b37c7f584c880bdee"
WORLDS = 12
PICKS = (1, 2, 3)
ROLES = ("sales", "finance")
MAX_FILE_BYTES = 1 << 20
EVIDENCE_KEYS = {"schema", "sales", "finance"}
ROLE_KEYS = {"pick", "view_sha256"}
PAYOFF_KEYS = {"schema", "world", "prefix_fingerprint", "source", "options"}
OPTION_KEYS = {"pick", "world_score", "multiple"}
HEX64 = re.compile(r"[0-9a-f]{64}")


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    return dict(pairs) if len({key for key, _ in pairs}) == len(pairs) else fail("duplicate key")


def load(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        fail(f"{path}: larger than {MAX_FILE_BYTES} bytes")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=fail)
    return value if isinstance(value, dict) else fail(f"{path}: expected a JSON object")


def is_pick(value: object) -> bool:
    return type(value) is int and value in PICKS


def is_hex(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(path: Path, checks: dict[str, bool]) -> None:
    bad = [name for name, ok in checks.items() if not ok]
    if bad:
        fail(f"{path}: invalid {', '.join(bad)}")


def evidence(path: Path) -> dict[str, int]:
    record = load(path)
    if record.keys() != EVIDENCE_KEYS:
        fail(f"{path}: expected exactly {sorted(EVIDENCE_KEYS)}")
    rows = [record[role] for role in ROLES]
    require(path, {
        "schema": record["schema"] == "seatlab.market-native-evidence/v1",
        "roles": all(isinstance(row, dict) and row.keys() == ROLE_KEYS for row in rows),
    })
    require(path, {
        "pick": all(is_pick(row["pick"]) for row in rows),
        "view_sha256": all(is_hex(row["view_sha256"]) for row in rows),
        "distinct_views": rows[0]["view_sha256"] != rows[1]["view_sha256"],
    })
    return {role: record[role]["pick"] for role in ROLES}


def payoffs(path: Path) -> list[dict[str, Any]]:
    record = load(path)
    if record.keys() != PAYOFF_KEYS:
        fail(f"{path}: expected exactly {sorted(PAYOFF_KEYS)}")
    options = record["options"] if isinstance(record["options"], list) else []
    picks = [item.get("pick") if isinstance(item, dict) else None for item in options]
    require(path, {
        "schema": record["schema"] == "seatlab.market-payoffs/v1",
        "source": record["source"] == PINNED_SOURCE,
        "world": type(record["world"]) is int and 0 <= record["world"] < WORLDS,
        "prefix_fingerprint": is_hex(record["prefix_fingerprint"]),
        "options": picks == list(PICKS) and all(
            item.keys() == OPTION_KEYS and is_number(item["world_score"])
            and is_number(item["multiple"]) and 0 <= item["world_score"] <= 1
            for item in options),
    })
    return options


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("payoffs", type=Path)
    args = parser.parse_args()
    try:
        picks = evidence(args.evidence)
        options = payoffs(args.payoffs)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    reward = float(options[picks["finance"] - 1]["world_score"])
    assert 0.0 <= reward <= 1.0
    sys.stdout.write(json.dumps({"reward": reward}, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
