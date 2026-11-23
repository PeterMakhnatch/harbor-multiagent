import argparse
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn, override
from uuid import UUID

from ..visibility import agy_seat, calendar_protocol
from ..visibility import calendar_packet as packet
from ..wait_peer import receipt as base
from ..wait_peer.receipt import (
    CODE,
    HEX64,
    Bad,
    binary,
    canonical,
    lines,
    moment,
    need,
    present,
    read,
    record,
    row_list,
    same,
    sealed,
)
from . import control, protocol, screen

DESCRIPTION = (
    "Audit one four-seat fan-in trial cell and print one redacted canonical row. "
    "Exit 2 with a fixed category when any record contradicts another."
)
SCHEMA = "seatlab.peer-fanin-evidence/v1"
LAUNCH_SCHEMA = "seatlab.fanin-launch/v1"
SEAT_IMPORT = "examples.seatlab.fanin.seat:FanInSeat"
AGENT = ("seatlab-fanin-seat", "1.0.0", None)
WORLDS = range(1200, 1208)
SEATS = protocol.SEATS
TAGS = ("live", "control-correct", "control-inexact", "control-wrong")
MODES = {
    "control-correct": "correct",
    "control-inexact": "inexact",
    "control-wrong": "wrong",
}
ACTIONS = ("report", "skip")
COMMANDS = {protocol.wire({"action": action}): action for action in ACTIONS}
MAX_TEXT = 4000
MAX_MESSAGES = 2 * (len(SEATS) - 1)
MAX_TREE = 256
WORLD = {
    "world_version": 2,
    "condition": "talk",
    "seats": list(SEATS),
    "limits": {"max_messages": 200, "max_text": MAX_TEXT},
}
ANSWERS = [calendar_protocol.answer(slot) for slot in range(protocol.SLOTS)]
LAUNCH_KEYS = {
    "schema",
    "world",
    "arm",
    "tag",
    "trial_name",
    "generator_commit",
    "seed",
    "task_name",
    "task_checksum",
    "config_sha256",
    "returncode",
    "error",
    "exception",
    "result_present",
    "reaped",
    "owners",
    "accepted_seats",
}
OWNER_KEYS = {"accepted", "relay_returncode", "relay_stopped", "record_present"}
META_KEYS = {
    "seat",
    "role",
    "stage",
    "error",
    "started_turn",
    "prompt_sha256",
    "reply_sha256",
    "commands",
    "sent",
    "polls",
}
STAGES = {
    "booker": {
        "starting",
        "identity",
        "calendar",
        "requesting",
        "waiting_report",
        "skipping",
        "booking",
        "submitting",
        "done",
    },
    "reporter": {
        "starting",
        "identity",
        "calendar",
        "waiting_command",
        "reporting",
        "replying",
        "done",
    },
}
ERRORS = {
    "invalid_seat_binding",
    "invalid_transport",
    "inbox_timeout",
    "calendar_invalid",
    "command_unbound",
    "turn_timeout",
    "turn_failed",
    "book_invalid",
    "submit_rejected",
    "cancelled",
    "seat_failed",
}
CONTROL_NOTE = (
    "Control turns come from a fixed external command; they are not model sessions."
)
Row = dict[str, Any]


def trial_name(world: int, arm: str, tag: str) -> str:
    return f"sf4-{world}-{arm}-{tag}"


def task_name(world: int) -> str:
    return f"schedule-{world}-talk"


def entries(directory: Path) -> set[str]:
    try:
        packet._directory(directory)
    except packet._Invalid:
        raise Bad("unsafe_directory") from None
    return {path.name for path in packet._files(directory)}


def launched(cell: Path, world: int, arm: str, tag: str) -> tuple[bytes, Row]:
    raw, launch = sealed(cell / "launch.json")
    need(
        launch.keys() == LAUNCH_KEYS
        and launch["schema"] == LAUNCH_SCHEMA
        and type(launch["world"]) is int
        and (launch["world"], launch["arm"], launch["tag"]) == (world, arm, tag)
        and launch["trial_name"] == trial_name(world, arm, tag)
        and launch["generator_commit"] == screen.GENERATOR_COMMIT
        and type(launch["seed"]) is int
        and launch["seed"] == screen.SEED,
        "launch_binding",
    )
    need(
        launch["task_name"] in (None, f"flotilla/{task_name(world)}")
        and all(
            launch[k] is None
            or isinstance(launch[k], str)
            and HEX64.fullmatch(launch[k])
            for k in ("task_checksum", "config_sha256")
        )
        and (launch["returncode"] is None or type(launch["returncode"]) is int)
        and (
            launch["error"] is None
            or isinstance(launch["error"], str)
            and 0 < len(launch["error"]) <= 200
        )
        and (
            launch["exception"] is None
            or isinstance(launch["exception"], str)
            and launch["exception"].isidentifier()
        )
        and type(launch["result_present"]) is bool
        and type(launch["reaped"]) is bool,
        "launch_shape",
    )
    owners = launch["owners"]
    need(isinstance(owners, dict) and owners.keys() == set(SEATS), "launch_owner_shape")
    for seat in SEATS:
        owner = owners[seat]
        need(
            isinstance(owner, dict)
            and owner.keys() == OWNER_KEYS
            and owner["accepted"] in (True, False, None)
            and type(owner["accepted"]) in (bool, type(None))
            and (
                owner["relay_returncode"] is None
                or type(owner["relay_returncode"]) is int
            )
            and type(owner["relay_stopped"]) is bool
            and type(owner["record_present"]) is bool,
            "launch_owner_shape",
        )
    need(
        launch["accepted_seats"] == [s for s in SEATS if owners[s]["accepted"] is True],
        "launch_owner_shape",
    )
    need(
        launch["error"] is not None
        or launch["returncode"] == 0
        and launch["result_present"]
        and launch["reaped"]
        and None
        not in (launch["task_name"], launch["task_checksum"], launch["config_sha256"]),
        "launch_binding",
    )
    return raw, launch


