from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from examples.seatlab.capsule import view_hash
from examples.seatlab.market_handoff import native_stage, simulator, timing_seat
from examples.seatlab.visibility import agy_seat
from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.wait_peer import receipt as owner_receipt

from . import domain, stage

SCHEMA = "seatlab.report-budget-row/v1"
ISSUE_SCHEMA = "seatlab.report-budget-issue/v1"
ISSUE_KEYS = frozenset({"schema", "world", "target", "control"})
ROW_KEYS = (
    "schema",
    "world",
    "target",
    "control",
    "status",
    "checkpoint_sha256",
    "native_id",
    "protocol_reward",
    "reason",
    "sales_status",
    "report",
    "finance_seen",
    "delivered",
    "finance_pick",
    "settlement",
    "sales",
    "finance",
)
OWNER_FACTS = (
    "session_sha256",
    "prompt_sha256",
    "answer_sha256",
    "requested_model",
    "served_model",
    "client_tokens",
    "tool_steps",
    "credit_fallback",
)
TURN_KEYS = (
    "source",
    "request",
    "verified",
    "failure",
    "admission",
    *OWNER_FACTS,
    "reply",
    "posted",
    "status",
    "actor_failure",
)
STATUSES = ("observed", "unobserved", "not_issued", "predecision")
SALES_STATUSES = ("accepted", "invalid", "unanswered", "unobserved")
SEATS = domain.SEATS
PURPOSES = {"sales": "sales", "finance": "finance-price"}
STEPS = ("sales_view", "sales_resolved", "finance_view", "finance_resolved")
LOG_KEYS = frozenset({"schema", "world", "target", "checkpoint_sha256", "events"})
METADATA_KEYS = frozenset(
    {"role", "target", "control", "phase", "view_sha256", "turn", "resolution"}
)
ACTOR_TURN_KEYS = frozenset({"purpose", "reply", "meta", "failure", "sent"})
UNPOSTED_ANSWER = frozenset({"provenance_rejected", "turn_unverified"})
CONTROL_META = {"control": True}
PASSED = ("passed", "complete", "none")
MAX_FILE_BYTES = 1 << 22
PARSE_ERRORS = (ValueError, TypeError, LookupError, RecursionError)
OWNER_ERRORS = (ValueError, KeyError, TypeError, OSError, RecursionError)
Row = dict[str, Any]

assert set(PURPOSES) == set(SEATS) and set(PURPOSES.values()) <= set(timing_seat.ROLES)
assert len(set(TURN_KEYS)) == len(TURN_KEYS) and len(set(ROW_KEYS)) == len(ROW_KEYS)


class Refused(ValueError):
    def __init__(self, category: str) -> None:
        super().__init__(f"report-budget evidence refused: {category}")
        self.category = category


def need(test: object, category: str) -> None:
    if not test:
        raise Refused(category)


def same(a: object, b: object) -> bool:
    return simulator.canonical(a) == simulator.canonical(b)


def present(path: Path) -> bool:
    return os.path.lexists(path)


def raw(path: Path) -> bytes:
    try:
        info = path.lstat()
        need(
            stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_FILE_BYTES,
            f"unsafe:{path.name}",
        )
        data = path.read_bytes()
    except OSError:
        raise Refused(f"missing:{path.name}") from None
    need(len(data) == info.st_size, f"changed:{path.name}")
    return data


def load(path: Path) -> Row:
    data = raw(path)
    try:
        value = packet._json(data)
    except ValueError:
        raise Refused(f"json:{path.name}") from None
    need(isinstance(value, dict), f"object:{path.name}")
    return value


def rebuilt[T](category: str, build: Callable[..., T], *args: Any) -> T:
    try:
        return build(*args)
    except PARSE_ERRORS:
        raise Refused(category) from None


def sales_outcome(reply: str | None) -> tuple[str, Row | None]:
    if reply is None:
        return "unanswered", None
    try:
        return "accepted", domain.read_sales(reply)
    except PARSE_ERRORS:
        return "invalid", None


def finance_outcome(reply: str | None) -> tuple[str, int | None]:
    if reply is None:
        return "unanswered", None
    try:
        return "accepted", domain.read_price(reply)
    except PARSE_ERRORS:
        return "invalid", None


