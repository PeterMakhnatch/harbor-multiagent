import argparse
import json
import os
import re
import stat
import sys
from typing import Any, NoReturn, override

from . import seat_stream
from .seat_stream import Malformed

SCHEMA = "seatlab.workbench.seat-stream-cohort/v1"
REGISTRATION = "seatlab.native-seat-event-question/v1"
OBSERVATIONS = "seatlab.seat-life-observations/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.seat_stream_cohort"
MAX_BYTES = 1024 * 1024
MAX_EVENTS = 64
OBSERVED_MODE = 0o600
HEX = re.compile(r"[0-9a-f]{40}")
CHECKSUM = re.compile(r"[0-9a-f]{64}")
FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")
SIX = [
    "start",
    "environment-start",
    "agent-start",
    "agent-end",
    "verification-start",
    "end",
]
NATIVE = frozenset({*SIX, "cancel"})
ROSTERS = {
    "ship-handover": {"outgoing": True, "relief": True},
    "hello-seats": {"p1": True, "p2": False},
}
SETUP = ("setup_entered", "setup_returned", "run_entered", "oracle_returned")
SCRIPTS = {
    "plain": ((),),
    "return": ((*SETUP, "wrapper_returned"),),
    "delay": ((*SETUP, "delay_started", "delay_elapsed", "wrapper_returned"),),
    "hold": ((*SETUP, "hold_started", "hold_cancelled", "wrapper_cancelled"),),
    "cancel": ((*SETUP, "cancel_raised", "wrapper_cancelled"),),
    "raise": ((*SETUP, "error_raised", "wrapper_raised"),),
    "refuse": (("setup_entered", "setup_refused"),),
    "unstarted": ((), ("setup_entered",), ("setup_entered", "setup_returned")),
}
MARKS = frozenset(
    {
        "oracle_raised",
        "oracle_cancelled",
        "hold_limit_reached",
        *(mark for allowed in SCRIPTS.values() for marks in allowed for mark in marks),
    }
)
SHIP: dict[str, str | None] = {"outgoing": "returned", "relief": "returned"}
SPECS: dict[str, tuple[str, bool, dict[str, str | None], dict[str, str]]] = {
    "ship-default": (
        "ship-handover",
        False,
        SHIP,
        {"outgoing": "plain", "relief": "plain"},
    ),
    "ship-observed": (
        "ship-handover",
        True,
        SHIP,
        {"outgoing": "plain", "relief": "plain"},
    ),
    "support-return": (
        "hello-seats",
        True,
        {"p1": "returned", "p2": "returned"},
        {"p1": "delay", "p2": "return"},
    ),
    "support-held": (
        "hello-seats",
        True,
        {"p1": "returned", "p2": "stop_requested"},
        {"p1": "delay", "p2": "hold"},
    ),
    "support-cancel": (
        "hello-seats",
        True,
        {"p1": "returned", "p2": "cancelled"},
        {"p1": "delay", "p2": "cancel"},
    ),
    "support-fault": (
        "hello-seats",
        True,
        {"p1": "returned", "p2": "raised"},
        {"p1": "delay", "p2": "raise"},
    ),
    "team-deadline": (
        "hello-seats",
        True,
        {"p1": "stop_requested", "p2": "stop_requested"},
        {"p1": "hold", "p2": "hold"},
    ),
    "setup-refusal": (
        "hello-seats",
        True,
        {"p1": None, "p2": None},
        {"p1": "refuse", "p2": "unstarted"},
    ),
}
LIVE = "support-return"
STOCK = ("ship-default", "ship-observed")
SHAPE_FIELDS = frozenset(
    {"result_fields", "schema_version", "seat_fields", "task_checksum"}
)
CASE_FIELDS = frozenset(
    {
        "trial",
        "native_events",
        "reward",
        "reward_valid",
        "journal_present",
        "journal_mode",
        "witness",
    }
)
RECEIPT_FIELDS = frozenset(
    {
        "case",
        "prefix_rows",
        "trial_result_existed_before",
        "trial_result_existed_after",
        "cli_process_still_running",
    }
)
CAVEAT = (
    "Eight registered native tickets only. A missing or partial file makes its ticket unknown, never a pass "
    "or a dropped trial. Journal-versus-result agreement is reported apart from the scripted witness controls. "
    "The live claim covers one external pre-result snapshot of support-return. Grades are native verifier "
    "observations. Output holds only fixed names, counts, checked IDs and commits; no prompts, configs or free text."
)

