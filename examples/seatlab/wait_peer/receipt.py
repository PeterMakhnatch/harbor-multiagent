"""Audit one optional peer-read trial and print one redacted canonical row.

python -BS -m examples.seatlab.wait_peer.receipt TRIAL_DIR --task STOCK_TASK --world N
    --arm {auto,neutral,remind} [--owner OWNER_DIR]

Exit 2 when any record contradicts another. No seat or model program is run.
"""

import argparse
import json
import math
import re
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn, TypeGuard, override
from uuid import UUID

from ..calendar_notices import heldout
from ..visibility import agy_seat, calendar_protocol
from ..visibility import calendar_packet as packet
from . import cohort, protocol

SCHEMA = "seatlab.wait-peer-receipt/v1"
REGISTRATION_SCHEMA = "seatlab.wait-peer-registration/v1"
LAUNCH_SCHEMA = "seatlab.wait-peer-launch/v1"
SEAT_IMPORT = "examples.seatlab.wait_peer.seat:PeerSeat"
AGENT = ("seatlab-wait-peer-seat", "1.0.0", None)
ARMS = ("auto", "neutral", "remind")
TAGS = ("live", "control-ok", "control-wrong")
SEATS = ("p1", "p2")
PHASES = {"sender": ("report",), "receiver": ("open", "book")}
STAGES = {
    "sender": {"report", "sending_ready", "waiting_open", "releasing"},
    "receiver": {
        "waiting_ready",
        "open",
        "sending_open",
        "waiting_release",
        "book",
        "submitting",
    },
}
ERRORS = {
    "invalid_seat_binding",
    "invalid_transport",
    "inbox_timeout",
    "command_unbound",
    "invalid_turn_binding",
    "book_invalid",
    "submit_rejected",
    "cancelled",
    "seat_failed",
    *(
        f"{phase}_{kind}"
        for phase in ("report", "open", "book")
        for kind in ("timeout", "failed", "invalid")
    ),
}
MAX_TEXT = 4000
WORLD = {
    "world_version": 2,
    "condition": "talk",
    "seats": list(SEATS),
    "limits": {"max_messages": 200, "max_text": MAX_TEXT},
}
ANSWERS = [calendar_protocol.answer(slot) for slot in range(35)]
REGISTRATION_KEYS = {
    "schema",
    "world",
    "arm",
    "tag",
    "trial_name",
    "sender",
    "receiver",
    "arm_order",
    "manifest_sha256",
    "instance_range",
    "seed",
    "calendar_sha256",
    "stock_semantics_sha256",
    "task_name",
    "task_path",
    "task_checksum",
    "config_sha256",
}
METADATA_KEYS = {
    "role",
    "phase",
    "error",
    "starts_sha256",
    "turns",
    "actions",
    "messages",
    "receipt",
}
RECEIPT_KEYS = {
    "requested_model",
    "client_model",
    "server_model",
    "session_sha256",
    "answer_sha256",
    "usage",
    "observed_tool_steps",
    "cli_version",
    "prompt_sha256",
    "api_key_passed",
    "ai_credit_fallback",
}
TURN_SCHEMA = "seatlab.seat-turn/v1"
CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
HEX64 = re.compile(r"[0-9a-f]{64}")
NOTES = (
    "The provider service reports no served model; the model name is the client's echo of the request.",
    "The provider service identity is not independently attested.",
    "Token counts are client reports; no cost is inferred from them.",
)
CONTROL_NOTE = "Control turns come from an external command; no model identity applies."
UNATTESTED_NOTE = (
    "No owner records were given; model identity and usage are unavailable."
)
Row = dict[str, Any]
Submit = tuple[float, Row]


class Bad(ValueError):
    pass


def need(condition: object, category: str) -> None:
    if not condition:
        raise Bad(category)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def same(a: object, b: object) -> bool:
    return canonical(a) == canonical(b)


def present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def read(path: Path, *, sealed: bool = False) -> bytes:
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_artifact")
    need(not sealed or stat.S_IMODE(info.st_mode) == 0o444, "unsealed_artifact")
    return packet._read(path)


def record(path: Path) -> Row:
    return packet._object(packet._json(read(path)))