def launch_reason(launch: Row) -> str | None:
    error = launch["error"]
    if error is None:
        return None
    return error if CODE.fullmatch(error) else "driver_rejected"


def native_checksum(task: Path) -> str:
    order = [task]
    index = 0
    while index < len(order):
        need(len(order) <= MAX_TREE, "task_tree_size")
        for path in packet._files(order[index]):
            if stat.S_ISDIR(path.lstat().st_mode):
                packet._directory(path)
                order.append(path)
        index += 1
    hashes: dict[Path, str] = {}
    for directory in reversed(order):
        parts = []
        for path in packet._files(directory):
            if path in hashes:
                parts.append(f"dirhash:{hashes[path]}\0name:{path.name}")
            else:
                digest = packet._digest(packet._read(path))
                parts.append(f"data:{digest}\0name:{path.name}")
        need(parts, "task_tree_empty")
        hashes[directory] = packet._digest("\0\0".join(sorted(parts)).encode("utf-8"))
    return hashes[task]


def stock_task(
    cell: Path, world: int, launch: Row
) -> tuple[Path, dict[str, list[int]], int, str]:
    need(entries(cell / "tasks") == {"schedule"}, "task_location")
    schedule = cell / "tasks" / "schedule"
    need(entries(schedule) == {task_name(world)}, "task_location")
    task = schedule / task_name(world)
    try:
        found, target, calendar = screen.world_source(task, world)
    except packet._Invalid:
        raise Bad("stock_invalid") from None
    starts = {seat: list(found[seat]) for seat in SEATS}
    need(
        all(starts[seat] == sorted(set(starts[seat])) for seat in SEATS)
        and set.intersection(*(set(v) for v in starts.values())) == {target},
        "stock_answer_binding",
    )
    need(native_checksum(task) == launch["task_checksum"], "task_checksum_binding")
    need(HEX64.fullmatch(calendar) is not None, "stock_invalid")
    return task, starts, target, calendar


def configured(
    cell: Path, task: Path, world: int, arm: str, tag: str, launch: Row
) -> Row:
    raw, cfg = sealed(cell / "config.json")
    need(packet._digest(raw) == launch["config_sha256"], "config_binding")
    path, trials = (cfg.get("task") or {}).get("path"), cfg.get("trials_dir")
    need(
        isinstance(path, str)
        and isinstance(trials, str)
        and Path(path).resolve(strict=True) == task
        and Path(trials).resolve(strict=True) == cell / "trials",
        "config_location",
    )
    expected = {
        "schema_version": 1,
        "task": {"path": path, "git_url": None, "name": None},
        "trial_name": trial_name(world, arm, tag),
        "trials_dir": trials,
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": [
            {
                "seat": seat,
                "name": None,
                "import_path": SEAT_IMPORT,
                "model_name": None,
                "kwargs": {"world": world, "arm": arm, "seat": seat},
            }
            for seat in SEATS
        ],
    }
    need(raw == canonical(expected), "config_binding")
    return cfg


def native(result: Row, cfg: Row, launch: Row, trial: Path) -> str:
    identifier = UUID(result["id"])
    need(
        str(identifier) == result["id"] and identifier.version == 4, "native_trial_uuid"
    )
    actual = result["config"]
    env = actual["environment"]
    need(
        result["trial_name"] == trial.name
        and result["task_name"] == launch["task_name"]
        and result["task_checksum"] == launch["task_checksum"]
        and same(result["task_id"], {"path": cfg["task"]["path"]})
        and result["trial_uri"] == (Path(cfg["trials_dir"]) / trial.name).as_uri()
        and actual["trial_name"] == trial.name
        and actual["trials_dir"] == cfg["trials_dir"]
        and actual["task"]["path"] == cfg["task"]["path"]
        and all(same(env.get(k), v) for k, v in cfg["environment"].items())
        and not any(
            env.get(k)
            for k in ("env", "mounts", "extra_docker_compose", "import_path", "kwargs")
        )
        and actual["verifier"].get("disable") is False
        and not any(
            actual["verifier"].get(k) for k in ("env", "import_path", "kwargs")
        ),
        "native_trial_binding",
    )
    roster = actual["participants"]
    need(isinstance(roster, list) and len(roster) == len(SEATS), "native_roster_size")
    for got, want in zip(roster, cfg["participants"], strict=True):
        need(
            all(
                same(got.get(k), want[k])
                for k in ("seat", "import_path", "model_name", "kwargs")
            )
            and not got.get("env"),
            "native_roster_binding",
        )
    return str(identifier)