def blank_row(world: int, target: int, control: bool) -> Row:
    return {
        "schema": SCHEMA,
        "world": world,
        "target": target,
        "control": control,
        "status": "unobserved",
        "checkpoint_sha256": None,
        "native_id": None,
        "protocol_reward": None,
        "reason": None,
        "sales_status": "unobserved",
        "report": None,
        "finance_seen": None,
        "delivered": None,
        "finance_pick": None,
        "settlement": None,
        "sales": None,
        "finance": None,
    }


def blank_turn(source: str, failure: str | None = None) -> Row:
    turn: Row = dict.fromkeys(TURN_KEYS)
    turn.update(source=source, request=False, verified=False, posted=False)
    turn["failure"] = failure
    return turn


def company(stock: Any, cell: Path, world: int) -> tuple[Any, Row]:
    ended_path, company_path = cell / "predecision.json", cell / "company.json"
    try:
        checkpoint, binding = domain.make_company(stock, world)
    except domain.CompanyEndedBeforeDecision as ended:
        need(not present(company_path), "company_record_after_end")
        need(raw(ended_path) == simulator.encode(ended.record), "predecision_record")
        return None, ended.record
    need(not present(ended_path), "predecision_record_for_live_company")
    need(raw(company_path) == simulator.encode(binding), "company_record")
    return checkpoint, binding


def staged(cell: Path, binding: Row, target: int, control: bool) -> tuple[Path, Row]:
    path = cell / "native"
    need(present(path), "not_staged")
    need(path.is_dir() and not path.is_symlink(), "unsafe_native")
    native = path.resolve(strict=True)
    config = load(native / "config.json")
    rows = config.get("participants")
    first = rows[0] if isinstance(rows, list) and rows else None
    kwargs = first.get("kwargs") if isinstance(first, dict) else None
    client = kwargs.get("client") if isinstance(kwargs, dict) else None
    need(
        (client is None) == control
        and (
            client is None
            or (
                isinstance(client, str)
                and Path(client).is_absolute()
                and os.path.normpath(client) == client
            )
        ),
        "config_client",
    )
    name = stage.trial_name(binding["world"], target)
    expected = stage.trial_config(native, name, target, client, control)
    need(same(config, expected), "config_binding")
    scenario = native_stage.canonical(stage.scenario(binding, target))
    task = native / "task"
    need(
        raw(task / stage.SCENARIO) == scenario
        and raw(task / stage.VERIFIER_SCENARIO) == scenario,
        "scenario_binding",
    )
    return native, config


def native_id(result: Row) -> str:
    value = result.get("id")
    if not isinstance(value, str):
        raise Refused("native_id")
    parsed = rebuilt("native_id", UUID, value)
    need(str(parsed) == value and parsed.int != 0, "native_id")
    return value


def native_reward(trial: Path, result: Row) -> int | None:
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    path = trial / stage.REWARD
    if not present(path):
        need(not rewards, "native_reward")
        return None
    record = load(path)
    value = record.get("reward")
    if type(value) not in (int, float) or value not in (0, 1):
        raise Refused("protocol_reward")
    need(record.keys() == {"reward"}, "protocol_reward")
    need(same(rewards, record), "native_reward")
    return int(value)


def seat_metadata(result: Row) -> dict[str, Row | None]:
    rows = result.get("seat_results")
    if not isinstance(rows, list) or len(rows) != len(SEATS):
        raise Refused("seat_results")
    metas: dict[str, Row | None] = {}
    for item in rows:
        need(isinstance(item, dict), "seat_results")
        seat = item.get("seat")
        need(
            seat in SEATS and seat not in metas and item.get("under_test") is True,
            "seat_results",
        )
        context = item.get("agent_result")
        meta = context.get("metadata") if isinstance(context, dict) else None
        metas[seat] = meta if isinstance(meta, dict) else None
    return metas