Record = dict[str, Any]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Malformed(reason)


def _mode(path: str) -> int | None:
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise Malformed("a bundle path is unreadable") from error


def _load(path: str, label: str, schema: str) -> Record:
    document = seat_stream._strict_json(
        seat_stream._read(path, MAX_BYTES, label), f"{label} is not strict UTF-8 JSON"
    )
    _require(
        type(document) is dict and document.get("schema") == schema,
        f"{label} has another schema",
    )
    return document


def _registration(path: str) -> None:
    attempts = [
        {
            "case": case,
            "task": task,
            "observe": observe,
            "native_dispositions": expected,
        }
        for case, (task, observe, expected, _) in SPECS.items()
    ]
    native = _load(path, "registration", REGISTRATION).get("native_attempts")
    canonical = json.dumps(native, sort_keys=True) == json.dumps(
        attempts, sort_keys=True
    )
    _require(canonical, "registration native_attempts are not the fixed release")


def _case(case: Any, roster: dict[str, bool]) -> None:
    _require(
        type(case) is dict and case.keys() == CASE_FIELDS,
        "observation case does not have exactly the fixed fields",
    )
    _require(
        type(case["trial"]) is str
        and seat_stream.UUID.fullmatch(case["trial"]) is not None,
        "observation trial is not a UUID",
    )
    events = case["native_events"]
    known = (
        type(events) is list
        and len(events) <= MAX_EVENTS
        and all(type(e) is str and e in NATIVE for e in events)
    )
    _require(known, "observation native_events are not native event names")
    _require(
        case["reward"] is None or type(case["reward"]) in (int, float),
        "observation reward is not a number or null",
    )
    _require(
        case["reward_valid"] is None or type(case["reward_valid"]) is bool,
        "observation reward_valid is not a boolean or null",
    )
    _require(
        type(case["journal_present"]) is bool,
        "observation journal_present is not a boolean",
    )
    _require(
        case["journal_mode"] is None or type(case["journal_mode"]) is int,
        "observation journal_mode is not an integer or null",
    )
    witness = case["witness"]
    _require(
        type(witness) is dict and witness.keys() == roster.keys(),
        "observation witness seats are not the task roster",
    )
    for marks in witness.values():
        fixed = (
            type(marks) is list
            and len(marks) <= len(MARKS)
            and all(type(m) is str and m in MARKS for m in marks)
        )
        _require(fixed, "observation witness has an unknown marker")


def _observations(path: str) -> Record:
    document = _load(path, "observations", OBSERVATIONS)
    fields = {"schema", "source", "registered_at", "cases", "external_prefix"}
    _require(
        document.keys() == fields, "observations do not have exactly the fixed fields"
    )
    for key in ("source", "registered_at"):
        _require(
            type(document[key]) is str and HEX.fullmatch(document[key]) is not None,
            f"observations {key} is not a commit",
        )
    cases = document["cases"]
    _require(
        type(cases) is dict and cases.keys() <= SPECS.keys(),
        "observations name an unregistered case",
    )
    for name, case in cases.items():
        _case(case, ROSTERS[SPECS[name][0]])
    trials = [case["trial"] for case in cases.values()]
    _require(
        len(set(trials)) == len(trials), "observations reuse one trial for two cases"
    )
    receipt = document["external_prefix"]
    shaped = (
        type(receipt) is dict
        and receipt.keys() == RECEIPT_FIELDS
        and receipt["case"] == LIVE
    )
    _require(
        shaped, "observations external_prefix does not have exactly the fixed fields"
    )
    rows = receipt["prefix_rows"]
    _require(
        type(rows) is int and 1 <= rows <= seat_stream.MAX_ROWS,
        "observations prefix_rows is out of range",
    )
    _require(
        all(
            type(receipt[key]) is bool
            for key in RECEIPT_FIELDS - {"case", "prefix_rows"}
        ),
        "receipt flag is not a boolean",
    )
    return document