def seat_states(result: Row) -> tuple[dict[str, Row | None], dict[str, bool]]:
    seats = row_list(result.get("seat_results"), "native_seat_roster")
    need([s.get("seat") for s in seats] == list(SEATS), "native_seat_roster")
    states: dict[str, Row | None] = {}
    raised: dict[str, bool] = {}
    for seat in seats:
        info = seat["agent_info"]
        need(
            seat["under_test"] is True
            and (info["name"], info["version"], info["model_info"]) == AGENT,
            "native_seat_identity",
        )
        context = seat["agent_result"]
        raised[seat["seat"]] = seat["exception_info"] is not None
        states[seat["seat"]] = (
            None if context is None else packet._object(context["metadata"])
        )
    return states, raised


def hexes(value: object) -> bool:
    return (
        value is None or isinstance(value, str) and HEX64.fullmatch(value) is not None
    )


def counter(value: object) -> bool:
    return type(value) is int and value >= 0


def metadata(meta: Row | None, seat: str, role: str) -> Row | None:
    if meta is None:
        return None
    need(meta.keys() == META_KEYS, "seat_metadata_shape")
    need(meta["seat"] == seat and meta["role"] == role, "seat_role_binding")
    done = meta["stage"] == "done"
    need(
        meta["stage"] in STAGES[role]
        and meta["error"] in ERRORS | {None}
        and (not done or meta["error"] is None),
        "seat_phase",
    )
    commands = meta["commands"]
    need(
        type(meta["started_turn"]) is bool
        and hexes(meta["prompt_sha256"])
        and hexes(meta["reply_sha256"])
        and meta["started_turn"] == (meta["prompt_sha256"] is not None)
        and (meta["reply_sha256"] is None or meta["started_turn"])
        and isinstance(commands, list)
        and all(c in ACTIONS for c in commands)
        and len(commands) <= (len(SEATS) - 1 if role == "booker" else 1)
        and counter(meta["sent"])
        and meta["sent"] <= (len(SEATS) - 1 if role == "booker" else 1)
        and counter(meta["polls"]),
        "seat_metadata_shape",
    )
    if not done:
        return meta
    if role == "booker":
        need(
            len(commands) == len(SEATS) - 1 and meta["reply_sha256"] is not None,
            "seat_done_incomplete",
        )
    else:
        need(
            commands == ["skip"]
            and not meta["started_turn"]
            and meta["sent"] == 0
            or commands == ["report"]
            and meta["reply_sha256"] is not None
            and meta["sent"] == 1,
            "seat_done_incomplete",
        )
    return meta


def world_messages(trial: Path) -> list[Row]:
    path = trial / "artifacts/logs/world/messages.jsonl"
    rows = lines(read(path)) if present(path) else []
    need(len(rows) <= MAX_MESSAGES, "extra_messages")
    previous = 0
    for i, msg in enumerate(rows, 1):
        need(
            msg.keys() == {"id", "from", "to", "text", "t"}
            and msg["id"] == f"m{i}"
            and msg["from"] in SEATS
            and msg["to"] in SEATS
            and msg["from"] != msg["to"]
            and isinstance(msg["text"], str)
            and 0 < len(msg["text"]) <= MAX_TEXT
            and moment(msg["t"])
            and msg["t"] >= previous,
            "message_scope",
        )
        previous = msg["t"]
    return rows


def report_value(text: str, reporter: str, own: list[int]) -> Row:
    try:
        value = packet._object(packet._json(text))
    except packet._Invalid:
        raise Bad("message_wire") from None
    starts, exact = value.get("starts"), value.get("exact")
    need(
        value.keys() == {"exact", "starts"}
        and text == protocol.wire(value)
        and type(exact) is bool
        and (
            starts is None
            or isinstance(starts, list)
            and protocol.parse_report(protocol.wire({"starts": starts})) == starts
        ),
        "message_wire",
    )
    need(exact == (starts == own), "report_exact_binding")
    return {"seat": reporter, "exact": exact, "starts": starts}


def exchange(
    messages: list[Row], order: tuple[str, ...], starts: dict[str, list[int]], arm: str
) -> list[Row]:
    booker, k, rounds = order[0], 0, []
    for reporter in order[1:]:
        if k == len(messages):
            break
        command = messages[k]
        need(
            (command["from"], command["to"]) == (booker, reporter)
            and command["text"] in COMMANDS,
            "command_binding",
        )
        k += 1
        entry: Row = {
            "seat": reporter,
            "action": COMMANDS[command["text"]],
            "command": command,
            "reply": None,
            "report": None,
        }
        rounds.append(entry)
        if entry["action"] == "skip":
            continue
        if k == len(messages):
            break
        reply = messages[k]
        need((reply["from"], reply["to"]) == (reporter, booker), "reply_binding")
        entry.update(
            reply=reply, report=report_value(reply["text"], reporter, starts[reporter])
        )
        k += 1
    need(k == len(messages), "extra_messages")
    asking = True
    for j, entry in enumerate(rounds):
        prior = [e["report"] for e in rounds[:j] if e["report"] is not None]
        try:
            asking = asking and protocol.ask_next(arm, starts[booker], prior)
        except ValueError:
            raise Bad("early_stop_rule") from None
        need(entry["action"] == ACTIONS[0 if asking else 1], "early_stop_rule")
    return rounds


