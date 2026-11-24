import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..native_study import as_record, require
from ..ship_inflight import replay
from . import receiver_options

USAGE = "usage: python3.12 -BS -m examples.ship_handover.receiver_archive QUESTION PACK [--json]"
SCHEMA = "harbor.shipping.receiver-archive/v1"
FRONTIER_SCHEMA = "harbor.shipping.receiver-options/v1"
RECEIVER_QUESTION = (
    Path(__file__).resolve().parent / "samples" / "receiver-options" / "question.json"
)
RECEIVER_QUESTION_SCHEMA = "harbor.shipping.receiver-options-question/v1"
WORLD = "inflight"
KNOWLEDGE = ("opaque", "original_only")
VIEWS = ("received", "empty")
ACTIONS = ("stop", "close", "fresh", "resume_received", "replace_received")
RECEIVED_ONLY = frozenset(("resume_received", "replace_received"))
STATUSES = ("missing", "unqualified", "no_decision", "outside_template")
STATUSES += ("unobserved", "off_view", "analyzed")
RECEIPTS = ("absent", "before_decision", "after_decision", "without_decision")
CONTROL = ("ticket", "outgoing", "relief", "fate", "channel")
BASIS = {
    "observed": "native seat and reward claims plus the public cut ledger after the pinned inflight verifier replays it",
    "conditional": "receiver-options frontier grade sets for the observed view and action under a stated knowledge model",
}
LIMITS = (
    "retrospective classification of 16 scripted Oracle controls, not an independent replication or a model result",
    "public cuts record handoff, inbox and resolve events only; relief GET /brief, /status and the ready flag are not recorded and are never inferred",
    "an inbox event shows what the handler served, not what a seat read, perceived or intended",
    "original and draft labels in handoff and served fields are assessor-side identities the relief seat cannot see",
    "a missing resolve is reported as no_decision, never as the frontier stop action",
    "the pinned verifier rejects a repeated inbox, an inbox before the handoff, or a served value that contradicts the handoff and channel; such rows are unqualified",
    "original_only grades apply only where the sender handed over the original; otherwise that model is not applicable",
    "grade sets are ranges over compatible hidden states, never probabilities",
)

assert set(RECEIVED_ONLY) <= set(ACTIONS) and len(STATUSES) == 7


def load_frontier() -> tuple[dict[str, Any], list[str]]:
    plan = receiver_options.frontier()
    assert plan["schema"] == FRONTIER_SCHEMA
    world = plan["worlds"][WORLD]
    for knowledge in KNOWLEDGE:
        for view in VIEWS:
            cell = world[knowledge][view]
            where = f"frontier {WORLD}/{knowledge}/{view}"
            require(cell["census"] == "valid", f"{where} census is {cell['census']}")
            require(
                len(cell["views"]) == 1,
                f"{where} has {len(cell['views'])} relief views, not 1",
            )
            problems = cell["problems"]
            require(
                not problems,
                f"{where} has {len(problems)} problems, first: {problems[0] if problems else ''}",
            )
            actions = cell["actions"]
            assert set(actions) <= set(ACTIONS) and actions
            assert view == "received" or not RECEIVED_ONLY & set(actions)
            assert cell["guaranteed"] == sorted(set(cell["guaranteed"]))
            assert set(cell["guaranteed"]) <= set(actions)
            for act in actions.values():
                grades = act["grades"]
                assert (
                    grades and grades == sorted(set(grades)) and set(grades) <= {0, 1}
                )
    for view in VIEWS:
        assert set(world["opaque"][view]["actions"]) == set(
            world["original_only"][view]["actions"]
        )
    limits = plan["limits"]
    assert isinstance(limits, list) and all(isinstance(limit, str) for limit in limits)
    return world, limits


def load_receiver_question() -> str:
    data = replay.read_bounded(
        RECEIVER_QUESTION, replay.MAX_SOURCE_BYTES, "receiver question"
    )
    question = as_record(
        replay.strict_json(data, "receiver question"), "receiver question"
    )
    require(
        question.get("schema") == RECEIVER_QUESTION_SCHEMA,
        "receiver question schema differs",
    )
    return replay.sha256(data)


def verified_history(row: dict[str, Any], replayed: dict[str, Any]) -> dict[str, Any]:
    observation = replay.verify.evaluate({**row["cut"], "run": replay.PLACEHOLDER_RUN})
    assert {key: observation[key] for key in replay.EXPECTED} == replayed
    return observation


