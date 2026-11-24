from __future__ import annotations

import hashlib
import json
import re
import stat
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from examples.seatlab.capsule import view_hash
from examples.seatlab.visibility import agy_seat
from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.wait_peer import receipt as owner_receipt

from . import timing_messages as m
from . import timing_world

SCHEMA = "seatlab.price-timing-evidence/v1"
NATIVE_SCHEMA = "seatlab.price-timing-native/v1"
JOURNAL_SCHEMA = "seatlab.price-timing-journal/v1"
SCENARIO_SCHEMA = "seatlab.price-timing-scenario/v1"
RECEIPT_DOMAIN = "seatlab.price-timing-receipt/v1"
TASK_NAME = "harbor/seatlab-price-timing-native"
SEAT_IMPORT = "examples.seatlab.market_handoff.timing_actor:TimingSeat"
NAMESPACE = timing_world.NAMESPACE
SALES = "sales"
FINANCE = ("finance_a", "finance_b")
SEATS = (SALES, *FINANCE)
PURPOSES = {
    "sales": ("sales",),
    "finance_a": ("finance-price", "finance-receipt"),
    "finance_b": ("finance-price", "finance-receipt"),
}
SEAT_EVENTS = {
    "sales": ["view", "commit"],
    "finance_a": ["view", "commit", "receipt", "ack"],
    "finance_b": ["view", "commit", "receipt", "ack"],
}
EVENT_COUNT = sum(len(events) for events in SEAT_EVENTS.values())
TURN_COUNT = sum(len(purposes) for purposes in PURPOSES.values())
TRIAL = re.compile(r"price-w(\d{2})-r([01])")
ACK_TOKEN = re.compile(r"[0-9a-f]{12}")
HEX64 = re.compile(r"[0-9a-f]{64}")
NATIVE_KEYS = frozenset(
    {
        "schema",
        "early",
        "checkpoint_sha256",
        "report",
        "events",
        "picks",
        "acknowledged",
    }
)
EVENT_KEYS = frozenset({"n", "seat", "event", "view_sha256"})
METADATA_KEYS = frozenset({"role", "phase", "turns"})
TURN_KEYS = frozenset({"purpose", "meta", "view_sha256", "reply"})
KWARG_KEYS = frozenset({"client", "owner", "control"})
META_KEYS = frozenset(
    {
        "session_sha256",
        "prompt_sha256",
        "answer_sha256",
        "requested_model",
        "served_model",
        "client_tokens",
        "tool_steps",
        "credit_fallback",
    }
)
CONTROL_META = {"control": True}
CONTROL_SALES = '{"pick":1,"reason":"control"}'
CONTROL_PRICE = {"finance_a": '{"pick":2}', "finance_b": '{"pick":3}'}
ADMISSION = "admission.json"
MAX_FILE_BYTES = 1 << 22
CONTROL_NOTE = (
    "Native plumbing control on company 8: fixed host replies, no model sessions; "
    "excluded from every behavioral count."
)
LIVE_NOTES = (
    (
        "Each turn is one fresh signed-in agy subscription session with tools denied "
        "and AI-credit fallback disabled; the owner request, reply and receipt files "
        "were re-verified against the rebuilt prompt."
    ),
    (
        "served_model is null because the client reports no server model; "
        "requested_model is the client's echo of the request."
    ),
)
Row = dict[str, Any]


class Refused(ValueError):
    def __init__(self, category: str) -> None:
        super().__init__(f"price-timing evidence refused: {category}")
        self.category = category


def need(test: object, category: str) -> None:
    if not test:
        raise Refused(category)