def causes(rounds: list[Row], booker: str) -> dict[str, tuple[str, str]]:
    needed: dict[str, tuple[str, str]] = {}
    for j, entry in enumerate(rounds):
        if entry["reply"] is not None:
            needed[entry["reply"]["id"]] = (entry["seat"], entry["command"]["id"])
        if j and rounds[j - 1]["reply"] is not None:
            needed[entry["command"]["id"]] = (booker, rounds[j - 1]["reply"]["id"])
    return needed


def settled(rounds: list[Row]) -> bool:
    return len(rounds) == len(SEATS) - 1 and all(
        e["action"] == "skip" or e["reply"] is not None for e in rounds
    )


def timeline(trial: Path, messages: list[Row], rounds: list[Row], booker: str) -> Row:
    path = trial / "artifacts/logs/world/events.jsonl"
    state: Row = {
        "delivered": {seat: set() for seat in SEATS},
        "polls": dict.fromkeys(SEATS, 0),
        "identified": set(),
        "submit": None,
        "frozen": False,
    }
    if not present(path):
        need(not messages, "missing_world_events")
        return state
    events = lines(read(path))
    need(events, "missing_world_events")
    needed = causes(rounds, booker)
    replies = {e["reply"]["id"] for e in rounds if e["reply"] is not None}
    sent, previous = 0, 0
    for i, event in enumerate(events, 1):
        need(
            event.keys() == {"seq", "kind", "seat", "t", "data"}
            and type(event["seq"]) is int
            and event["seq"] == i
            and moment(event["t"])
            and event["t"] >= previous,
            "world_event_sequence",
        )
        kind, seat, data = event["kind"], event["seat"], event["data"]
        if i == 1:
            need(kind == "start" and seat is None and same(data, WORLD), "world_start")
        elif kind == "health":
            need(seat is None and data == {}, "world_health")
        elif kind == "whoami":
            need(
                seat in SEATS and seat not in state["identified"] and data == {},
                "world_identity",
            )
            state["identified"].add(seat)
        elif seat == "admin":
            need(kind in ("freeze", "state") and data == {}, "world_admin_event")
            state["frozen"] = True
        else:
            need(seat in state["identified"], "world_event_actor")
            sent = world_action(
                state, event, messages, sent, previous, needed, replies, rounds, booker
            )
        previous = event["t"]
    need(sent == len(messages), "world_log_binding")
    return state


def world_action(
    state: Row,
    event: Row,
    messages: list[Row],
    sent: int,
    previous: float,
    needed: dict[str, tuple[str, str]],
    replies: set[str],
    rounds: list[Row],
    booker: str,
) -> int:
    kind, seat, data, t = event["kind"], event["seat"], event["data"], event["t"]
    delivered = state["delivered"]
    if kind in ("send", "submit"):
        need(state["submit"] is None and not state["frozen"], "world_event_actor")
    if kind == "send":
        need(sent < len(messages), "send_order")
        msg = messages[sent]
        cause = needed.get(msg["id"])
        need(cause is None or cause[1] in delivered[cause[0]], "send_order")
        need(
            previous <= msg["t"] <= t
            and seat == msg["from"]
            and same(
                data, {"id": msg["id"], "to": msg["to"], "chars": len(msg["text"])}
            ),
            "send_binding",
        )
        return sent + 1
    if kind == "deliver":
        ids = [msg["id"] for msg in messages[:sent] if msg["to"] == seat]
        need(same(data, {"ids": ids}), "deliver_binding")
        delivered[seat].update(ids)
        state["polls"][seat] += 1
        return sent
    if kind == "submit":
        need(
            seat == booker
            and settled(rounds)
            and sent == len(messages)
            and replies <= delivered[booker]
            and isinstance(data, dict)
            and data.keys() == {"n", "answer"}
            and type(data["n"]) is int
            and data["n"] == 1
            and data["answer"] in ANSWERS,
            "submit_event_binding",
        )
        state["submit"] = (t, data["answer"])
        return sent
    raise Bad("unexpected_world_event")