def _names(value: Any) -> bool:
    fields = type(value) is list and len(value) <= MAX_EVENTS
    fields = fields and all(
        type(name) is str and FIELD.fullmatch(name) is not None for name in value
    )
    return fields and len(set(value)) == len(value)


def _stock_shape(path: str) -> bool | None:
    if _mode(path) is None:
        return None
    raw = seat_stream._read(path, MAX_BYTES, "stock shapes")
    document = seat_stream._strict_json(raw, "stock shapes is not strict UTF-8 JSON")
    _require(
        type(document) is dict and document.keys() == set(STOCK),
        "stock shapes do not name exactly the stock cases",
    )
    for shape in document.values():
        _require(
            type(shape) is dict and shape.keys() == SHAPE_FIELDS,
            "stock shape does not have exactly the fixed fields",
        )
        _require(
            _names(shape["result_fields"]),
            "stock shape result_fields are not field names",
        )
        _require(
            type(shape["schema_version"]) is int,
            "stock shape schema_version is not an integer",
        )
        seats = shape["seat_fields"]
        roster = type(seats) is dict and seats.keys() == ROSTERS["ship-handover"].keys()
        _require(
            roster and all(_names(fields) for fields in seats.values()),
            "stock shape seat_fields are not the stock roster",
        )
        checksum = shape["task_checksum"]
        _require(
            type(checksum) is str and CHECKSUM.fullmatch(checksum) is not None,
            "stock shape task_checksum is not a digest",
        )
    default = document["ship-default"]
    required = {"id", "seat_results", "reward_valid"} <= set(default["result_fields"])
    return (
        default == document["ship-observed"]
        and default["schema_version"] == 1
        and required
    )


def _bind(trial: str | None, roster: dict[str, bool], case: Record, task: str) -> None:
    _require(trial == case["trial"], "native trial id does not match observations")
    _require(
        roster == ROSTERS[task], "native seat roster does not match the registered task"
    )


def _snapshot(raw: bytes, case: Record, task: str) -> Record | None:
    report = seat_stream.decode_snapshot(raw)
    if report["trial"] is None:
        return None
    _bind(
        report["trial"],
        {seat["name"]: seat["under_test"] for seat in report["seats"]},
        case,
        task,
    )
    return report


def _grade(case: Record) -> str:
    if case["reward_valid"] is None:
        return "unknown"
    if not case["reward_valid"]:
        return "invalid"
    return "one" if case["reward"] == 1 else "other"


def _seat_ok(seat: Record, compared: Record, disposition: str | None) -> bool:
    if disposition is None:
        return (
            seat["status"] == "not_observed" and compared["relation"] == "neither_known"
        )
    agree = (
        compared["relation"] == "agree"
        and compared["result_disposition"] == disposition
    )
    return seat["entered"] and seat["terminal"] == disposition and agree