def mapping(value: object, category: str) -> Row:
    need(isinstance(value, dict), category)
    return cast("Row", value)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def same(a: object, b: object) -> bool:
    return canonical(a) == canonical(b)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hex64(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def rebuilt[T](category: str, build: Callable[..., T], *args: Any) -> T:
    try:
        return build(*args)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise Refused(f"{category}:{error}") from None


def raw(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError:
        raise Refused(f"missing:{path.name}") from None
    need(stat.S_ISREG(info.st_mode), f"unsafe:{path.name}")
    need(0 < info.st_size <= MAX_FILE_BYTES, f"size:{path.name}")
    data = path.read_bytes()
    need(len(data) == info.st_size, f"changed:{path.name}")
    return data


def load(path: Path) -> tuple[str, Row]:
    data = raw(path)
    value = rebuilt(f"json:{path.name}", packet._json, data)
    need(isinstance(value, dict), f"object:{path.name}")
    return sha(data), value


def private_dir(path: Path, category: str) -> set[str]:
    try:
        info = path.lstat()
    except OSError:
        raise Refused(category) from None
    need(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077, category)
    return {child.name for child in path.iterdir()}


def assignment(world: int, repeat: int) -> str:
    key = f"{NAMESPACE}:{world}:assignment".encode("ascii")
    return FINANCE[(hashlib.sha256(key).digest()[0] & 1) ^ repeat]


def receipt_token(
    checkpoint: str, seat: str, pick: int, commit: str, report: Row
) -> str:
    body = {
        "domain": RECEIPT_DOMAIN,
        "checkpoint_sha256": checkpoint,
        "seat": seat,
        "pick": pick,
        "view_sha256": commit,
        "report": report,
    }
    return sha(canonical(body))[:12]


def check_binding(binding: object) -> int:
    fields = mapping(binding, "binding_keys")
    need(fields.keys() == timing_world.BINDING_KEYS, "binding_keys")
    world = fields["world"]
    need(
        type(world) is int and 0 <= world <= timing_world.CONTROL_WORLD, "binding_world"
    )
    need(
        fields["stock_commit"] == timing_world.PIN
        and fields["world_seed"] == timing_world.world_seed(world)
        and fields["truth_seed"] == timing_world.truth_seed(world)
        and fields["shape"] == timing_world.SHAPES[world]
        and hex64(fields["checkpoint_sha256"]),
        "binding_provenance",
    )
    return world


def owner_path(root: Path, value: object) -> Path:
    need(isinstance(value, str) and Path(value).is_absolute(), "participant_owner")
    assert isinstance(value, str)
    path = Path(value)
    need(
        ".." not in path.parts and path.parent.resolve() == path.parent,
        "owner_location",
    )
    need(
        root in path.parents
        and not path.is_relative_to(root / "task")
        and not path.is_relative_to(root / "trials"),
        "owner_location",
    )
    return path


def participants(root: Path, value: object, control: bool) -> dict[str, Path]:
    need(isinstance(value, list) and len(value) == len(SEATS), "participants")
    assert isinstance(value, list)
    owners: dict[str, Path] = {}
    for item in value:
        row = mapping(item, "participant_shape")
        seat = row.get("seat")
        if not isinstance(seat, str) or seat not in SEATS or seat in owners:
            raise Refused("participant_seat")
        kwargs = mapping(row.get("kwargs"), "participant_shape")
        need(
            row.get("import_path") == SEAT_IMPORT and kwargs.keys() == KWARG_KEYS,
            "participant_shape",
        )
        need(kwargs["control"] is control, "participant_control")
        client = kwargs["client"]
        need(
            isinstance(client, str) and Path(client).is_absolute(), "participant_client"
        )
        owners[seat] = owner_path(root, kwargs["owner"])
    paths = list(owners.values())
    need(
        all(a == b or a not in b.parents for a in paths for b in paths)
        and len(set(paths)) == len(SEATS),
        "owner_distinct",
    )
    return owners


def declared(root: Path, world: int, early: str, control: bool) -> tuple[Row, int, Row]:
    config_sha, config = load(root / "config.json")
    name = config.get("trial_name")
    match = TRIAL.fullmatch(name) if isinstance(name, str) else None
    need(match is not None, "trial_name")
    assert match is not None
    need(int(match[1]) == world, "trial_world")
    repeat = int(match[2])
    need((world == timing_world.CONTROL_WORLD) is control, "control_world")
    need(early == assignment(world, repeat), "early_assignment")
    task, trials = config.get("task"), config.get("trials_dir")
    need(
        isinstance(task, dict)
        and isinstance(task.get("path"), str)
        and Path(task["path"]).resolve(strict=True) == root / "task",
        "task_path",
    )
    need(
        isinstance(trials, str)
        and Path(trials).resolve(strict=True) == root / "trials",
        "trials_dir",
    )
    owners = participants(root, config.get("participants"), control)
    return config, repeat, {"owners": owners, "config_sha256": config_sha}


def staged_task(root: Path, binding: Row, early: str) -> str:
    section = rebuilt("task_toml", tomllib.loads, raw(root / "task/task.toml").decode())
    task = section.get("task")
    need(isinstance(task, dict) and task.get("name") == TASK_NAME, "task_name")
    world_sha, world = load(root / "task/environment/scenario.json")
    tests_sha, tests = load(root / "task/tests/scenario.json")
    need(world_sha == tests_sha, "scenario_copies")
    expected = {
        "schema": SCENARIO_SCHEMA,
        "early": early,
        "checkpoint_sha256": binding["checkpoint_sha256"],
        "sales": binding["sales"],
        "finance": binding["finance"],
    }
    need(same(tests, expected) and same(world, expected), "scenario_binding")
    return tests_sha


def native_id(result: Row) -> str:
    value = result.get("id")
    need(isinstance(value, str), "native_id")
    assert isinstance(value, str)
    parsed = rebuilt("native_id", UUID, value)
    need(str(parsed) == value and parsed.int != 0, "native_id")
    return value


def native_trial(trial: Path, config: Row) -> tuple[Row, Row]:
    need(trial.is_dir() and trial.resolve(strict=True) == trial, "native_trial_dir")
    result_sha, result = load(trial / "result.json")
    name = config["trial_name"]
    settings = result.get("config")
    need(
        result.get("trial_name") == name
        and result.get("task_name") == TASK_NAME
        and result.get("trial_uri") == trial.as_uri()
        and isinstance(settings, dict)
        and settings.get("trial_name") == name,
        "native_trial",
    )
    assert isinstance(settings, dict)
    ran = settings.get("participants")
    need(isinstance(ran, list) and len(ran) == len(SEATS), "native_participants")
    declared_rows = {row["seat"]: row for row in config["participants"]}
    ran_rows = {row.get("seat"): row for row in ran if isinstance(row, dict)}
    need(
        ran_rows.keys() == declared_rows.keys()
        and all(
            same(ran_rows[seat].get(key), value)
            for seat, row in declared_rows.items()
            for key, value in row.items()
        ),
        "native_participants",
    )
    need(
        "exception_info" in result and result["exception_info"] is None,
        "native_exception",
    )
    need(
        isinstance(result.get("finished_at"), str) and result["finished_at"],
        "native_unfinished",
    )
    need(
        result.get("step_results") is None and result.get("agent_result") is None,
        "native_shape",
    )
    reward_sha, reward = load(trial / "verifier/reward.json")
    value = reward.get("reward")
    need(
        reward.keys() == {"reward"} and type(value) in (int, float) and value == 1,
        "protocol_reward",
    )
    verifier = result.get("verifier_result")
    need(
        isinstance(verifier, dict)
        and same(verifier.get("rewards"), reward)
        and result.get("reward_valid") is True,
        "native_reward",
    )
    return result, {"result": result_sha, "reward": reward_sha, "reward_value": value}


def seat_turns(result: Row) -> dict[str, list[Row]]:
    rows = result.get("seat_results")
    need(isinstance(rows, list) and len(rows) == len(SEATS), "seat_results")
    assert isinstance(rows, list)
    seats: dict[str, list[Row]] = {}
    for row in rows:
        need(isinstance(row, dict), "seat_results")
        seat = row.get("seat")
        need(seat in SEATS and seat not in seats, "seat_results")
        need(row.get("under_test") is True, f"seat_not_under_test:{seat}")
        need(
            "exception_info" in row and row["exception_info"] is None,
            f"seat_error:{seat}",
        )
        context = row.get("agent_result")
        meta = context.get("metadata") if isinstance(context, dict) else None
        need(
            isinstance(meta, dict)
            and meta.keys() == METADATA_KEYS
            and meta["role"] == seat
            and meta["phase"] == "complete",
            f"seat_incomplete:{seat}",
        )
        assert isinstance(meta, dict)
        turns = meta["turns"]
        need(
            isinstance(turns, list) and len(turns) == len(PURPOSES[seat]),
            f"seat_turns:{seat}",
        )
        for turn, purpose in zip(turns, PURPOSES[seat], strict=True):
            need(
                isinstance(turn, dict)
                and turn.keys() == TURN_KEYS
                and turn["purpose"] == purpose
                and hex64(turn["view_sha256"])
                and isinstance(turn["reply"], str)
                and isinstance(turn["meta"], dict),
                f"turn_shape:{seat}:{purpose}",
            )
        seats[seat] = turns
    return seats


def native_world(trial: Path) -> tuple[Row, Row]:
    timing_sha, timing = load(trial / "artifacts/logs/world/timing.json")
    journal_sha, journal = load(trial / "artifacts/logs/world/timing-journal.json")
    need(
        timing.keys() == NATIVE_KEYS and timing["schema"] == NATIVE_SCHEMA,
        "timing_shape",
    )
    need(
        journal.keys() == NATIVE_KEYS and journal["schema"] == JOURNAL_SCHEMA,
        "journal_shape",
    )
    need(
        all(same(journal[key], timing[key]) for key in NATIVE_KEYS - {"schema"}),
        "journal_differs",
    )
    return timing, {"timing": timing_sha, "journal": journal_sha}


def ack_token(seat: str, reply: str) -> str:
    record = rebuilt(f"ack_reply:{seat}", packet._json, reply)
    need(isinstance(record, dict) and record.keys() == {"ack"}, f"ack_reply:{seat}")
    token = record["ack"]
    need(isinstance(token, str) and ACK_TOKEN.fullmatch(token), f"ack_token:{seat}")
    return token


def sales_plan(binding: Row, turn: Row) -> tuple[Row, Row]:
    view = view_hash({"observation": binding["sales"]})
    need(turn["view_sha256"] == view, "sales_view")
    prompt = rebuilt("sales_prompt", m.sales_prompt, binding["sales"])
    report = rebuilt("sales_reply", m.read_sales, turn["reply"])
    step = {"seat": SALES, "purpose": "sales", "prompt": prompt, "turn": turn}
    return report, {"step": step, "digests": {"view": view, "commit": view}}


def finance_plan(
    binding: Row, early: str, report: Row, seat: str, turns: list[Row]
) -> Row:
    price, ack = turns
    slot = report if seat == early else None
    late = None if seat == early else report
    view = view_hash({"observation": binding["finance"], "report": slot})
    need(price["view_sha256"] == view, f"price_view:{seat}")
    price_prompt = rebuilt(
        f"price_prompt:{seat}", m.price_prompt, binding["finance"], slot
    )
    pick = rebuilt(f"price_reply:{seat}", m.read_price, price["reply"])
    token = ack_token(seat, ack["reply"])
    expected = receipt_token(binding["checkpoint_sha256"], seat, pick, view, report)
    need(token == expected, f"token_binding:{seat}")
    receipt = view_hash({"report": late, "token": token})
    need(ack["view_sha256"] == receipt, f"receipt_view:{seat}")
    ack_prompt = rebuilt(
        f"receipt_prompt:{seat}",
        m.receipt_prompt,
        price_prompt,
        price["reply"],
        late,
        token,
    )
    need(m.read_receipt(ack["reply"], token) is True, f"ack_reply:{seat}")
    return {
        "pick": pick,
        "token": token,
        "steps": [
            {
                "seat": seat,
                "purpose": "finance-price",
                "prompt": price_prompt,
                "turn": price,
            },
            {
                "seat": seat,
                "purpose": "finance-receipt",
                "prompt": ack_prompt,
                "turn": ack,
            },
        ],
        "digests": {"view": view, "commit": view, "receipt": receipt, "ack": receipt},
    }


def check_events(events: object, digests: dict[str, Row]) -> list[Row]:
    need(isinstance(events, list) and len(events) == EVENT_COUNT, "event_count")
    rows = [mapping(event, "event_shape") for event in cast("list[object]", events)]
    for index, event in enumerate(rows):
        need(
            event.keys() == EVENT_KEYS
            and type(event["n"]) is int
            and event["n"] == index + 1
            and event["seat"] in SEATS,
            "event_shape",
        )
    order = {seat: [e["event"] for e in rows if e["seat"] == seat] for seat in SEATS}
    need(order == SEAT_EVENTS, "event_order")
    need(
        [(e["seat"], e["event"]) for e in rows[:2]]
        == [(SALES, "view"), (SALES, "commit")],
        "sales_commit_first",
    )
    for event in rows:
        need(
            event["view_sha256"] == digests[event["seat"]][event["event"]],
            f"event_digest:{event['seat']}:{event['event']}",
        )
    return [dict(event) for event in rows]


def check_timing(
    timing: Row, binding: Row, early: str, report: Row, plans: dict[str, Row]
) -> None:
    need(timing["early"] == early, "timing_early")
    need(
        timing["checkpoint_sha256"] == binding["checkpoint_sha256"], "timing_checkpoint"
    )
    need(same(timing["report"], report), "timing_report")
    picks = timing["picks"]
    need(
        isinstance(picks, dict)
        and picks.keys() == set(FINANCE)
        and all(
            type(picks[seat]) is int and picks[seat] == plans[seat]["pick"]
            for seat in FINANCE
        ),
        "timing_picks",
    )
    need(timing["acknowledged"] == list(FINANCE), "timing_acknowledged")


def owner_turn(owner: Path, purpose: str, prompt: str, reply: str) -> Row:
    need(
        private_dir(owner, f"owner_dir:{purpose}") == {ADMISSION, purpose},
        f"owner_entries:{purpose}",
    )
    admission = raw(owner / ADMISSION)
    need(
        admission == agy_seat.admission("complete", None), f"owner_admission:{purpose}"
    )
    private_dir(owner / purpose, f"owner_dir:{purpose}")
    bound = {"prompt_sha256": agy_seat.sha(prompt), "reply_sha256": agy_seat.sha(reply)}
    owned = rebuilt(
        f"owner_receipt:{purpose}",
        owner_receipt.owner_turn,
        owner,
        purpose,
        prompt,
        bound,
    )
    need(
        owned["status"] == "bound" and owned["text"] == reply,
        f"owner_binding:{purpose}",
    )
    return {**owned, "digests": {"admission": sha(admission), **owned["digests"]}}


def live_row(owner: Path, step: Row) -> Row:
    turn, purpose = step["turn"], step["purpose"]
    meta = turn["meta"]
    need(meta.keys() == META_KEYS, f"turn_meta:{step['seat']}:{purpose}")
    owned = owner_turn(owner / purpose, purpose, step["prompt"], turn["reply"])
    receipt = owned["receipt"]
    expected = {
        "session_sha256": receipt["session_sha256"],
        "prompt_sha256": receipt["prompt_sha256"],
        "answer_sha256": receipt["answer_sha256"],
        "requested_model": receipt["requested_model"],
        "served_model": receipt["server_model"],
        "client_tokens": receipt["usage"]["total_tokens"],
        "tool_steps": receipt["observed_tool_steps"],
        "credit_fallback": receipt["ai_credit_fallback"],
    }
    need(same(meta, expected), f"turn_meta_receipt:{step['seat']}:{purpose}")
    need(
        meta["requested_model"] == agy_seat.MODEL
        and meta["tool_steps"] == 0
        and meta["credit_fallback"] is False
        and receipt["api_key_passed"] is False
        and type(meta["client_tokens"]) is int
        and meta["client_tokens"] > 0,
        f"turn_subscription:{step['seat']}:{purpose}",
    )
    return {
        **expected,
        "client_usage": dict(receipt["usage"]),
        "owner_sha256": owned["digests"],
    }


def control_row(owner: Path, step: Row, token: str | None) -> Row:
    turn, purpose = step["turn"], step["purpose"]
    replies = {"sales": CONTROL_SALES, "finance-price": CONTROL_PRICE.get(step["seat"])}
    fixed = replies.get(purpose) or canonical({"ack": token}).decode("ascii")
    need(same(turn["meta"], CONTROL_META), f"control_meta:{step['seat']}:{purpose}")
    need(turn["reply"] == fixed, f"control_reply:{step['seat']}:{purpose}")
    need(
        not (owner.exists() or owner.is_symlink())
        or private_dir(owner, "control_owner") == set(),
        "control_owner",
    )
    return {
        "session_sha256": None,
        "prompt_sha256": agy_seat.sha(step["prompt"]),
        "answer_sha256": agy_seat.sha(turn["reply"]),
        "requested_model": None,
        "served_model": None,
        "client_tokens": None,
        "tool_steps": None,
        "credit_fallback": None,
        "client_usage": None,
        "owner_sha256": None,
    }


def attest(
    steps: list[Row], owners: dict[str, Path], tokens: Row, control: bool
) -> list[Row]:
    need(len(steps) == TURN_COUNT, "turn_count")
    rows = []
    for step in steps:
        seat, owner = step["seat"], owners[step["seat"]]
        if control:
            facts = control_row(owner, step, tokens.get(seat))
        else:
            if step["purpose"] == PURPOSES[seat][0]:
                need(
                    private_dir(owner, f"owner_root:{seat}") == set(PURPOSES[seat]),
                    f"owner_root:{seat}",
                )
            facts = live_row(owner, step)
        rows.append(
            {
                "seat": seat,
                "purpose": step["purpose"],
                "view_sha256": step["turn"]["view_sha256"],
                "reply": step["turn"]["reply"],
                **facts,
            }
        )
    sessions = [row["session_sha256"] for row in rows]
    need(control or len(set(sessions)) == TURN_COUNT, "shared_provider_session")
    return rows


def evidence(out: Path, binding: Row, early: str, *, control: bool = False) -> Row:
    need(type(control) is bool and early in FINANCE, "arguments")
    world = check_binding(binding)
    root = out.expanduser().resolve(strict=True)
    config, repeat, staged = declared(root, world, early, control)
    scenario_sha = staged_task(root, binding, early)
    trial = root / "trials" / config["trial_name"]
    result, native_sha = native_trial(trial, config)
    timing, world_sha = native_world(trial)
    seats = seat_turns(result)
    report, sales = sales_plan(binding, seats[SALES][0])
    plans = {
        seat: finance_plan(binding, early, report, seat, seats[seat])
        for seat in FINANCE
    }
    digests = {
        SALES: sales["digests"],
        **{seat: plans[seat]["digests"] for seat in FINANCE},
    }
    events = check_events(timing["events"], digests)
    check_timing(timing, binding, early, report, plans)
    tokens = {seat: plans[seat]["token"] for seat in FINANCE}
    steps = [
        sales["step"],
        *(step for seat in FINANCE for step in plans[seat]["steps"]),
    ]
    turns = attest(steps, staged["owners"], tokens, control)
    late = FINANCE[1 - FINANCE.index(early)]
    return {
        "schema": SCHEMA,
        "kind": "control" if control else "live",
        "control": control,
        "world": world,
        "repeat": repeat,
        "shape": binding["shape"],
        "trial_name": config["trial_name"],
        "native_id": native_id(result),
        "protocol_reward": native_sha["reward_value"],
        "early": early,
        "late": late,
        "checkpoint_sha256": binding["checkpoint_sha256"],
        "report": report,
        "picks": {seat: plans[seat]["pick"] for seat in FINANCE},
        "ack_tokens": tokens,
        "acknowledged": list(FINANCE),
        "events": events,
        "turns": turns,
        "client_tokens": None
        if control
        else sum(row["client_tokens"] for row in turns),
        "sha256": {
            "config": staged["config_sha256"],
            "scenario": scenario_sha,
            "result": native_sha["result"],
            "reward": native_sha["reward"],
            **world_sha,
        },
        "notes": [CONTROL_NOTE] if control else list(LIVE_NOTES),
    }