def stock_reward(
    trial: Path,
    result: Row,
    messages: list[Row],
    submit: Any,
    world: int,
    booker: str,
    target: int,
) -> int | None:
    rewards = (result.get("verifier_result") or {}).get("rewards")
    reward = (
        binary(rewards.get("reward"))
        if isinstance(rewards, dict) and rewards.keys() == {"reward"}
        else None
    )
    if reward is None:
        return None
    logs = trial / "verifier"
    frozen = record(logs / "world-state.json")
    subs = frozen.get("submissions")
    if not isinstance(subs, list):
        raise Bad("submission_count")
    need(len(subs) == int(submit is not None), "submission_count")
    need(
        same(
            frozen,
            {
                **WORLD,
                "frozen": True,
                "messages": messages,
                "submissions": subs,
                "sent": {
                    seat: sum(m["from"] == seat for m in messages) for seat in SEATS
                },
            },
        ),
        "frozen_world_binding",
    )
    answer = None
    if submit is not None:
        entry = packet._object(subs[0])
        need(
            entry.keys() == {"n", "seat", "answer", "t"}
            and type(entry["n"]) is int
            and entry["n"] == 1
            and entry["seat"] == booker
            and moment(entry["t"])
            and entry["t"] <= submit[0]
            and same(entry["answer"], submit[1]),
            "native_submission_shape",
        )
        answer = submit[1]
    won = int(answer is not None and same(answer, calendar_protocol.answer(target)))
    file_reward, score = record(logs / "reward.json"), record(logs / "score.json")
    need(
        file_reward.keys() == {"reward"}
        and binary(file_reward["reward"]) == reward == won
        and binary(score.get("score")) == won
        and result.get("reward_valid") is True,
        "stock_reward_mismatch",
    )
    need(
        same(
            {k: v for k, v in score.items() if k != "score"},
            {
                "deceived": None,
                "error": None,
                "family": "schedule",
                "instance": world,
                "condition": "talk",
                "seats": list(SEATS),
                "world_version": 2,
                "fields_correct": won,
                "fields_total": 1,
                "answer": answer,
                "submitted_by": booker if answer is not None else None,
                "n_submissions": len(subs),
                "n_messages": len(messages),
            },
        ),
        "stock_score_binding",
    )
    return reward


def prompts(
    order: tuple[str, ...], starts: dict[str, list[int]], rounds: list[Row]
) -> dict[str, str | None]:
    actions = {e["seat"]: e["action"] for e in rounds}
    plan: dict[str, str | None] = {
        seat: protocol.report_prompt(starts[seat])
        if actions.get(seat) == "report"
        else None
        for seat in order[1:]
    }
    reports = [e["report"] for e in rounds if e["report"] is not None]
    plan[order[0]] = (
        protocol.book_prompt(starts[order[0]], reports) if settled(rounds) else None
    )
    return plan


def tolerant(meta: Row, key: str, actual: int) -> bool:
    if meta["stage"] == "done":
        return meta[key] == actual
    return actual - 1 <= meta[key] <= actual


def bind_seat(
    seat: str,
    meta: Row | None,
    prompt: str | None,
    rounds: list[Row],
    messages: list[Row],
    state: Row,
) -> None:
    if meta is None:
        return
    delivered = state["delivered"][seat]
    need(
        tolerant(meta, "sent", sum(m["from"] == seat for m in messages))
        and tolerant(meta, "polls", state["polls"][seat])
        and (meta["stage"] in ("starting", "identity") or seat in state["identified"]),
        "seat_world_binding",
    )
    if meta["role"] == "booker":
        actions = [e["action"] for e in rounds]
        need(
            meta["commands"] == actions
            or meta["stage"] != "done"
            and meta["commands"] == actions[:-1],
            "seat_command_binding",
        )
        replies = {e["reply"]["id"] for e in rounds if e["reply"] is not None}
        need(
            not meta["started_turn"] or prompt is not None and replies <= delivered,
            "turn_prompt_binding",
        )
    else:
        entry = next((e for e in rounds if e["seat"] == seat), None)
        received = entry is not None and entry["command"]["id"] in delivered
        need(
            meta["commands"] == ([entry["action"]] if received else [])
            or received
            and meta["stage"] != "done"
            and meta["commands"] == [],
            "seat_command_binding",
        )
        need(
            not meta["started_turn"] or meta["commands"] == ["report"],
            "turn_prompt_binding",
        )
        need(
            entry is None or entry["reply"] is None or meta["reply_sha256"] is not None,
            "reply_without_turn",
        )
    need(
        not meta["started_turn"]
        or prompt is not None
        and meta["prompt_sha256"] == agy_seat.sha(prompt),
        "turn_prompt_binding",
    )


def control_turn(
    owner: Path, seat: str, prompt: str | None, turn: Row | None, mode: str, world: int
) -> Row:
    directory = owner / seat
    if not present(directory):
        need(turn is None, "owner_turn_missing")
        return {"status": "absent"}
    names = entries(directory)
    need(names in ({"request.json"}, {"request.json", "reply.json"}), "owner_entries")
    if prompt is None:
        raise Bad("owner_prompt_binding")
    need(
        read(directory / "request.json")
        == control.line({"schema": control.SCHEMA, "prompt": prompt}),
        "owner_prompt_binding",
    )
    if names == {"request.json"}:
        need(turn is None, "owner_reply_missing")
        return {"status": "unanswered"}
    raw = read(directory / "reply.json")
    reply = packet._object(packet._json(raw))
    text = reply.get("text")
    if not isinstance(text, str):
        raise Bad("owner_record_shape")
    need(
        reply.keys() == {"text"} and raw == control.line(reply),
        "owner_record_shape",
    )
    need(text == control.answer(mode, world, seat, prompt), "control_reply_binding")
    need(
        turn is None
        or turn["prompt_sha256"] == agy_seat.sha(prompt)
        and turn["reply_sha256"] == agy_seat.sha(text),
        "owner_turn_binding",
    )
    return {"status": "bound" if turn is not None else "answered_unused", "text": text}


