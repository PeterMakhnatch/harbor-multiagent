import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..native_study import DIGEST, as_choice, as_list, as_record, as_text, require
from . import inflight_verify as verify

USAGE = "usage: python3.12 -BS -m examples.ship_inflight.replay QUESTION PACK [--json | --totals]"
SCHEMA = "harbor.ship-inflight.public-cuts/v1"
REPORT_SCHEMA = "harbor.ship-inflight.replay/v1"
QUESTION_SCHEMA = "harbor.ship-inflight.controls/v1"
PIN_SCHEMA = "harbor.ship-inflight.source/v1"
SAMPLES = Path(__file__).resolve().parent / "samples"
PLACEHOLDER_RUN = "00000000-0000-4000-8000-000000000000"
REGISTERED = 16
MAX_PACK_BYTES = 1 << 17
MAX_SOURCE_BYTES = 1 << 16
TICKET = re.compile(r"si-[0-9a-f]{16}")
EXPECTED = ("outgoing_handoff", "relief_action", "settled", "drained")
EXPECTED += ("grade_before_drain", "reward", "outcome")
PACK_KEYS = frozenset(("schema", "question_sha256", "task_files", "rows"))
ROW_KEYS = frozenset(("ticket", "native", "cut"))
NATIVE_KEYS = frozenset(("seats", "reward_valid", "reward"))
SEAT_KEYS = frozenset(("outgoing", "relief"))
DISPOSITIONS = ("returned", "raised", "cancelled", "stop_requested")
CASE_KEYS = frozenset(("ticket", "outgoing", "relief", "fate", "channel", "expected"))
PUBLIC_CUT_KEYS = verify.CUT_KEYS - {"run"}
VERIFIERS = (
    ("tests/inflight_verify.py", Path(verify.__file__)),
    ("tests/verify.py", Path(verify.stock.__file__)),
)
LIMITS = (
    "native seats, reward_valid and reward are producer claims; this recipient cannot reauthenticate raw trials, locks or identities",
    "replayed grades come from the pinned repo verifier over redacted public cuts with a fixed placeholder run",
    "scripted controls only; no estimate of model behavior",
)

assert PLACEHOLDER_RUN == verify.stock._run(PLACEHOLDER_RUN)
assert len(EXPECTED) == 7 and "run" in verify.CUT_KEYS


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_bounded(path: Path, bound: int, what: str) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(bound + 1)
    except OSError as error:
        raise ValueError(f"{what} is unreadable: {error.strerror}") from None
    require(0 < len(data) <= bound, f"{what} is empty or too large")
    return data


def strict_json(data: bytes, what: str) -> object:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=verify.stock._unique_object,
            parse_constant=verify.stock._reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError, verify.CutError):
        raise ValueError(f"{what} is not strict JSON") from None