def _ticket(bundle: str, name: str, case: Record | None) -> Record:
    task, observe, expected, scripts = SPECS[name]
    known = sum(disposition is not None for disposition in expected.values())
    ticket: Record = {
        "case": name,
        "state": "unknown",
        "missing": [],
        "journal": "unchecked",
        "known": known,
        "entries": None,
        "agree": None,
        "trace": None,
        "controls": None,
        "grade": "unknown",
        "events": None,
    }
    journal = os.path.join(bundle, "journals", f"{name}.jsonl")
    result = os.path.join(bundle, "results", f"{name}.json")
    has_journal = _mode(journal) is not None
    gaps = (
        ("observation", case is None),
        ("result", _mode(result) is None),
        ("journal", observe and not has_journal),
    )
    ticket["missing"] = [label for label, gone in gaps if gone]
    if case is None or ticket["missing"]:
        return ticket
    controls = all(
        tuple(case["witness"][seat]) in SCRIPTS[script]
        for seat, script in scripts.items()
    )
    ticket.update(
        grade=_grade(case),
        events="six" if case["native_events"] == SIX else "other",
        controls=controls,
    )
    if not observe:
        trial, roster = seat_stream._result_roster(result)
        _bind(
            trial,
            {seat: under_test for seat, (under_test, _) in roster.items()},
            case,
            task,
        )
        quiet = (
            not has_journal
            and not case["journal_present"]
            and case["journal_mode"] is None
        )
        ticket.update(
            journal="absent" if quiet else "unexpected",
            trace=all(roster[s][1] == d for s, d in expected.items()),
        )
    else:
        report = _snapshot(seat_stream.read_journal_bytes(journal), case, task)
        if report is None or report["state"] != "closed" or report["trailing_partial"]:
            ticket["journal"] = "partial"
            return ticket
        comparison = seat_stream.compare_result(report, result)
        seats = {seat["name"]: seat for seat in report["seats"]}
        compared = {seat["seat"]: seat for seat in comparison["seats"]}
        private = case["journal_present"] and case["journal_mode"] == OBSERVED_MODE
        ticket.update(
            journal="closed" if private else "unexpected",
            entries=sum(seat["entered"] for seat in report["seats"]),
            agree=comparison["counts"]["agree"],
            trace=all(_seat_ok(seats[s], compared[s], d) for s, d in expected.items()),
        )
    ship = task != "ship-handover" or (
        ticket["grade"] == "one" and ticket["events"] == "six"
    )
    met = (
        ticket["journal"] in ("closed", "absent")
        and ticket["trace"]
        and controls
        and ship
    )
    ticket["state"] = "pass" if met else "fail"
    return ticket


def _total(values: list[int | None]) -> int | None:
    return (
        None if None in values else sum(value for value in values if value is not None)
    )


def _cohort(tickets: dict[str, Record], shape: bool | None) -> Record:
    observed = [ticket for name, ticket in tickets.items() if SPECS[name][1]]
    default, stock, refusal = (
        tickets["ship-default"],
        tickets["ship-observed"],
        tickets["setup-refusal"],
    )
    counts = {
        "tickets": len(tickets),
        "unknown": sum(ticket["state"] == "unknown" for ticket in tickets.values()),
        "registered_known": sum(ticket["known"] for ticket in observed),
        "observed_entries": _total([ticket["entries"] for ticket in observed]),
        "observed_agree": _total([ticket["agree"] for ticket in observed]),
    }
    claims = {
        "tickets_complete": counts["unknown"] == 0 and counts["tickets"] == len(SPECS),
        "disabled_no_journal": default["journal"] == "absent",
        "default_two_returned_grade_one": default["trace"] is True
        and default["grade"] == "one",
        "stock_same_grade_and_six_events": stock["grade"] == default["grade"] == "one"
        and stock["events"] == default["events"] == "six",
        "seven_journals_closed": sum(
            ticket["journal"] == "closed" for ticket in observed
        )
        == len(observed)
        == 7,
        "journal_result_agree": counts["observed_agree"] == counts["registered_known"]
        and all(t["trace"] is True for t in observed),
        "setup_no_entry_or_terminal": refusal["trace"] is True
        and refusal["entries"] == 0,
        "entries_match_registration": counts["observed_entries"]
        == counts["registered_known"],
        "witness_controls_match": all(
            ticket["controls"] is True for ticket in tickets.values()
        ),
        "same_stock_result_shape": shape is True,
    }
    passed = all(claims.values()) and all(
        ticket["state"] == "pass" for ticket in tickets.values()
    )
    return {"claims": claims, "counts": counts, "pass": passed}