def not_admitted(
    owner: Path, seat: str, prompt: str | None, meta: Row | None, launch: Row
) -> bool:
    directory = owner / seat
    if not present(directory) or entries(directory):
        return False
    need(
        stat.S_IMODE(directory.lstat().st_mode) == 0o700
        and prompt is not None
        and meta is not None
        and meta["started_turn"] is True
        and meta["error"] == "turn_failed"
        and meta["stage"] != "done"
        and meta["reply_sha256"] is None
        and launch["owners"][seat]["accepted"] is True,
        "owner_entries",
    )
    return True


def owner_turn(
    owner: Path, seat: str, prompt: str | None, meta: Row | None, launch: Row
) -> Row:
    turn = (
        None
        if meta is None or meta["reply_sha256"] is None
        else {
            "prompt_sha256": meta["prompt_sha256"],
            "reply_sha256": meta["reply_sha256"],
        }
    )
    need(
        not present(owner / seat) or meta is None or meta["started_turn"],
        "owner_without_turn",
    )
    tag = launch["tag"]
    if tag == "live" and not_admitted(owner, seat, prompt, meta, launch):
        return {"status": "not_admitted"}
    if tag == "live":
        return base.owner_turn(owner, seat, prompt, turn)
    return control_turn(owner, seat, prompt, turn, MODES[tag], launch["world"])


def distinct_sessions(rows: dict[str, Row]) -> None:
    sessions = [r["receipt"]["session_sha256"] for r in rows.values() if "receipt" in r]
    need(len(set(sessions)) == len(sessions), "shared_provider_session")


def owner_turns(
    cell: Path,
    order: tuple[str, ...],
    plan: dict[str, str | None],
    metas: dict[str, Row | None],
    launch: Row,
) -> dict[str, Row]:
    owner = cell / "owner"
    need(entries(owner) <= set(SEATS), "owner_entries")
    rows = {
        seat: owner_turn(owner, seat, plan[seat], metas[seat], launch) for seat in order
    }
    distinct_sessions(rows)
    return rows


def owner_launch(
    cell: Path, launch: Row, rounds: list[Row], metas: dict[str, Row | None]
) -> None:
    skipped = {e["seat"] for e in rounds if e["action"] == "skip"}
    for seat in SEATS:
        state = launch["owners"][seat]
        meta = metas[seat]
        recorded = present(cell / "owner" / seat)
        need(
            state["record_present"] == recorded
            and (not recorded or state["accepted"] is True),
            "owner_launch_binding",
        )
        need(
            seat not in skipped or not recorded and state["accepted"] is not True,
            "skipped_owner_called",
        )
        need(
            meta is None or meta["started_turn"] or state["accepted"] is not True,
            "owner_called_without_turn",
        )


def replies_bound(
    order: tuple[str, ...],
    starts: dict[str, list[int]],
    rounds: list[Row],
    owned: dict[str, Row],
    metas: dict[str, Row | None],
    submit: Any,
) -> int | None:
    for entry in rounds:
        text = owned[entry["seat"]].get("text")
        reply = entry["reply"]
        if reply is None:
            continue
        if text is None:
            raise Bad("reply_without_owner_turn")
        need(
            reply["text"]
            == protocol.wire(protocol.outgoing(starts[entry["seat"]], text)),
            "reply_commitment",
        )
    text = owned[order[0]].get("text")
    booked = None if text is None else protocol.parse_book(text)
    meta = metas[order[0]]
    if text is not None and booked is None:
        need(
            submit is None and (meta is None or meta["error"] == "book_invalid"),
            "choice_submission_binding",
        )
    need(
        submit is None
        or booked is not None
        and same(submit[1], calendar_protocol.answer(booked)),
        "choice_submission_binding",
    )
    return booked


def native_failure(launch: Row, result: Row) -> str | None:
    reason = launch_reason(launch)
    if reason is not None:
        return reason
    if launch["returncode"] != 0:
        return "native_nonzero_exit"
    if (
        result.get("exception_info") is not None
        or not result.get("finished_at")
        or result.get("step_results")
        or result.get("agent_result") is not None
    ):
        return "native_trial_exception"
    return None


def incomplete_reason(
    launch: Row,
    result: Row,
    order: tuple[str, ...],
    metas: dict[str, Row | None],
    raised: dict[str, bool],
    rounds: list[Row],
    state: Row,
    reward: int | None,
    owned: dict[str, Row],
) -> str | None:
    reason = native_failure(launch, result)
    if reason is not None:
        return reason
    for seat in order:
        meta = metas[seat]
        if raised[seat]:
            return f"{seat}:seat_exception"
        if meta is None:
            return f"{seat}:missing_seat_result"
        if meta["stage"] != "done":
            return f"{seat}:{meta['error'] or 'seat_interrupted'}"
    if (
        state["identified"] != set(SEATS)
        or not settled(rounds)
        or state["submit"] is None
    ):
        return "behavior_incomplete"
    if reward is None:
        return "missing_native_reward"
    for seat in order:
        meta = metas[seat]
        if meta is None:
            return f"{seat}:missing_seat_result"
        expected = "bound" if meta["started_turn"] else "absent"
        if owned[seat]["status"] != expected:
            return "owner_turn_unbound"
    return None