def terminal(cell: Path, trial: Path, config: Row, row: Row) -> dict[str, Row | None]:
    need(present(cell / "native-exit.json"), "native_unfinished")
    exit_record = load(cell / "native-exit.json")
    need(
        exit_record.keys() == {"returncode"} and type(exit_record["returncode"]) is int,
        "native_exit",
    )
    need(present(trial), "native_no_trial")
    need(
        trial.is_dir()
        and not trial.is_symlink()
        and trial.resolve(strict=True) == trial,
        "unsafe_trial",
    )
    result = load(trial / "result.json")
    name, settings = config["trial_name"], result.get("config")
    need(
        result.get("trial_name") == name
        and result.get("trial_uri") == trial.as_uri()
        and isinstance(settings, dict)
        and settings.get("trial_name") == name,
        "native_trial",
    )
    assert isinstance(settings, dict)
    ran = settings.get("participants")
    need(isinstance(ran, list) and len(ran) == len(SEATS), "native_participants")
    ran_rows = {item.get("seat"): item for item in ran if isinstance(item, dict)}
    declared = {item["seat"]: item for item in config["participants"]}
    need(
        ran_rows.keys() == declared.keys()
        and all(
            same(ran_rows[seat].get(key), value)
            for seat, item in declared.items()
            for key, value in item.items()
        ),
        "native_participants",
    )
    need(
        isinstance(result.get("finished_at"), str) and result["finished_at"],
        "native_unfinished",
    )
    row["native_id"] = native_id(result)
    row["protocol_reward"] = native_reward(trial, result)
    return seat_metadata(result)


def world_log(trial: Path, binding: Row, target: int) -> dict[str, Row]:
    log = load(trial / stage.LOG)
    need(
        LOG_KEYS <= log.keys() and log["schema"] == stage.LOG_SCHEMA,
        "world_log_shape",
    )
    need(
        type(log["world"]) is int
        and log["world"] == binding["world"]
        and type(log["target"]) is int
        and log["target"] == target
        and log["checkpoint_sha256"] == binding["checkpoint_sha256"],
        "world_log_binding",
    )
    events = log["events"]
    need(isinstance(events, list) and len(events) <= len(STEPS), "world_events")
    out: dict[str, Row] = {}
    for index, event in enumerate(events):
        need(
            isinstance(event, dict)
            and type(event.get("n")) is int
            and event["n"] == index + 1
            and event.get("step") == STEPS[index],
            "world_event_order",
        )
        out[STEPS[index]] = event
    return out


def wire_sales(events: dict[str, Row], binding: Row) -> tuple[str | None, Row | None]:
    view = events.get("sales_view")
    digest = view_hash({"view": binding["sales"]})
    need(view is None or view.get("view_sha256") == digest, "world_sales_view")
    event = events.get("sales_resolved")
    if event is None:
        return None, None
    reply = event.get("reply")
    need(reply is None or isinstance(reply, str), "world_sales_reply")
    status, report = sales_outcome(reply)
    need(
        event.get("status") == status and same(event.get("report"), report),
        "world_sales_status",
    )
    return status, report


def wire_view(events: dict[str, Row], binding: Row, report: Row | None) -> None:
    view = events.get("finance_view")
    if view is None:
        return
    need("report" in view, "world_finance_view")
    seen = view["report"]
    digest = view_hash({"view": binding["finance"], "report": seen})
    need(view.get("view_sha256") == digest, "world_finance_view")
    need(same(seen, report), "world_forwarding")


def wire_finance(events: dict[str, Row]) -> tuple[str | None, int | None]:
    event = events.get("finance_resolved")
    if event is None:
        return None, None
    reply = event.get("reply")
    need(reply is None or isinstance(reply, str), "world_finance_reply")
    status, pick = finance_outcome(reply)
    need(
        event.get("status") == status and same(event.get("pick"), pick),
        "world_finance_status",
    )
    return status, pick


def admission_failure(note: Row | None) -> str:
    if note is None:
        return "admission_missing"
    if note["outcome"] in ("refused", "unavailable"):
        return f"admission_{note['outcome']}"
    return "no_request"


def recorded_prompt(record: Path, seat: str) -> str:
    try:
        request = packet._json(owner_receipt.read(record / "request.json"))
    except (ValueError, OSError):
        raise Refused(f"{seat}_request_unreadable") from None
    need(
        isinstance(request, dict)
        and request.keys() == {"schema", "prompt"}
        and request["schema"] == timing_seat.TURN_SCHEMA
        and isinstance(request["prompt"], str),
        f"{seat}_request_shape",
    )
    return request["prompt"]


def owner_dir(path: Path, names: set[str], category: str) -> None:
    try:
        info = path.lstat()
        entries = set(os.listdir(path)) if stat.S_ISDIR(info.st_mode) else None
    except OSError:
        raise Refused(category) from None
    need(
        entries is not None
        and entries <= names
        and stat.S_IMODE(info.st_mode) == 0o700,
        category,
    )