def sealed(path: Path) -> tuple[bytes, Row]:
    data = read(path, sealed=True)
    value = packet._object(packet._json(data))
    need(data == canonical(value), "noncanonical_record")
    return data, value


def lines(data: bytes) -> list[Row]:
    return [packet._object(packet._json(line)) for line in data.splitlines()]


def real(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def moment(value: object) -> bool:
    return real(value) and math.isfinite(value) and value >= 0


def binary(value: object) -> int | None:
    return int(value) if real(value) and value in (0, 1) else None


def slot(value: object) -> bool:
    return type(value) is int and 0 <= value < 35


def known[T](value: T | None, category: str) -> T:
    if value is None:
        raise Bad(category)
    return value


def row_list(value: object, category: str) -> list[Row]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise Bad(category)
    return [packet._object(item) for item in value]


def staged_cohort(
    out: Path, reg: Row
) -> tuple[heldout._Cohort, tuple[tuple[str, str], ...]]:
    manifest = out / "manifest.json"
    need(present(manifest), "manifest_missing")
    digest = reg["manifest_sha256"]
    need(
        isinstance(digest, str)
        and HEX64.fullmatch(digest)
        and packet._digest(read(manifest, sealed=True)) == digest,
        "manifest_binding",
    )
    study, rows = cohort.load(manifest, digest)
    need(
        study.last - study.first + 1 == protocol.COUNT == len(rows),
        "manifest_world_count",
    )
    return study, rows


def registered(
    out: Path, trial: Path, task: Path, world: int, arm: str
) -> tuple[Row, Row, Row, heldout._Cohort]:
    _, reg = sealed(out / "registration.json")
    cfg_raw, cfg = sealed(out / "config.json")
    need(reg.keys() == REGISTRATION_KEYS and reg["tag"] in TAGS, "registration_shape")
    study, rows = staged_cohort(out, reg)
    need(study.first <= world <= study.last, "world_outside_cohort")
    sender, receiver = protocol.role_ids(world)
    name = study.names()[world]
    need(
        same(
            {
                k: reg[k]
                for k in REGISTRATION_KEYS
                - {
                    "manifest_sha256",
                    "calendar_sha256",
                    "stock_semantics_sha256",
                    "task_path",
                    "task_checksum",
                }
            },
            {
                "schema": REGISTRATION_SCHEMA,
                "world": world,
                "arm": arm,
                "tag": reg["tag"],
                "trial_name": f"swr-{world:03d}-{arm}-{reg['tag']}",
                "sender": sender,
                "receiver": receiver,
                "arm_order": list(protocol.ordered_arms(world, study.first)),
                "instance_range": [study.first, study.last],
                "seed": study.stock_limits[0],
                "task_name": name,
                "config_sha256": packet._digest(cfg_raw),
            },
        )
        and reg["trial_name"] == trial.name
        and sorted(reg["arm_order"]) == list(ARMS),
        "registration_binding",
    )
    need(
        isinstance(cfg.get("trials_dir"), str)
        and Path(cfg["trials_dir"]).resolve(strict=True) == trial.parent
        and isinstance(reg["task_path"], str)
        and Path(reg["task_path"]).resolve(strict=True) == task
        and task.name == name,
        "config_location",
    )
    expected = {
        "schema_version": 1,
        "task": {"path": reg["task_path"], "git_url": None, "name": None},
        "trial_name": trial.name,
        "trials_dir": cfg["trials_dir"],
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
    need(cfg_raw == canonical(expected), "config_binding")
    stock = stock_source(task, world, reg, study, rows[world - study.first])
    return reg, cfg, stock, study


def stock_source(
    task: Path, world: int, reg: Row, study: heldout._Cohort, pinned: tuple[str, str]
) -> Row:
    name = study.names()[world]
    root = heldout._check_schedule(task.parent, study)
    source, tables = heldout._checked_stock(
        root / name,
        world,
        name,
        study,
        expected_semantics_sha256=pinned[1],
    )
    calendar = heldout._calendar_digest(tables)
    need(
        (calendar, source.semantics_sha256, source.native_sha256)
        == (reg["calendar_sha256"], reg["stock_semantics_sha256"], reg["task_checksum"])
        and pinned[0] == calendar,
        "stock_source_binding",
    )
    seat_digests = {
        seat: packet._digest(heldout._wire(table).encode("ascii"))
        for seat, table in zip(SEATS, tables)
    }
    need(len(set(seat_digests.values())) == 2, "seat_calendars_not_unique")
    return {
        "tables": dict(zip(SEATS, tables)),
        "target": packet._shared(tables),
        "public": {
            "generator_commit": heldout.GENERATOR_COMMIT,
            "manifest_sha256": reg["manifest_sha256"],
            "manifest_verified": True,
            "calendar_sha256": calendar,
            "stock_semantics_sha256": source.semantics_sha256,
            "task_checksum": source.native_sha256,
            "seat_calendar_sha256": seat_digests,
        },
    }


def launched(out: Path, trial: Path) -> tuple[bytes, Row]:
    data, launch = sealed(out / "launch.json")
    need(
        launch.keys()
        == {"schema", "trial_name", "returncode", "error", "result_present"}
        and launch["schema"] == LAUNCH_SCHEMA
        and launch["trial_name"] == trial.name
        and (launch["returncode"] is None or type(launch["returncode"]) is int)
        and (
            launch["error"] is None
            or isinstance(launch["error"], str)
            and CODE.fullmatch(launch["error"])
        )
        and type(launch["result_present"]) is bool
        and launch["result_present"] == present(trial / "result.json"),
        "launch_binding",
    )
    return data, launch


def native(result: Row, cfg: Row, reg: Row, trial: Path) -> str:
    identifier = UUID(result["id"])
    need(
        str(identifier) == result["id"] and identifier.version == 4, "native_trial_uuid"
    )
    actual = result["config"]
    env = actual["environment"]
    need(
        result["trial_name"] == trial.name
        and result["task_name"] == f"flotilla/{reg['task_name']}"
        and result["task_checksum"] == reg["task_checksum"]
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
    need(isinstance(roster, list) and len(roster) == 2, "native_roster_size")
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


def metadata(meta: Row | None, role: str, starts: list[int]) -> Row | None:
    if meta is None:
        return None
    need(meta.keys() == METADATA_KEYS, "seat_metadata_shape")
    done = meta["phase"] == "done"
    phases, turns, actions = PHASES[role], meta["turns"], meta["actions"]
    allowed_actions = {"report"} if role == "sender" else {"open", "book"}
    need(
        meta["role"] == role or (meta["role"] is None and meta["phase"] == "starting"),
        "seat_role_binding",
    )
    need(
        meta["phase"] in {"done", "starting", "reading_calendar"} | STAGES[role]
        and (not done or meta["error"] is None)
        and meta["error"] in ERRORS | {None},
        "seat_phase",
    )
    need(
        meta["starts_sha256"] in (None, protocol.sha(protocol.wire(starts)))
        and (meta["starts_sha256"] is not None or not done),
        "seat_calendar_binding",
    )
    need(
        isinstance(turns, dict)
        and turns.keys() <= set(phases)
        and all(
            isinstance(t, dict)
            and t.keys() == {"prompt_sha256", "reply_sha256"}
            and all(isinstance(h, str) and HEX64.fullmatch(h) for h in t.values())
            for t in turns.values()
        )
        and isinstance(actions, dict)
        and actions.keys() <= allowed_actions
        and isinstance(meta["messages"], list),
        "seat_metadata_shape",
    )
    need(
        isinstance(actions.get("report", []), list)
        and all(type(v) is int for v in actions.get("report", []))
        and type(actions.get("open", False)) is bool
        and ("book" not in actions or slot(actions["book"])),
        "seat_action_shape",
    )
    if role == "sender":
        need(meta["receipt"] is None, "seat_submit_receipt")
    elif "book" in actions:
        need(same(meta["receipt"], {"accepted": True}), "seat_submit_receipt")
    else:
        need(
            meta["receipt"] is None
            or isinstance(meta["receipt"], dict)
            and meta["receipt"].get("accepted") is not True,
            "seat_submit_receipt",
        )
    need(
        not done or (turns.keys() == set(phases) and actions.keys() == allowed_actions),
        "seat_done_incomplete",
    )
    need(all(p in turns for p in actions if p in phases), "action_without_turn")
    return meta


def world_messages(trial: Path, sender: str, receiver: str) -> list[Row]:
    path = trial / "artifacts/logs/world/messages.jsonl"
    rows = lines(read(path)) if present(path) else []
    directions = ((sender, receiver), (receiver, sender), (sender, receiver))
    need(len(rows) <= 3, "extra_messages")
    for i, (msg, direction) in enumerate(zip(rows, directions), 1):
        need(
            msg.keys() == {"id", "from", "to", "text", "t"}
            and msg["id"] == f"m{i}"
            and (msg["from"], msg["to"]) == direction
            and isinstance(msg["text"], str)
            and 0 < len(msg["text"]) <= MAX_TEXT
            and moment(msg["t"]),
            "message_scope",
        )
    return rows


def timeline(
    trial: Path, messages: list[Row], receiver: str
) -> tuple[dict[str, set[str]], Submit | None]:
    path = trial / "artifacts/logs/world/events.jsonl"
    delivered: dict[str, set[str]] = {seat: set() for seat in SEATS}
    if not present(path):
        need(not messages, "missing_world_events")
        return delivered, None
    events = lines(read(path))
    need(events, "missing_world_events")
    identified: set[str] = set()
    sent, previous = 0, 0
    submit: Submit | None = None
    for i, event in enumerate(events, 1):
        need(
            event.keys() == {"seq", "kind", "seat", "t", "data"}
            and type(event["seq"]) is int
            and event["seq"] == i
            and moment(event["t"])
            and event["t"] >= previous,
            "world_event_sequence",
        )
        kind, seat, t, data = (event[k] for k in ("kind", "seat", "t", "data"))
        if i == 1:
            need(kind == "start" and seat is None and same(data, WORLD), "world_start")
        elif kind == "health":
            need(seat is None and data == {}, "world_health")
        elif kind == "whoami":
            need(
                seat in SEATS and seat not in identified and data == {},
                "world_identity",
            )
            identified.add(seat)
        else:
            need(seat in identified and submit is None, "world_event_actor")
            if kind == "send":
                need(
                    sent < len(messages)
                    and (
                        sent == 0 or f"m{sent}" in delivered[messages[sent - 1]["to"]]
                    ),
                    "send_order",
                )
                msg = messages[sent]
                need(
                    previous <= msg["t"] <= t
                    and seat == msg["from"]
                    and same(
                        data,
                        {"id": msg["id"], "to": msg["to"], "chars": len(msg["text"])},
                    ),
                    "send_binding",
                )
                sent += 1
            elif kind == "deliver":
                ids = [msg["id"] for msg in messages[:sent] if msg["to"] == seat]
                need(same(data, {"ids": ids}), "deliver_binding")
                delivered[seat].update(ids)
            elif kind == "submit":
                need(
                    seat == receiver
                    and "m3" in delivered[receiver]
                    and isinstance(data, dict)
                    and data.keys() == {"n", "answer"}
                    and type(data["n"]) is int
                    and data["n"] == 1
                    and data["answer"] in ANSWERS,
                    "submit_event_binding",
                )
                submit = (t, data["answer"])
            else:
                raise Bad("unexpected_world_event")
        previous = t
    need(sent == len(messages), "world_log_binding")
    return delivered, submit


def stock_reward(
    trial: Path,
    result: Row,
    messages: list[Row],
    submit: Submit | None,
    world: int,
    receiver: str,
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
            and entry["seat"] == receiver
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
                "submitted_by": receiver if answer is not None else None,
                "n_submissions": len(subs),
                "n_messages": len(messages),
            },
        ),
        "stock_score_binding",
    )
    return reward


def wire_object(text: str, keys: set[str]) -> Row:
    value = packet._object(packet._json(text))
    need(value.keys() == keys and text == protocol.wire(value), "message_wire")
    return value


def turn_inputs(
    arm: str,
    starts: dict[str, list[int]],
    roles: tuple[str, str],
    metas: dict[str, Row | None],
    messages: list[Row],
    delivered: dict[str, set[str]],
) -> Row:
    sender, receiver = roles
    sm, rm = metas[sender], metas[receiver]
    prompts = {
        "report": protocol.render("report", starts[sender], arm),
        "open": protocol.render("open", starts[receiver], arm),
        "book": None,
    }
    requested: bool | None = None
    released = report = None
    if messages:
        need(messages[0]["text"] == protocol.wire({"ready": True}), "message_wire")
        need(sm is None or "report" in sm["actions"], "message_without_turn")
    if len(messages) > 1:
        flag = wire_object(messages[1]["text"], {"open"})["open"]
        need(
            type(flag) is bool
            and (rm is None or same(rm["actions"].get("open"), flag)),
            "message_turn_binding",
        )
        requested = bool(flag)
    if len(messages) > 2:
        released = protocol.allowed(arm, known(requested, "message_turn_binding"))
        if released:
            report = wire_object(messages[2]["text"], {"starts"})["starts"]
            need(
                isinstance(report, list) and all(type(v) is int for v in report),
                "message_wire",
            )
        else:
            need(
                messages[2]["text"] == protocol.wire({"opened": False}), "message_wire"
            )
        need(
            sm is None or not released or same(sm["actions"]["report"], report),
            "message_turn_binding",
        )
    received = len(messages) > 2 and "m3" in delivered[receiver]
    if received:
        prompts["book"] = protocol.render("book", starts[receiver], arm, report)
    for seat in roles:
        meta = metas[seat]
        if meta is None:
            continue
        own = meta["messages"]
        need(
            same(own, messages[: len(own)])
            and all(m["from"] == seat or m["id"] in delivered[seat] for m in own),
            "seat_message_binding",
        )
        for phase, turn in meta["turns"].items():
            prompt = prompts[phase]
            need(
                prompt is not None and turn["prompt_sha256"] == protocol.sha(prompt),
                "turn_prompt_binding",
            )
    stated = None if sm is None else sm["actions"].get("report")
    return {
        "prompts": prompts,
        "did_request": requested,
        "released": released,
        "delivered": None if len(messages) < 3 else bool(released and received),
        "report": stated if stated is not None else report,
    }


def owner_turn(owner: Path, phase: str, prompt: str | None, turn: Row | None) -> Row:
    directory = owner / phase
    if not present(directory):
        need(turn is None, "owner_turn_missing")
        return {"status": "absent"}
    packet._directory(directory)
    names = {p.name for p in packet._files(directory)}
    need(
        names in ({"request.json"}, {"request.json", "reply.json", "receipt.json"}),
        "owner_entries",
    )
    prompt = known(prompt, "owner_prompt_binding")
    need(
        read(directory / "request.json")
        == agy_seat.line({"schema": TURN_SCHEMA, "prompt": prompt}),
        "owner_prompt_binding",
    )
    digests = {"request": packet._digest(read(directory / "request.json"))}
    if names == {"request.json"}:
        need(turn is None, "owner_reply_missing")
        return {"status": "unanswered", "digests": digests}
    reply_raw, receipt_raw = (
        read(directory / "reply.json"),
        read(directory / "receipt.json"),
    )
    reply, receipt = (
        packet._object(packet._json(reply_raw)),
        packet._object(packet._json(receipt_raw)),
    )
    text = reply.get("text")
    usage = receipt.get("usage")
    if not isinstance(text, str):
        raise Bad("owner_record_shape")
    need(
        reply.keys() == {"text"}
        and reply_raw == agy_seat.line(reply)
        and receipt.keys() == RECEIPT_KEYS
        and receipt_raw == agy_seat.line(receipt),
        "owner_record_shape",
    )
    need(
        receipt["requested_model"] == receipt["client_model"] == agy_seat.MODEL
        and receipt["server_model"] is None
        and receipt["cli_version"] == agy_seat.VERSION
        and type(receipt["observed_tool_steps"]) is int
        and receipt["observed_tool_steps"] == 0
        and receipt["api_key_passed"] is False
        and receipt["ai_credit_fallback"] is False
        and receipt["prompt_sha256"] == agy_seat.sha(prompt)
        and receipt["answer_sha256"] == agy_seat.sha(text)
        and isinstance(receipt["session_sha256"], str)
        and HEX64.fullmatch(receipt["session_sha256"])
        and isinstance(usage, dict)
        and usage.keys() == agy_seat.USAGE_FIELDS
        and all(type(n) is int and n >= 0 for n in usage.values())
        and usage["total_tokens"] > 0,
        "owner_receipt_binding",
    )
    digests.update(reply=packet._digest(reply_raw), receipt=packet._digest(receipt_raw))
    bound = turn is not None
    need(
        turn is None
        or (
            turn["prompt_sha256"] == protocol.sha(prompt)
            and turn["reply_sha256"] == protocol.sha(text)
        ),
        "owner_turn_binding",
    )
    return {
        "status": "bound" if bound else "answered_unused",
        "text": text,
        "receipt": receipt,
        "digests": digests,
    }


def decoded_matches(phase: str, text: str, meta: Row) -> None:
    try:
        value = protocol.decode(phase, text)
    except (ValueError, TypeError, LookupError, RecursionError):
        need(
            meta["error"] in (f"{phase}_invalid", "book_invalid")
            and phase not in meta["actions"],
            "invalid_reply_accepted",
        )
        return
    action = meta["actions"].get(phase)
    need(
        action is None and meta["phase"] != "done" or same(action, value),
        "reply_action_binding",
    )


def owner_turns(
    owner: Path, inputs: Row, roles: tuple[str, str], metas: dict[str, Row | None]
) -> dict[str, Row]:
    packet._directory(owner)
    need(
        {p.name for p in packet._files(owner)} <= {"report", "open", "book"},
        "owner_entries",
    )
    rows = {}
    for phase in ("report", "open", "book"):
        meta = metas[roles[0] if phase == "report" else roles[1]]
        turn = None if meta is None else meta["turns"].get(phase)
        rows[phase] = owner_turn(owner, phase, inputs["prompts"][phase], turn)
        if rows[phase]["status"] == "bound":
            decoded_matches(
                phase, rows[phase]["text"], known(meta, "owner_turn_binding")
            )
    sessions = [r["receipt"]["session_sha256"] for r in rows.values() if "receipt" in r]
    need(len(set(sessions)) == len(sessions), "shared_provider_session")
    return rows


def turn_rows(
    tag: str,
    roles: tuple[str, str],
    metas: dict[str, Row | None],
    owned: dict[str, Row] | None,
) -> Row:
    rows = {}
    for phase in ("report", "open", "book"):
        meta = metas[roles[0] if phase == "report" else roles[1]]
        turn = None if meta is None else meta["turns"].get(phase)
        row: Row = {
            "prompt_sha256": None if turn is None else turn["prompt_sha256"],
            "reply_sha256": None if turn is None else turn["reply_sha256"],
        }
        if tag != "live":
            row["turn_source"] = "external_command"
        elif owned is None:
            row["turn_source"] = "unattested_command"
        else:
            entry = owned[phase]
            receipt = entry.get("receipt")
            row.update(
                turn_source="owner_subscription_cli",
                owner_status=entry["status"],
                owner_digests=entry.get("digests"),
            )
            if receipt is not None:
                row.update(
                    {
                        k: receipt[k]
                        for k in (
                            "requested_model",
                            "client_model",
                            "server_model",
                            "cli_version",
                            "usage",
                            "observed_tool_steps",
                        )
                    }
                )
        rows[phase] = row
    return rows


def incomplete_reason(
    launch: Row,
    result: Row,
    metas: dict[str, Row | None],
    raised: dict[str, bool],
    roles: tuple[str, str],
    delivered: dict[str, set[str]],
    submit: Submit | None,
    reward: int | None,
    owned: dict[str, Row] | None,
) -> str | None:
    if launch["error"] is not None:
        return launch["error"]
    if launch["returncode"] != 0:
        return "native_nonzero_exit"
    if (
        result.get("exception_info") is not None
        or not result.get("finished_at")
        or result.get("step_results")
        or result.get("agent_result") is not None
    ):
        return "native_trial_exception"
    for role, seat in zip(("sender", "receiver"), roles):
        meta = metas[seat]
        if raised[seat]:
            return f"{role}:seat_exception"
        if meta is None:
            return f"{role}:missing_seat_result"
        if meta["phase"] != "done":
            return f"{role}:{meta['error'] or 'seat_interrupted'}"
    if (
        delivered[roles[0]] != {"m2"}
        or delivered[roles[1]] != {"m1", "m3"}
        or submit is None
    ):
        return "behavior_incomplete"
    if reward is None:
        return "missing_native_reward"
    if owned is not None and any(
        entry["status"] != "bound" for entry in owned.values()
    ):
        return "owner_turn_unbound"
    return None


def claims(
    tag: str,
    complete: bool,
    reward: int | None,
    inputs: Row,
    booked: int | None,
    target: int,
    turns: Row,
    owned: dict[str, Row] | None,
) -> Row:
    bound = owned is not None and all(
        entry["status"] == "bound" for entry in owned.values()
    )
    receipts = [
        entry["receipt"] for entry in (owned or {}).values() if "receipt" in entry
    ]
    result = {
        "native_trial_complete": complete,
        "stock_source_verified": True,
        "stock_reward_verified": reward is not None,
        "all_prompts_reconstructed": all(
            t["prompt_sha256"] is not None for t in turns.values()
        ),
        "report_delivered_before_book": inputs["delivered"] is True
        and booked is not None,
        "owner_answers_bound": bound,
        "separate_provider_sessions": bound
        and len({r["session_sha256"] for r in receipts}) == 3,
        "client_echoed_requested_model": bound,
        "served_model_reported": False,
        "zero_tool_steps_reported": bound,
    }
    if tag != "live":
        result["control_as_designed"] = (
            complete
            and booked is not None
            and (
                (booked == target and reward == 1)
                if tag == "control-ok"
                else (booked != target and reward == 0)
            )
        )
    return result


def outcome(
    inputs: Row,
    starts: dict[str, list[int]],
    roles: tuple[str, str],
    booked: int | None,
    target: int,
) -> Row:
    report = inputs["report"]
    return {
        "did_request": inputs["did_request"],
        "released": inputs["released"],
        "delivered": inputs["delivered"],
        "booked_slot": booked,
        "booked_correct": None if booked is None else booked == target,
        "booked_fits_receiver": None if booked is None else booked in starts[roles[1]],
        "booked_fits_sender": None if booked is None else booked in starts[roles[0]],
        "report_size": None if report is None else len(report),
        "report_exact": None if report is None else report == starts[roles[0]],
        "report_has_joint_slot": None if report is None else target in report,
    }


def audit(trial: Path, task: Path, world: int, arm: str, owner: Path | None) -> Row:
    out = trial.parent.parent
    need(trial.parent.name == "trials", "trial_location")
    packet._directory(trial)
    reg, cfg, stock, study = registered(out, trial, task, world, arm)
    tag, roles = reg["tag"], (reg["sender"], reg["receiver"])
    need(owner is None or tag == "live", "owner_records_for_control")
    need(present(out / "launch.json"), "launch_record_missing")
    launch_raw, launch = launched(out, trial)
    digests = {
        "registration": packet._digest(read(out / "registration.json")),
        "config": reg["config_sha256"],
        "launch": packet._digest(launch_raw),
        "manifest": stock["public"]["manifest_sha256"],
    }
    row: Row = {
        "schema": SCHEMA,
        "trial_uuid": None,
        "world": world,
        "arm": arm,
        "tag": tag,
        "arm_position": list(protocol.ordered_arms(world, study.first)).index(arm),
        "sender": roles[0],
        "receiver": roles[1],
        "source": stock["public"],
        "complete": False,
        "incomplete_reason": launch["error"] or "missing_native_result",
        "native_reward": None,
        **dict.fromkeys(
            (
                "did_request",
                "released",
                "delivered",
                "booked_slot",
                "booked_correct",
                "booked_fits_receiver",
                "booked_fits_sender",
                "report_size",
                "report_exact",
                "report_has_joint_slot",
                "seat_starts_sha256",
                "turns",
            )
        ),
        "inputs": digests,
        "claims": {"native_trial_complete": False, "stock_source_verified": True},
        "notes": [],
    }
    if launch["result_present"]:
        trial_row(row, trial, cfg, reg, stock, launch, owner)
    return row


def trial_row(
    row: Row,
    trial: Path,
    cfg: Row,
    reg: Row,
    stock: Row,
    launch: Row,
    owner: Path | None,
) -> None:
    world, arm, tag, roles = (
        reg["world"],
        reg["arm"],
        reg["tag"],
        (reg["sender"], reg["receiver"]),
    )
    digests = row["inputs"]
    result_raw = read(trial / "result.json")
    result = packet._object(packet._json(result_raw))
    row["trial_uuid"] = native(result, cfg, reg, trial)
    digests["native_result"] = packet._digest(result_raw)
    starts = {seat: list(packet.starts(stock["tables"][seat])) for seat in SEATS}
    states, raised = seat_states(result)
    metas = {
        seat: metadata(
            states[seat], "sender" if seat == roles[0] else "receiver", starts[seat]
        )
        for seat in SEATS
    }
    messages = world_messages(trial, *roles)
    delivered, submit = timeline(trial, messages, roles[1])
    for key, name in (
        ("messages", "artifacts/logs/world/messages.jsonl"),
        ("events", "artifacts/logs/world/events.jsonl"),
        ("world_state", "verifier/world-state.json"),
        ("reward", "verifier/reward.json"),
        ("score", "verifier/score.json"),
    ):
        digests[key] = (
            packet._digest(read(trial / name)) if present(trial / name) else None
        )
    inputs = turn_inputs(arm, starts, roles, metas, messages, delivered)
    receiver_meta = metas[roles[1]]
    booked = None if receiver_meta is None else receiver_meta["actions"].get("book")
    need(submit is not None or booked is None, "choice_submission_binding")
    if submit is not None:
        if booked is None:
            need(
                receiver_meta is None or receiver_meta["phase"] != "done",
                "choice_submission_binding",
            )
            booked = ANSWERS.index(submit[1])
        need(
            same(submit[1], calendar_protocol.answer(booked)),
            "choice_submission_binding",
        )
    reward = stock_reward(
        trial, result, messages, submit, world, roles[1], stock["target"]
    )
    owned = None if owner is None else owner_turns(owner, inputs, roles, metas)
    turns = turn_rows(tag, roles, metas, owned)
    reason = incomplete_reason(
        launch, result, metas, raised, roles, delivered, submit, reward, owned
    )
    row.update(
        complete=reason is None,
        incomplete_reason=reason,
        native_reward=reward,
        **outcome(inputs, starts, roles, booked, stock["target"]),
        seat_starts_sha256={
            seat: protocol.sha(protocol.wire(starts[seat])) for seat in SEATS
        },
        turns=turns,
        claims=claims(
            tag, reason is None, reward, inputs, booked, stock["target"], turns, owned
        ),
        notes=[CONTROL_NOTE]
        if tag != "live"
        else list(NOTES)
        if owner is not None
        else [UNATTESTED_NOTE],
    )


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "wait-peer receipt: invalid arguments; use --help\n")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        prog="python -BS -m examples.seatlab.wait_peer.receipt",
        description=__doc__,
        allow_abbrev=False,
    )
    parser.add_argument("trial", type=Path)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--owner", type=Path)
    args = parser.parse_args(argv)
    try:
        trial = args.trial.resolve(strict=True)
        task = args.task.resolve(strict=True)
        owner = None if args.owner is None else args.owner.resolve(strict=True)
        row = audit(trial, task, args.world, args.arm, owner)
        sys.stdout.buffer.write(canonical(row) + b"\n")
    except Bad as exc:
        print(f"wait-peer receipt: {exc}", file=sys.stderr)
        return 2
    except packet._Invalid as exc:
        print(f"wait-peer receipt: stock: {exc}", file=sys.stderr)
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
        print("wait-peer receipt: invalid_or_unreadable_evidence", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
