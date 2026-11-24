import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

from market_handoff.timing_messages import read_price, read_sales

SALES = "sales"
FINANCE = "finance"
SEATS = (SALES, FINANCE)
TARGETS = (100, 160)
MAX_WORLD = 24
STEPS = ("sales_view", "sales_resolved", "finance_view", "finance_resolved")
EVENT_KEYS = {
    "sales_view": {"n", "step", "view_sha256"},
    "sales_resolved": {"n", "step", "reply", "status", "report"},
    "finance_view": {"n", "step", "view_sha256", "report"},
    "finance_resolved": {"n", "step", "reply", "status", "pick"},
}
SCENARIO_SCHEMA = "seatlab.report-budget-scenario/v1"
SCENARIO_KEYS = {"schema", "world", "target", "checkpoint_sha256", "sales", "finance"}
LOG_SCHEMA = "seatlab.report-budget-native/v1"
LOG_KEYS = {
    "schema",
    "world",
    "target",
    "checkpoint_sha256",
    "events",
    "rejected",
    "rejected_total",
}
REJECTED_KEYS = {"path", "status", "error"}
MAX_REJECTED = 32
PICKS = (1, 2, 3)
PARSE_ERRORS = (ValueError, TypeError, LookupError, RecursionError)
MAX_FILE_BYTES = 1 << 24


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


def same(left: object, right: object) -> bool:
    return canonical(left) == canonical(right)


def load(path: Path, keys: set[str]) -> dict[str, Any]:
    data = path.read_bytes()
    if not 0 < len(data) <= MAX_FILE_BYTES:
        fail(f"{path.name}: size is out of bounds")
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=unique,
        parse_float=finite,
        parse_constant=fail,
    )
    if not isinstance(value, dict) or set(value) != keys:
        fail(f"{path.name}: keys are invalid")
    if canonical(value) != data:
        fail(f"{path.name}: not canonical")
    return value


def scenario(path: Path) -> dict[str, Any]:
    scene = load(path, SCENARIO_KEYS)
    world, target = scene["world"], scene["target"]
    if scene["schema"] != SCENARIO_SCHEMA:
        fail("scenario schema is invalid")
    if type(world) is not int or not 0 <= world <= MAX_WORLD:
        fail("scenario world is invalid")
    if type(target) is not int or target not in TARGETS:
        fail("scenario target is invalid")
    if not all(isinstance(scene[seat], dict) and scene[seat] for seat in SEATS):
        fail("scenario views are invalid")
    return scene


def sales_outcome(reply: str | None) -> tuple[str, dict[str, Any] | None]:
    if reply is None:
        return "unanswered", None
    try:
        return "accepted", read_sales(reply)
    except PARSE_ERRORS:
        return "invalid", None


def finance_outcome(reply: str | None) -> tuple[str, int | None]:
    if reply is None:
        return "unanswered", None
    try:
        return "accepted", read_price(reply)
    except PARSE_ERRORS:
        return "invalid", None


def reply_of(event: dict[str, Any]) -> str | None:
    reply = event["reply"]
    if reply is not None and type(reply) is not str:
        fail("reply must be a string or null")
    return reply


def check_event(scene: dict[str, Any], event: dict[str, Any], report: object) -> object:
    step = event["step"]
    if step == "sales_view":
        if event["view_sha256"] != digest({"view": scene[SALES]}):
            fail("sales view digest does not match the staged view")
        return report
    if step == "sales_resolved":
        status, parsed = sales_outcome(reply_of(event))
        if event["status"] != status or not same(event["report"], parsed):
            fail("sales resolution does not follow from its reply")
        return parsed
    if step == "finance_view":
        if not same(event["report"], report):
            fail("finance received a report other than the resolved one")
        if event["view_sha256"] != digest({"view": scene[FINANCE], "report": report}):
            fail("finance view digest does not match the staged view")
        return report
    status, pick = finance_outcome(reply_of(event))
    if event["status"] != status or not same(event["pick"], pick):
        fail("finance resolution does not follow from its reply")
    return report


def check_rejected(log: dict[str, Any]) -> None:
    rejected, total = log["rejected"], log["rejected_total"]
    if type(rejected) is not list or type(total) is not int:
        fail("rejected record is malformed")
    if len(rejected) != min(total, MAX_REJECTED):
        fail("rejected count is inconsistent")
    for row in rejected:
        if not isinstance(row, dict) or set(row) != REJECTED_KEYS:
            fail("rejected row is malformed")


def score(log_path: Path, scenario_path: Path) -> float:
    scene = scenario(scenario_path)
    log = load(log_path, LOG_KEYS)
    if log["schema"] != LOG_SCHEMA:
        fail("log schema is invalid")
    for key in ("world", "target", "checkpoint_sha256"):
        if not same(log[key], scene[key]):
            fail(f"log {key} does not match the scenario")
    check_rejected(log)
    events = log["events"]
    if type(events) is not list or len(events) > len(STEPS):
        fail("events are malformed")
    report: object = None
    for index, value in enumerate(events):
        step = STEPS[index]
        if not isinstance(value, dict) or set(value) != EVENT_KEYS[step]:
            fail(f"event {index + 1} is malformed")
        event = cast(dict[str, Any], value)
        if (
            event["step"] != step
            or event["n"] != index + 1
            or type(event["n"]) is not int
        ):
            fail(f"event {index + 1} is out of order")
        report = check_event(scene, event, report)
    if len(events) != len(STEPS):
        return 0.0
    return 1.0 if events[-1]["pick"] in PICKS else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    try:
        reward = score(args.log, args.scenario)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps({"reward": reward}, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