def owner_turn(native: Path, seat: str, prompts: list[str]) -> Row | None:
    purpose = PURPOSES[seat]
    owner_dir(native / "private", set(SEATS), "owner_private")
    root = stage.owner_root(native, seat)
    if not present(root):
        return None
    owner_dir(root, {purpose}, f"{seat}_owner_root")
    turn = root / purpose
    if not present(turn):
        return None
    owner_dir(turn, {timing_seat.NOTE, purpose}, f"{seat}_owner_turn")
    entry = blank_turn("model")
    note = timing_seat.admission(turn)
    if note is not None:
        entry["admission"] = {key: note[key] for key in ("outcome", "stage", "cause")}
    record = turn / purpose
    if not present(record / "request.json"):
        entry["failure"] = admission_failure(note)
        return entry
    entry["request"] = True
    prompt = recorded_prompt(record, seat)
    entry["prompt_sha256"] = agy_seat.sha(prompt)
    need(prompt in prompts, f"{seat}_prompt_mismatch")
    need(
        note is not None and (note["outcome"], note["stage"], note["cause"]) == PASSED,
        f"{seat}_request_without_admission",
    )
    try:
        owned = owner_receipt.owner_turn(turn, purpose, prompt, None)
    except OWNER_ERRORS:
        entry["failure"] = "reply_unverified"
        return entry
    if owned["status"] != "answered_unused":
        entry["failure"] = "unanswered"
        return entry
    receipt = owned["receipt"]
    entry.update(
        verified=True,
        reply=owned["text"],
        session_sha256=receipt["session_sha256"],
        answer_sha256=receipt["answer_sha256"],
        requested_model=receipt["requested_model"],
        served_model=receipt["server_model"],
        client_tokens=receipt["usage"]["total_tokens"],
        tool_steps=receipt["observed_tool_steps"],
        credit_fallback=receipt["ai_credit_fallback"],
    )
    need(entry["prompt_sha256"] == receipt["prompt_sha256"], f"{seat}_receipt_prompt")
    return entry


def actor_turn(
    meta: Row | None, seat: str, row: Row, events: dict[str, Row]
) -> Row | None:
    if meta is None:
        return None
    view = events.get(f"{seat}_view")
    resolved = events.get(f"{seat}_resolved")
    need(
        meta.keys() == METADATA_KEYS
        and meta["role"] in (seat, None)
        and type(meta["target"]) is int
        and meta["target"] == row["target"]
        and meta["control"] is row["control"],
        f"{seat}_actor_metadata",
    )
    need(
        meta["view_sha256"] is None
        or (view is not None and meta["view_sha256"] == view.get("view_sha256")),
        f"{seat}_actor_view",
    )
    need(
        meta["resolution"] is None
        or (resolved is not None and meta["resolution"] == resolved.get("status")),
        f"{seat}_actor_resolution",
    )
    turn = meta["turn"]
    if turn is None:
        return None
    need(
        isinstance(turn, dict)
        and turn.keys() == ACTOR_TURN_KEYS
        and turn["purpose"] == PURPOSES[seat]
        and turn["sent"] in (None, True, False),
        f"{seat}_actor_turn",
    )
    return turn


def posted_turn(turn: Row, resolved: Row | None, actor: Row | None) -> Row:
    if actor is not None:
        turn["actor_failure"] = actor["failure"]
    if resolved is not None:
        turn.update(posted=True, status=resolved["status"])
    return turn


def live_turn(
    native: Path,
    seat: str,
    prompts: list[str],
    events: dict[str, Row],
    meta: Row | None,
    row: Row,
) -> Row | None:
    resolved = events.get(f"{seat}_resolved")
    actor = actor_turn(meta, seat, row, events)
    turn = owner_turn(native, seat, prompts)
    if turn is None and resolved is None and actor is None:
        return None
    turn = turn or blank_turn("model", "no_owner_turn")
    reply = None if resolved is None else resolved.get("reply")
    if isinstance(reply, str):
        need(turn["verified"] and turn["reply"] == reply, f"{seat}_reply_unverified")
    elif resolved is not None and turn["verified"]:
        need(
            actor is not None and actor["failure"] in UNPOSTED_ANSWER,
            f"{seat}_reply_not_posted",
        )
    if actor is not None:
        need(
            actor["sent"] is None or actor["sent"] == turn["request"],
            f"{seat}_actor_sent",
        )
        need(resolved is None or actor["reply"] == reply, f"{seat}_actor_reply")
        need(
            actor["meta"] is None
            or (
                turn["verified"]
                and same(actor["meta"], {key: turn[key] for key in OWNER_FACTS})
            ),
            f"{seat}_actor_receipt",
        )
    return posted_turn(turn, resolved, actor)