def _live(bundle: str, observations: Record) -> Record:
    receipt = observations["external_prefix"]
    before, after = (
        receipt["trial_result_existed_before"],
        receipt["trial_result_existed_after"],
    )
    claims = {
        "proper_prefix": False,
        "snapshot_p2_returned_p1_entered_only": False,
        "receipt_before_result": not before
        and not after
        and receipt["cli_process_still_running"],
        "p1_result_only_when_compared": False,
    }
    prefix = os.path.join(bundle, "journals", f"{LIVE}.prefix.jsonl")
    final = os.path.join(bundle, "journals", f"{LIVE}.jsonl")
    result = os.path.join(bundle, "results", f"{LIVE}.json")
    case = observations["cases"].get(LIVE)
    if case is None or _mode(prefix) is None or _mode(final) is None:
        return {"state": "unknown", "claims": claims, "pass": False}
    raw = seat_stream.read_journal_bytes(prefix)
    report = _snapshot(raw, case, SPECS[LIVE][0])
    if report is None:
        return {"state": "unknown", "claims": claims, "pass": False}
    whole = seat_stream.read_journal_bytes(final)
    statuses = {seat["name"]: seat["status"] for seat in report["seats"]}
    compared = (
        seat_stream.compare_result(report, result)["seats"]
        if _mode(result) is not None
        else []
    )
    claims.update(
        proper_prefix=len(raw) < len(whole) and whole.startswith(raw),
        snapshot_p2_returned_p1_entered_only=report["state"] == "open"
        and report["rows"] == receipt["prefix_rows"]
        and statuses == {"p1": "entered_only", "p2": "returned"},
        p1_result_only_when_compared={s["seat"]: s["relation"] for s in compared}
        == {"p1": "result_only", "p2": "agree"},
    )
    return {"state": "checked", "claims": claims, "pass": all(claims.values())}


def build(bundle: str) -> Record:
    mode = _mode(bundle)
    _require(mode is not None and stat.S_ISDIR(mode), "bundle is not a real directory")
    for child in ("journals", "results"):
        mode = _mode(os.path.join(bundle, child))
        _require(
            mode is None or stat.S_ISDIR(mode),
            "bundle subdirectory is not a real directory",
        )
    _registration(os.path.join(bundle, "registration.json"))
    observations = _observations(os.path.join(bundle, "observations.json"))
    shape = _stock_shape(os.path.join(bundle, "stock-result-shapes.json"))
    tickets = {
        name: _ticket(bundle, name, observations["cases"].get(name)) for name in SPECS
    }
    if shape is None:
        for name in STOCK:
            tickets[name].update(
                state="unknown", missing=[*tickets[name]["missing"], "stock_shapes"]
            )
    cohort, live = _cohort(tickets, shape), _live(bundle, observations)
    return {
        "schema": SCHEMA,
        "source": observations["source"],
        "registered_at": observations["registered_at"],
        "tickets": list(tickets.values()),
        "cohort": cohort,
        "live": live,
        "pass": cohort["pass"] and live["pass"],
        "caveat": CAVEAT,
    }


def _count(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _flag(value: bool | None) -> str:
    return "unknown" if value is None else "yes" if value else "no"


def text(report: Record) -> str:
    verdict = "pass" if report["pass"] else "not passed"
    lines = [
        f"seat stream cohort {verdict}: source {report['source']}; registered at {report['registered_at']}"
    ]
    for t in report["tickets"]:
        entries = f"{_count(t['entries'])}/{t['known']}"
        lines.append(
            f"  {t['case']}: {t['state']}; missing {','.join(t['missing']) or 'none'}; journal {t['journal']}; entries {entries}; "
            f"journal-result {_flag(t['trace'])}; witness {_flag(t['controls'])}; grade {t['grade']}"
        )
    lines.append(
        "counts: "
        + ", ".join(
            f"{name} {_count(count)}"
            for name, count in report["cohort"]["counts"].items()
        )
    )
    for title, block in (
        ("cohort", report["cohort"]),
        ("live before grade", report["live"]),
    ):
        lines.append(f"{title}: {'pass' if block['pass'] else 'not passed'}")
        lines.extend(
            f"  {name}: {'met' if met else 'not met'}"
            for name, met in block["claims"].items()
        )
    return "\n".join([*lines, report["caveat"]])


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        sys.stderr.write(
            "seat_stream_cohort: usage error; expected BUNDLE_DIR [--json]\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    cli = _Cli(
        prog=PROG,
        description="Check the eight registered native seat stream tickets in one bundle.",
    )
    cli.add_argument("bundle")
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    try:
        report = build(args.bundle)
    except Malformed as error:
        print(f"seat_stream_cohort: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
        if args.json
        else text(report)
    )
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