def turn_rows(order: tuple[str, ...], owned: dict[str, Row]) -> list[Row]:
    rows = []
    for position, seat in enumerate(order):
        receipt = owned[seat].get("receipt")
        rows.append(
            {
                "seat": seat,
                "role": "booker" if position == 0 else "reporter",
                "position": position,
                "status": owned[seat]["status"],
                "session_sha256": None
                if receipt is None
                else receipt["session_sha256"],
                "total_tokens": None
                if receipt is None
                else receipt["usage"]["total_tokens"],
            }
        )
    return rows


def control_as_designed(
    tag: str, complete: bool, reward: int | None, rounds: list[Row]
) -> bool | None:
    if tag == "live":
        return None
    exact = [e["report"]["exact"] for e in rounds if e["report"] is not None]
    if tag == "control-correct":
        return complete and reward == 1 and bool(exact) and all(exact)
    if tag == "control-inexact":
        return (
            complete
            and [e["action"] for e in rounds] == ["report"] * 3
            and exact
            == [
                False,
                True,
                True,
            ]
        )
    return complete and reward == 0 and bool(exact) and all(exact)


def claims(
    tag: str,
    complete: bool,
    reward: int | None,
    state: Row,
    rounds: list[Row],
    owned: dict[str, Row],
) -> Row:
    receipts = [r["receipt"] for r in owned.values() if "receipt" in r]
    bound = complete and all(r["status"] in ("bound", "absent") for r in owned.values())
    live = tag == "live"
    return {
        "native_trial_complete": complete,
        "four_physical_seats_under_test": True,
        "four_whoami": state["identified"] == set(SEATS),
        "commands_follow_stop_rule": True,
        "skipped_reporters_never_called": all(
            owned[e["seat"]]["status"] == "absent"
            for e in rounds
            if e["action"] == "skip"
        ),
        "prompts_reconstructed": True,
        "stock_source_verified": True,
        "stock_reward_verified": reward is not None,
        "owner_answers_bound": bound,
        "separate_provider_sessions": live
        and bound
        and len({r["session_sha256"] for r in receipts}) == len(receipts) > 0,
        "client_echoed_requested_model": live and bound and len(receipts) > 0,
        "served_model_reported": False,
        "zero_tool_steps_reported": live and bound and len(receipts) > 0,
    }


def base_row(
    world: int, arm: str, tag: str, cell: Path, launch: Row, digests: Row
) -> Row:
    owned = [present(cell / "owner" / seat) for seat in protocol.order(world)]
    return {
        "schema": SCHEMA,
        "world": world,
        "arm": arm,
        "tag": tag,
        "trial_id": None,
        "complete": False,
        "incomplete_reason": launch_reason(launch) or "missing_native_result",
        "native_reward": None,
        "booker": protocol.order(world)[0],
        "reporters": list(protocol.order(world)[1:]),
        "booker_turns": int(owned[0]),
        "reporter_turns": sum(owned[1:]),
        "model_sessions": None,
        "session_sha256s": None,
        "client_tokens": None,
        "reporters_asked": None,
        "reporters_skipped": None,
        "all_reports_exact": None,
        "control_as_designed": None if tag == "live" else False,
        "turns": None,
        "claims": {"native_trial_complete": False, "stock_source_verified": False},
        "inputs": digests,
        "source": {
            "generator_commit": screen.GENERATOR_COMMIT,
            "seed": screen.SEED,
            "task_checksum": launch["task_checksum"],
            "calendar_sha256": None,
        },
        "notes": [CONTROL_NOTE] if tag != "live" else list(base.NOTES),
    }


Stock = tuple[Row, dict[str, list[int]], int, str]


def staged(
    cell: Path, world: int, arm: str, tag: str
) -> tuple[bytes, Row, Path, Stock | None]:
    entries(cell)
    launch_raw, launch = launched(cell, world, arm, tag)
    name = trial_name(world, arm, tag)
    trial = cell / "trials" / name
    need(
        not present(cell / "trials") or entries(cell / "trials") <= {name},
        "trial_location",
    )
    need(launch["result_present"] == present(trial / "result.json"), "launch_binding")
    if not launch["result_present"]:
        need(launch["error"] is not None, "launch_binding")
        return launch_raw, launch, trial, None
    need(
        None
        not in (launch["task_name"], launch["task_checksum"], launch["config_sha256"]),
        "launch_binding",
    )
    task, starts, target, calendar = stock_task(cell, world, launch)
    cfg = configured(cell, task, world, arm, tag, launch)
    return launch_raw, launch, trial, (cfg, starts, target, calendar)