def control_turn(
    native: Path,
    seat: str,
    expected: str,
    events: dict[str, Row],
    meta: Row | None,
    row: Row,
) -> Row | None:
    owner_dir(native / "private", set(), f"{seat}_control_owner")
    need(not present(stage.owner_root(native, seat)), f"{seat}_control_owner")
    resolved = events.get(f"{seat}_resolved")
    actor = actor_turn(meta, seat, row, events)
    if resolved is None and actor is None:
        return None
    turn = blank_turn("control")
    if actor is not None:
        need(
            actor["sent"] in (None, False)
            and (actor["meta"] is None or same(actor["meta"], CONTROL_META)),
            f"{seat}_control_actor",
        )
    if resolved is not None:
        reply = resolved.get("reply")
        need(
            reply == expected and (actor is None or actor["reply"] == reply),
            f"{seat}_control_reply",
        )
        turn.update(verified=True, reply=expected)
        turn["answer_sha256"] = agy_seat.sha(expected)
    return posted_turn(turn, resolved, actor)


def finance_prompts(
    binding: Row, events: dict[str, Row], sales: Row | None
) -> list[str]:
    view = events.get("finance_view")
    reports: list[Row | None] = [None if view is None else view.get("report")]
    if view is None and sales is not None and sales["verified"]:
        parsed = sales_outcome(sales["reply"])[1]
        if parsed is not None:
            reports.append(parsed)
    return [
        rebuilt("finance_prompt", domain.finance_prompt, binding["finance"], report)
        for report in reports
    ]


def sales_prompts(binding: Row, target: int) -> list[str]:
    return [rebuilt("sales_prompt", domain.sales_prompt, binding["sales"], target)]


def partial_turns(native: Path, binding: Row, target: int, row: Row) -> None:
    prompts = {"sales": sales_prompts(binding, target)}
    for seat in SEATS:
        if row[seat] is not None:
            continue
        if seat == "finance":
            prompts[seat] = finance_prompts(binding, {}, row["sales"])
        try:
            row[seat] = owner_turn(native, seat, prompts[seat])
        except Refused:
            row[seat] = None


def turns(
    native: Path,
    binding: Row,
    events: dict[str, Row],
    metas: dict[str, Row | None],
    row: Row,
) -> None:
    target = row["target"]
    if row["control"]:
        view = events.get("finance_view")
        expected = {
            "sales": domain.control_reply("sales", target),
            "finance": rebuilt(
                "control_reply",
                domain.control_reply,
                "finance",
                target,
                None if view is None else view.get("report"),
            ),
        }
        for seat in SEATS:
            row[seat] = control_turn(
                native, seat, expected[seat], events, metas[seat], row
            )
        return
    prompts = sales_prompts(binding, target)
    row["sales"] = live_turn(native, "sales", prompts, events, metas["sales"], row)
    prompts = finance_prompts(binding, events, row["sales"])
    finance_meta = metas["finance"]
    row["finance"] = live_turn(native, "finance", prompts, events, finance_meta, row)
    finance = row["finance"]
    need(
        "finance_view" in events or finance is None or not finance["request"],
        "finance_request_without_view",
    )
    sessions = [row[seat]["session_sha256"] for seat in SEATS if row[seat] is not None]
    sessions = [session for session in sessions if session is not None]
    need(len(set(sessions)) == len(sessions), "shared_provider_session")


def sales_known(row: Row) -> bool:
    sales = row["sales"]
    if sales is None or not sales["posted"]:
        return False
    return sales["verified"] if row["control"] else sales["request"]


def problem(row: Row, events: dict[str, Row]) -> str | None:
    sales, finance = row["sales"], row["finance"]
    if "sales_resolved" not in events:
        return "world_sales_missing"
    if not sales_known(row):
        return f"sales_{(sales or {}).get('failure') or 'unproven'}"
    if "finance_view" not in events:
        return "finance_view_missing"
    if not row["control"] and not (finance and finance["request"]):
        return f"finance_{(finance or {}).get('failure') or 'no_turn'}"
    if "finance_resolved" not in events:
        return "world_finance_missing"
    if row["control"] and not (finance and finance["verified"]):
        return "finance_control_unverified"
    return None