def load_source(question_path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    raw_pin = read_bounded(SAMPLES / "task-pin.json", MAX_SOURCE_BYTES, "task pin")
    pin = as_record(strict_json(raw_pin, "task pin"), "task pin")
    require(pin.get("schema") == PIN_SCHEMA, "task pin schema differs")
    task_files = as_text(pin.get("task_files"), "task pin task_files", DIGEST)
    files = as_record(pin.get("files"), "task pin files")
    for name, path in VERIFIERS:
        local = sha256(read_bounded(path, MAX_SOURCE_BYTES, name))
        require(files.get(name) == local, f"local {name} differs from the task pin")
    data = read_bounded(question_path, MAX_SOURCE_BYTES, "question")
    bundled = read_bounded(SAMPLES / "question.json", MAX_SOURCE_BYTES, "bundled question")
    require(data == bundled, "question is not the registered public question")
    question = as_record(strict_json(data, "question"), "question")
    task = as_record(pin.get("task"), "task pin task")
    require(question.get("schema") == QUESTION_SCHEMA, "question schema differs")
    require(question.get("task_name") == task.get("name"), "question names another task")
    cases = as_list(question.get("cases"), "question cases", REGISTERED, REGISTERED)
    for item in cases:
        case = as_record(item, "question case", CASE_KEYS)
        as_text(case["ticket"], "question ticket", TICKET)
        as_choice(case["fate"], "question fate", verify.FATES)
        as_choice(case["channel"], "question channel", verify.CHANNELS)
        as_record(case["expected"], "question expected", frozenset(EXPECTED))
    require(len({case["ticket"] for case in cases}) == REGISTERED, "tickets repeat")
    return sha256(data), task_files, cases


def check_native(native: dict[str, Any], ticket: str) -> None:
    seats = as_record(native["seats"], f"{ticket} native seats", SEAT_KEYS)
    known = all(value is None or value in DISPOSITIONS for value in seats.values())
    require(known, f"{ticket} native seat disposition is not a contract value")
    valid = native["reward_valid"]
    require(type(valid) is bool, f"{ticket} native reward_valid is not a boolean")
    reward = native["reward"]
    fits = type(reward) is int and reward in (0, 1) if valid else reward is None
    require(fits, f"{ticket} native reward contradicts reward_valid")


def load_rows(
    pack_path: Path, question_sha: str, task_files: str, tickets: list[str]
) -> dict[str, dict[str, Any]]:
    raw = read_bounded(pack_path, MAX_PACK_BYTES, "pack")
    pack = as_record(strict_json(raw, "pack"), "pack", PACK_KEYS)
    require(pack["schema"] == SCHEMA, "pack schema differs")
    bound = as_text(pack["question_sha256"], "pack question_sha256", DIGEST)
    require(bound == question_sha, "pack is bound to another question")
    files = as_text(pack["task_files"], "pack task_files", DIGEST)
    require(files == task_files, "pack is bound to other task files")
    rows: dict[str, dict[str, Any]] = {}
    for item in as_list(pack["rows"], "pack rows", 0, REGISTERED):
        row = as_record(item, "pack row", ROW_KEYS)
        ticket = as_text(row["ticket"], "row ticket", TICKET)
        require(ticket in tickets, f"row {ticket} is not registered")
        require(ticket not in rows, f"row {ticket} repeats")
        check_native(as_record(row["native"], f"{ticket} native", NATIVE_KEYS), ticket)
        cut = as_record(row["cut"], f"{ticket} cut")
        require("run" not in cut, f"{ticket} cut includes the raw world run")
        require(set(cut) == PUBLIC_CUT_KEYS, f"{ticket} cut fields differ")
        rows[ticket] = row
    return rows


def replay(case: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    record = {key: case[key] for key in ("ticket", "outgoing", "relief", "fate", "channel")}
    if row is None:
        return {**record, "status": "missing", "native": None, "replay": None, "reason": "row absent"}
    native = row["native"]
    seats = native["seats"]
    named = {f"{role}_disposition": seats[role] for role in sorted(SEAT_KEYS)}
    claims = {**named, "reward_valid": native["reward_valid"], "reward": native["reward"]}
    unknown = {**record, "status": "unknown", "native": claims, "replay": None}
    try:
        observation = verify.evaluate({**row["cut"], "run": PLACEHOLDER_RUN})
    except verify.CutError as error:
        return {**unknown, "reason": f"no grade: {error}"}
    if (observation["fate"], observation["channel"]) != (case["fate"], case["channel"]):
        return {**unknown, "reason": "cut world differs from the registered world"}
    replayed = {key: observation[key] for key in EXPECTED}
    unknown["replay"] = replayed
    if not native["reward_valid"]:
        return {**unknown, "reason": "native reward is not valid"}
    if native["reward"] != replayed["reward"]:
        return {**unknown, "reason": "native reward conflicts with the replayed reward"}
    misses = [key for key in EXPECTED if replayed[key] != case["expected"][key]]
    roles = sorted(SEAT_KEYS)
    misses += [f"{role} seat {seats[role]}" for role in roles if seats[role] not in ("returned", None)]
    lost = [role for role in roles if seats[role] is None]
    graded = {**record, "native": claims, "replay": replayed, "misses": misses}
    if misses:
        return {**graded, "status": "fail"}
    if lost:
        return {**graded, "status": "exit_unknown", "reason": f"seat exit unknown: {', '.join(lost)}"}
    return {**graded, "status": "pass"}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    def count(status: str) -> int:
        return sum(record["status"] == status for record in records)

    claims = [record["native"] for record in records if record["native"] is not None]
    worlds = [record["replay"] for record in records if record["replay"] is not None]
    supported, failed, lost = count("pass"), count("fail"), count("exit_unknown")
    verdict = "supported" if supported == REGISTERED else "incomplete"
    seats = [c[f"{role}_disposition"] for c in claims for role in sorted(SEAT_KEYS)]
    return {
        "cases": {
            "registered": len(records),
            "present": len(claims),
            "missing": count("missing"),
            "grade_unknown": count("missing") + count("unknown"),
            "graded": supported + failed + lost,
            "exit_unknown": lost,
            "eligible": supported + failed,
            "supported": supported,
            "failed": failed,
        },
        "native_claims": {
            "reward_valid": sum(claim["reward_valid"] for claim in claims),
            "reward_1": sum(claim["reward"] == 1 for claim in claims),
            "reward_0": sum(claim["reward"] == 0 for claim in claims),
            "both_returned": sum(
                c["outgoing_disposition"] == c["relief_disposition"] == "returned" for c in claims
            ),
            **{f"seats_{name}": seats.count(name) for name in DISPOSITIONS},
            "seats_unknown": seats.count(None),
        },
        "replayed_worlds": {
            "replayed": len(worlds),
            **{name: sum(w["outcome"] == name for w in worlds) for name in verify.OUTCOMES},
            "pre_drain_ne_final": sum(w["grade_before_drain"] != w["reward"] for w in worlds),
        },
        "verdict": "not_supported" if failed else verdict,
    }


def render(report: dict[str, Any]) -> str:
    lines = []
    for record in report["records"]:
        detail = record.get("reason") or ", ".join(record.get("misses", []))
        world = f"{record['fate']}/{record['channel']}"
        team = f"{record['outgoing']}+{record['relief']}"
        lines.append(f"{record['ticket']} {team:<22} {world:<16} {record['status']:<8} {detail}")
    for section in ("cases", "native_claims", "replayed_worlds"):
        pairs = " ".join(f"{key}={value}" for key, value in report["totals"][section].items())
        lines.append(f"{section}: {pairs}")
    lines.append(f"verdict: {report['totals']['verdict']}")
    lines += [f"limit: {limit}" for limit in LIMITS]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str]) -> int:
    flags = [arg for arg in argv if arg.startswith("--")]
    args = [arg for arg in argv if not arg.startswith("--")]
    mode = flags[0] if len(flags) == 1 else "text" if not flags else None
    if len(args) != 2 or mode not in ("text", "--json", "--totals"):
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        question_sha, task_files, cases = load_source(Path(args[0]))
        tickets = [case["ticket"] for case in cases]
        rows = load_rows(Path(args[1]), question_sha, task_files, tickets)
    except ValueError as error:
        sys.stderr.write(f"replay: {error}\n")
        return 2
    records = [replay(case, rows.get(case["ticket"])) for case in cases]
    totals = summarize(records)
    assert totals["cases"]["present"] == len(rows) <= REGISTERED
    bound = {"schema": REPORT_SCHEMA, "question_sha256": question_sha, "task_files": task_files}
    report = {**bound, "records": records, "totals": totals, "limits": list(LIMITS)}
    if mode == "--totals":
        sys.stdout.write(json.dumps(totals, indent=2) + "\n")
    else:
        sys.stdout.write(json.dumps(report, indent=2) + "\n" if mode == "--json" else render(report))
    return 0 if totals["verdict"] == "supported" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