def first(events: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    found = [event for event in events if event["kind"] == kind]
    assert len(found) <= 1
    return found[0] if found else None


def receipt(inbox: dict[str, Any] | None, resolve: dict[str, Any] | None) -> str:
    if inbox is None:
        return "absent"
    if resolve is None:
        return "without_decision"
    return "before_decision" if inbox["seq"] < resolve["seq"] else "after_decision"


def served_reference(
    handoff: dict[str, Any], reads: list[dict[str, Any]], channel: str
) -> str:
    assert len(reads) == 1
    served = reads[0]["choice"]
    assert served == (handoff["choice"] if channel == "deliver" else "none")
    return served


def frontier_action(
    observation: dict[str, Any], resolve: str, served: str
) -> tuple[str | None, str]:
    replaced = observation["replace"]
    if resolve == "close" or (resolve == "fresh" and replaced is None):
        return resolve, ""
    action = "replace_received" if resolve == "fresh" else "resume_received"
    used = replaced if resolve == "fresh" else resolve
    assert used in ("original", "draft")
    if served == "none":
        return None, f"{action} names a reference while the served inbox was empty"
    if used != served:
        return None, f"{action} names a reference other than the one served"
    return action, ""


def conditional(
    world: dict[str, Any], view: str, action: str, grade: int, offered: str
) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for knowledge in KNOWLEDGE:
        if knowledge == "original_only" and offered != "original":
            models[knowledge] = {
                "applies": False,
                "reason": f"sender handed over {offered}, not the original",
            }
            continue
        cell = world[knowledge][view]
        grades = cell["actions"][action]["grades"]
        models[knowledge] = {
            "applies": True,
            "grades": grades,
            "guaranteed": cell["guaranteed"],
            "action_guaranteed": action in cell["guaranteed"],
            "grade_in_range": grade in grades,
        }
    return models


def observed_fields(
    claims: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    events = observation["events"]
    handoff, inbox, resolve = (
        first(events, kind) for kind in ("handoff", "inbox", "resolve")
    )
    return {
        "native": claims,
        "grade": observation["reward"],
        "outcome": observation["outcome"],
        "relief_action": observation["relief_action"],
        "sender_handoff": "absent" if handoff is None else handoff["choice"],
        "served": None if inbox is None else inbox["choice"],
        "receipt": receipt(inbox, resolve),
        "view": None,
        "action": None,
    }


def placement(
    observed: dict[str, Any], observation: dict[str, Any], channel: str
) -> tuple[str, str]:
    events = observation["events"]
    handoff, resolve = first(events, "handoff"), first(events, "resolve")
    if resolve is None:
        return "no_decision", "native ledger has no resolve"
    if handoff is None:
        return "outside_template", "no accepted sender handoff"
    if handoff["seq"] > resolve["seq"]:
        return "outside_template", "sender handoff follows the decision"
    reads = [
        event
        for event in events
        if event["kind"] == "inbox" and event["seq"] < resolve["seq"]
    ]
    if not reads:
        return (
            "unobserved",
            f"no witnessed inbox before the decision (receipt {observed['receipt']})",
        )
    served = served_reference(handoff, reads, channel)
    observed["view"] = "empty" if served == "none" else "received"
    action, reason = frontier_action(observation, resolve["choice"], served)
    if action is None:
        return "off_view", reason
    observed["action"] = action
    return "analyzed", ""


def classify(
    case: dict[str, Any], row: dict[str, Any] | None, world: dict[str, Any]
) -> dict[str, Any]:
    entry: dict[str, Any] = {key: case[key] for key in CONTROL}
    record = replay.replay(case, row)
    if record["status"] == "missing":
        return {
            **entry,
            "status": "missing",
            "reason": "row absent",
            "observed": None,
            "conditional": None,
        }
    if record["status"] != "pass":
        reason = record.get("reason") or ", ".join(record["misses"])
        observed = {"native": record["native"], "grade": None}
        return {
            **entry,
            "status": "unqualified",
            "reason": reason,
            "observed": observed,
            "conditional": None,
        }
    assert row is not None
    observation = verified_history(row, record["replay"])
    observed = observed_fields(record["native"], observation)
    status, reason = placement(observed, observation, case["channel"])
    models: dict[str, Any] | None = None
    if status == "analyzed":
        if observed["action"] not in world["opaque"][observed["view"]]["actions"]:
            status, reason = (
                "off_view",
                f"{observed['action']} is not offered in the {observed['view']} view",
            )
            observed["action"] = None
        else:
            grade, offered = observed["grade"], observed["sender_handoff"]
            models = conditional(
                world, observed["view"], observed["action"], grade, offered
            )
    assert status in STATUSES
    return {
        **entry,
        "status": status,
        "reason": reason,
        "observed": observed,
        "conditional": models,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    qualified = [
        r["observed"]
        for r in records
        if r["observed"] is not None and r["observed"]["grade"] is not None
    ]
    analyzed = [r for r in records if r["status"] == "analyzed"]
    cases = {status: sum(r["status"] == status for r in records) for status in STATUSES}
    models: dict[str, dict[str, int]] = {}
    for knowledge in KNOWLEDGE:
        found = [r["conditional"][knowledge] for r in analyzed]
        applied = [model for model in found if model["applies"]]
        models[knowledge] = {
            "applies": len(applied),
            "not_applicable": len(found) - len(applied),
            "grade_in_range": sum(model["grade_in_range"] for model in applied),
            "grade_out_of_range": sum(not model["grade_in_range"] for model in applied),
            "action_guaranteed": sum(model["action_guaranteed"] for model in applied),
        }
    deviations = sum(model["grade_out_of_range"] for model in models.values())
    verdict = (
        "deviation"
        if deviations
        else "within_range"
        if analyzed
        else "no_eligible_evidence"
    )
    return {
        "cases": {
            "registered": len(records),
            **cases,
            "uncertain": len(records) - len(analyzed),
        },
        "observed": {
            "qualified": len(qualified),
            "grade_1": sum(o["grade"] == 1 for o in qualified),
            "grade_0": sum(o["grade"] == 0 for o in qualified),
            **{
                f"receipt_{name}": sum(o["receipt"] == name for o in qualified)
                for name in RECEIPTS
            },
            **{
                f"sender_{name}": sum(o["sender_handoff"] == name for o in qualified)
                for name in ("absent", "original", "draft", "none")
            },
            **{
                f"view_{view}": sum(r["observed"]["view"] == view for r in analyzed)
                for view in VIEWS
            },
            **{
                f"action_{act}": sum(r["observed"]["action"] == act for r in analyzed)
                for act in ACTIONS
            },
            "reads_unrecorded": len(analyzed),
        },
        "conditional": models,
        "deviations": deviations,
        "verdict": verdict,
    }


def render_case(record: dict[str, Any]) -> list[str]:
    team = f"{record['outgoing']}+{record['relief']}"
    world = f"{record['fate']}/{record['channel']}"
    lines = [
        f"{record['ticket']} {team:<22} {world:<16} {record['status']:<16} {record['reason']}"
    ]
    observed = record["observed"]
    if observed is not None and observed["grade"] is not None:
        keys = (
            "sender_handoff",
            "served",
            "receipt",
            "view",
            "action",
            "relief_action",
            "grade",
        )
        lines.append(
            "  observed:    " + " ".join(f"{key}={observed[key]}" for key in keys)
        )
    for knowledge, model in (record["conditional"] or {}).items():
        if not model["applies"]:
            lines.append(
                f"  conditional: {knowledge} not applicable ({model['reason']})"
            )
            continue
        detail = f"grades={model['grades']} in_range={model['grade_in_range']}"
        lines.append(
            f"  conditional: {knowledge} {detail} guaranteed={model['guaranteed']}"
        )
    return lines


def render(report: dict[str, Any]) -> str:
    lines = [f"{kind}: {text}" for kind, text in BASIS.items()]
    for record in report["records"]:
        lines += render_case(record)
    totals = report["totals"]
    for section in ("cases", "observed"):
        lines.append(
            f"{section}: " + " ".join(f"{k}={v}" for k, v in totals[section].items())
        )
    for knowledge, counts in totals["conditional"].items():
        lines.append(
            f"conditional {knowledge}: "
            + " ".join(f"{k}={v}" for k, v in counts.items())
        )
    lines.append(f"verdict: {totals['verdict']} deviations={totals['deviations']}")
    lines += [f"limit: {limit}" for limit in report["limits"]]
    lines += [f"frontier limit: {limit}" for limit in report["frontier_limits"]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str]) -> int:
    flags = [arg for arg in argv if arg.startswith("--")]
    args = [arg for arg in argv if not arg.startswith("--")]
    if len(args) != 2 or flags not in ([], ["--json"]):
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        question_sha, task_files, cases = replay.load_source(Path(args[0]))
        tickets = [case["ticket"] for case in cases]
        rows = replay.load_rows(Path(args[1]), question_sha, task_files, tickets)
        receiver_sha = load_receiver_question()
        world, frontier_limits = load_frontier()
    except ValueError as error:
        sys.stderr.write(f"receiver_archive: {error}\n")
        return 2
    records = [classify(case, rows.get(case["ticket"]), world) for case in cases]
    totals = summarize(records)
    assert totals["cases"]["registered"] == replay.REGISTERED == len(records)
    assert totals["cases"]["missing"] == replay.REGISTERED - len(rows)
    bound = {"schema": SCHEMA, "receiver_question_sha256": receiver_sha}
    bound |= {"question_sha256": question_sha, "task_files": task_files, "world": WORLD}
    report: dict[str, Any] = {
        **bound,
        "basis": BASIS,
        "records": records,
        "totals": totals,
    }
    report |= {"limits": [*LIMITS, *replay.LIMITS], "frontier_limits": frontier_limits}
    sys.stdout.write(json.dumps(report, indent=2) + "\n" if flags else render(report))
    return 0 if totals["verdict"] == "within_range" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
