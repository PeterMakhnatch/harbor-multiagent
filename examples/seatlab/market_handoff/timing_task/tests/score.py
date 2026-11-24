import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

SALES = "sales"
FINANCE = ("finance_a", "finance_b")
SEATS = (SALES, *FINANCE)
PICKS = (1, 2, 3)
EVENTS = ("view", "commit", "receipt", "ack")
NEEDS = {
    (SALES, "view"): None,
    (SALES, "commit"): (SALES, "view"),
    **{(seat, "view"): (SALES, "commit") for seat in FINANCE},
    **{(seat, "commit"): (seat, "view") for seat in FINANCE},
    **{(seat, "receipt"): (seat, "commit") for seat in FINANCE},
    **{(seat, "ack"): (seat, "receipt") for seat in FINANCE},
}
COMPLETE_EVENTS = len(NEEDS)
SCENARIO_SCHEMA = "seatlab.price-timing-scenario/v1"
EVIDENCE_SCHEMA = "seatlab.price-timing-native/v1"
JOURNAL_SCHEMA = "seatlab.price-timing-journal/v1"
TOKEN_DOMAIN = "seatlab.price-timing-receipt/v1"
SCENARIO_KEYS = {"schema", "early", "checkpoint_sha256", "sales", "finance"}
RECORD_KEYS = {
    "schema",
    "early",
    "checkpoint_sha256",
    "report",
    "events",
    "picks",
    "acknowledged",
}
EVENT_KEYS = {"n", "seat", "event", "view_sha256"}
REASON_MAX_CHARS = 160
TOKEN_CHARS = 12
MAX_FILE_BYTES = 1 << 22
HEX64 = re.compile(r"[0-9a-f]{64}")


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    return (
        dict(pairs)
        if len({key for key, _ in pairs}) == len(pairs)
        else fail("duplicate key")
    )


def finite(text: str) -> float:
    value = float(text)
    return value if math.isfinite(value) else fail("non-finite number")


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path: Path, keys: set[str]) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        fail(f"{path}: larger than {MAX_FILE_BYTES} bytes")
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=unique,
        parse_float=finite,
        parse_constant=fail,
    )
    if not isinstance(value, dict) or value.keys() != keys:
        fail(f"{path}: expected exactly {sorted(keys)}")
    if canonical(value) != data:
        fail(f"{path}: not canonical JSON")
    return value


def json_object(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def is_pick(value: object) -> bool:
    return type(value) is int and value in PICKS


def scenario(path: Path) -> dict[str, Any]:
    record = load(path, SCENARIO_KEYS)
    if (
        record["schema"] != SCENARIO_SCHEMA
        or record["early"] not in FINANCE
        or not isinstance(record["checkpoint_sha256"], str)
        or not HEX64.fullmatch(record["checkpoint_sha256"])
        or not all(
            isinstance(record[key], dict) and record[key]
            for key in ("sales", "finance")
        )
    ):
        fail(f"{path}: invalid scenario")
    return record


def report_ok(value: object) -> bool:
    report = json_object(value)
    if report is None or report.keys() != {"pick", "reason"}:
        return False
    reason = report["reason"]
    return (
        is_pick(report["pick"])
        and isinstance(reason, str)
        and 0 < len(reason) <= REASON_MAX_CHARS
        and reason.isprintable()
    )


def token(
    scene: dict[str, Any],
    seat: str,
    pick: int,
    view_sha256: str,
    report: dict[str, Any],
) -> str:
    return digest(
        {
            "domain": TOKEN_DOMAIN,
            "checkpoint_sha256": scene["checkpoint_sha256"],
            "seat": seat,
            "pick": pick,
            "view_sha256": view_sha256,
            "report": report,
        }
    )[:TOKEN_CHARS]


def expected_digest(
    scene: dict[str, Any],
    record: dict[str, Any],
    seat: str,
    event: str,
    seen: dict[tuple[str, str], str],
) -> str:
    report, early = record["report"], scene["early"]
    if event in ("commit", "ack"):
        return seen[(seat, "view" if event == "commit" else "receipt")]
    if event == "view" and seat == SALES:
        return digest({"observation": scene["sales"]})
    if event == "view":
        return digest(
            {
                "observation": scene["finance"],
                "report": report if seat == early else None,
            }
        )
    pick = record["picks"].get(seat)
    if not is_pick(pick):
        fail(f"{seat}: receipt without a committed price")
    receipt = {
        "report": None if seat == early else report,
        "token": token(scene, seat, pick, seen[(seat, "commit")], report),
    }
    return digest(receipt)


def replay(scene: dict[str, Any], record: dict[str, Any]) -> dict[tuple[str, str], str]:
    if (
        record["early"] != scene["early"]
        or record["checkpoint_sha256"] != scene["checkpoint_sha256"]
    ):
        fail("record is bound to another scenario")
    events = record["events"]
    if not isinstance(events, list) or len(events) > COMPLETE_EVENTS:
        fail("events must be a bounded list")
    seen: dict[tuple[str, str], str] = {}
    for index, item in enumerate(events):
        row = json_object(item)
        if row is None or row.keys() != EVENT_KEYS:
            fail(f"event {index}: invalid keys")
        seat, event, number, view_sha256 = (
            row["seat"],
            row["event"],
            row["n"],
            row["view_sha256"],
        )
        if not isinstance(seat, str) or not isinstance(event, str):
            fail(f"event {index}: invalid seat or event name")
        key = (seat, event)
        if type(number) is not int or number != index + 1 or key not in NEEDS:
            fail(f"event {index}: invalid sequence number or seat event")
        if key == (SALES, "commit") and not report_ok(record["report"]):
            fail("sales commit without a valid report")
        expected = expected_digest(scene, record, *key, seen)
        if not isinstance(view_sha256, str) or view_sha256 != expected:
            fail(f"event {index}: digest does not match the materialized payload")
        seen[key] = view_sha256
    return seen


def consistent(
    scene: dict[str, Any], record: dict[str, Any]
) -> dict[tuple[str, str], str]:
    picks, acknowledged = record["picks"], record["acknowledged"]
    if not isinstance(picks, dict) or not all(
        is_pick(value) for value in picks.values()
    ):
        fail("picks are invalid")
    seen = replay(scene, record)
    if (record["report"] is None) == ((SALES, "commit") in seen):
        fail("report presence disagrees with the sales commit")
    if set(picks) != {seat for seat in FINANCE if (seat, "commit") in seen}:
        fail("picks disagree with the finance commits")
    if acknowledged != [seat for seat in FINANCE if (seat, "ack") in seen]:
        fail("acknowledged disagrees with the acks")
    return seen


def score(evidence: Path, journal_path: Path, scenario_path: Path) -> float:
    scene = scenario(scenario_path)
    journal = load(journal_path, RECORD_KEYS)
    if journal["schema"] != JOURNAL_SCHEMA:
        fail("journal schema is invalid")
    consistent(scene, journal)
    if not evidence.exists():
        return 0.0
    record = load(evidence, RECORD_KEYS)
    if record["schema"] != EVIDENCE_SCHEMA:
        fail("evidence schema is invalid")
    seen = consistent(scene, record)
    if len(seen) != COMPLETE_EVENTS or list(record["picks"]) != list(FINANCE):
        fail("evidence is incomplete")
    if {**journal, "schema": EVIDENCE_SCHEMA} != record:
        fail("journal and evidence disagree")
    return 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("journal", type=Path)
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    try:
        reward = score(args.evidence, args.journal, args.scenario)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps({"reward": reward}, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