def delivery(row: Row, events: dict[str, Row], report: Row | None) -> bool | None:
    finance, status = row["finance"], row["sales_status"]
    if status in ("invalid", "unanswered"):
        return False
    if status != "accepted" or report is None or "finance_view" not in events:
        return None
    if row["control"]:
        return True if finance is not None and finance["posted"] else None
    return True if finance is not None and finance["request"] else None


def classify(
    row: Row, events: dict[str, Row], sales: tuple[str | None, Row | None]
) -> None:
    status, report = sales
    if sales_known(row):
        row["sales_status"] = status
        row["report"] = report
    row["delivered"] = delivery(row, events, report)
    finance = row["finance"]
    row["finance_seen"] = (
        "finance_view" in events
        and finance is not None
        and bool(finance["posted"] if row["control"] else finance["request"])
    )
    finance_status, pick = wire_finance(events)
    if finance is not None and finance["verified"] and finance_status == "accepted":
        row["finance_pick"] = pick
    grade = 1 if len(events) == len(STEPS) and finance_status == "accepted" else 0
    need(
        row["protocol_reward"] is None or row["protocol_reward"] == grade,
        "protocol_reward_disagrees",
    )
    reason = problem(row, events)
    if reason is None:
        row["status"] = "observed"
        if row["finance_pick"] is None:
            reason = f"finance_{finance_status}"
    row["reason"] = reason


def reconcile(
    stock: Any,
    checkpoint: Any,
    binding: Row,
    native: Path,
    events: dict[str, Row],
    metas: dict[str, Row | None],
    row: Row,
) -> None:
    sales = wire_sales(events, binding)
    turns(native, binding, events, metas, row)
    wire_view(events, binding, sales[1])
    classify(row, events, sales)
    if row["finance_pick"] is not None:
        settlement = domain.settle(stock, checkpoint, row["finance_pick"])
        need(
            settlement["checkpoint_sha256"] == binding["checkpoint_sha256"]
            and settlement["pick"] == row["finance_pick"],
            "settlement_binding",
        )
        row["settlement"] = settlement


def attempt(stock: Any, cell: Path, row: Row) -> None:
    world, target, control = row["world"], row["target"], row["control"]
    need(cell.is_dir() and not cell.is_symlink(), "unsafe_cell")
    issued = load(cell / "issued.json")
    expected = {
        "schema": ISSUE_SCHEMA,
        "world": world,
        "target": target,
        "control": control,
    }
    need(issued.keys() == ISSUE_KEYS and same(issued, expected), "issue_identity")
    checkpoint, binding = company(stock, cell, world)
    row["checkpoint_sha256"] = binding["checkpoint_sha256"]
    if checkpoint is None:
        row.update(status="predecision", reason="company_ended_before_decision")
        return
    native, config = staged(cell, binding, target, control)
    trial = native / "trials" / config["trial_name"]
    try:
        metas = terminal(cell, trial, config, row)
        events = world_log(trial, binding, target)
        reconcile(stock, checkpoint, binding, native, events, metas, row)
    except Refused:
        if not control:
            partial_turns(native, binding, target, row)
        raise


def collect(
    stock: Any, cell: Path, world: int, target: int, control: bool = False
) -> Row:
    if type(world) is not int or not 0 <= world <= domain.CONTROL_WORLD:
        raise ValueError(f"world must be an int in 0..{domain.CONTROL_WORLD}")
    if type(target) is not int or target not in domain.TARGETS:
        raise ValueError(f"target must be one of {domain.TARGETS}")
    if type(control) is not bool or control != (world == domain.CONTROL_WORLD):
        raise ValueError(f"control is exactly company {domain.CONTROL_WORLD}")
    if not isinstance(cell, Path) or cell.name != f"w{world:02d}-t{target:03d}":
        raise ValueError("cell does not name this world and target")
    row = blank_row(world, target, control)
    if not present(cell):
        row.update(status="not_issued", reason="not_issued")
        return row
    try:
        attempt(stock, cell, row)
    except Refused as refused:
        row.update(
            status="unobserved",
            reason=refused.category,
            sales_status="unobserved",
            report=None,
            finance_seen=None,
            delivered=None,
            finance_pick=None,
            settlement=None,
        )
    if tuple(row) != ROW_KEYS or row["status"] not in STATUSES:
        raise AssertionError("row schema drifted")
    if row["sales_status"] not in SALES_STATUSES:
        raise AssertionError("sales status drifted")
    return row