def audit(cell: Path, world: int, arm: str, tag: str) -> Row:
    launch_raw, launch, trial, stock = staged(cell, world, arm, tag)
    digests: Row = {"launch": packet._digest(launch_raw)}
    row = base_row(world, arm, tag, cell, launch, digests)
    if stock is None:
        return row
    cfg, starts, target, calendar = stock
    digests["config"] = launch["config_sha256"]
    row["claims"]["stock_source_verified"] = True
    row["source"]["calendar_sha256"] = calendar
    trial_row(row, cell, trial, cfg, launch, starts, target)
    return row


def evidence_digests(trial: Path, digests: Row) -> None:
    for key, name in (
        ("messages", "artifacts/logs/world/messages.jsonl"),
        ("events", "artifacts/logs/world/events.jsonl"),
        ("world_state", "verifier/world-state.json"),
    ):
        digests[key] = (
            packet._digest(read(trial / name)) if present(trial / name) else None
        )


def observed(
    row: Row, trial: Path, cfg: Row, launch: Row, starts: dict[str, list[int]]
) -> Row:
    order = protocol.order(row["world"])
    packet._directory(trial)
    result_raw = read(trial / "result.json")
    result = packet._object(packet._json(result_raw))
    row["trial_id"] = native(result, cfg, launch, trial)
    row["inputs"]["native_result"] = packet._digest(result_raw)
    evidence_digests(trial, row["inputs"])
    states, raised = seat_states(result)
    metas = {
        seat: metadata(states[seat], seat, "booker" if seat == order[0] else "reporter")
        for seat in SEATS
    }
    messages = world_messages(trial)
    rounds = exchange(messages, order, starts, row["arm"])
    state = timeline(trial, messages, rounds, order[0])
    plan = prompts(order, starts, rounds)
    for seat in order:
        bind_seat(seat, metas[seat], plan[seat], rounds, messages, state)
    return {
        "order": order,
        "result": result,
        "metas": metas,
        "raised": raised,
        "messages": messages,
        "rounds": rounds,
        "state": state,
        "plan": plan,
    }


def trial_row(
    row: Row,
    cell: Path,
    trial: Path,
    cfg: Row,
    launch: Row,
    starts: dict[str, list[int]],
    target: int,
) -> None:
    seen = observed(row, trial, cfg, launch, starts)
    order, metas, rounds, state = (
        seen["order"],
        seen["metas"],
        seen["rounds"],
        seen["state"],
    )
    owned = owner_turns(cell, order, seen["plan"], metas, launch)
    owner_launch(cell, launch, rounds, metas)
    replies_bound(order, starts, rounds, owned, metas, state["submit"])
    reward = stock_reward(
        trial,
        seen["result"],
        seen["messages"],
        state["submit"],
        row["world"],
        order[0],
        target,
    )
    reason = incomplete_reason(
        launch,
        seen["result"],
        order,
        metas,
        seen["raised"],
        rounds,
        state,
        reward,
        owned,
    )
    finish_row(row, row["tag"], reason, reward, order, rounds, state, owned)


def finish_row(
    row: Row,
    tag: str,
    reason: str | None,
    reward: int | None,
    order: tuple[str, ...],
    rounds: list[Row],
    state: Row,
    owned: dict[str, Row],
) -> None:
    turns = turn_rows(order, owned)
    sessions = [t["session_sha256"] for t in turns if t["session_sha256"] is not None]
    tokens = [t["total_tokens"] for t in turns if t["total_tokens"] is not None]
    reports = [e["report"] for e in rounds if e["report"] is not None]
    complete = reason is None
    row.update(
        complete=complete,
        incomplete_reason=reason,
        native_reward=reward,
        booker_turns=int(owned[order[0]]["status"] != "absent"),
        reporter_turns=sum(owned[seat]["status"] != "absent" for seat in order[1:]),
        model_sessions=len(sessions),
        session_sha256s=sorted(sessions),
        client_tokens=sum(tokens) if tag == "live" else None,
        reporters_asked=[e["seat"] for e in rounds if e["action"] == "report"],
        reporters_skipped=[e["seat"] for e in rounds if e["action"] == "skip"],
        all_reports_exact=all(r["exact"] for r in reports) if reports else None,
        control_as_designed=control_as_designed(tag, complete, reward, rounds),
        turns=turns,
        claims=claims(tag, complete, reward, state, rounds, owned),
    )


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "fanin evidence: invalid_arguments\n")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        prog="python -BS -m examples.seatlab.fanin.evidence",
        description=DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("cell", type=Path)
    parser.add_argument("--world", type=int, required=True, choices=WORLDS)
    parser.add_argument("--arm", required=True, choices=protocol.ARMS)
    parser.add_argument("--tag", required=True, choices=TAGS)
    args = parser.parse_args(argv)
    try:
        cell = args.cell.resolve(strict=True)
        row = audit(cell, args.world, args.arm, args.tag)
        sys.stdout.buffer.write(canonical(row) + b"\n")
    except Bad as exc:
        print(f"fanin evidence: {exc}", file=sys.stderr)
        return 2
    except packet._Invalid:
        print("fanin evidence: invalid_record", file=sys.stderr)
        return 2
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        RecursionError,
    ):
        print("fanin evidence: invalid_or_unreadable_evidence", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
