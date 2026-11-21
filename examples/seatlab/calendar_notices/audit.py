import argparse
import json
import os
import sqlite3
import stat
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from ..live_answers.audit import (
    BindingError,
    _context_usage,
    _native_binding,
    _usage,
    raw,
    require,
)
from ..visibility import (
    calendar_packet,
    calendar_protocol,
    calendar_replica_source,
    deepseek,
    spend,
)
from . import protocol as p
from . import team_source, team_views

SCHEMA = "seatlab.calendar-notice-audit/v1"
PRIVATE_VIEWS_SCHEMA = "seatlab.calendar-private-view-audit/v1"
ROLE_VIEWS_SCHEMA = "seatlab.calendar-role-view-audit/v1"
_REGISTRATION = "seatlab.native-calendar-notices/v1"
_WORLD = {
    "world_version": 2,
    "condition": "talk",
    "seats": list(p.SEATS),
    "limits": {"max_messages": 200, "max_text": 4000},
}
_USAGE = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
Row = dict[str, Any]
Cell = p.Cell | team_views.Cell
Tables = tuple[calendar_packet.Table, calendar_packet.Table]


def _object(data: bytes | str) -> Row:
    return calendar_packet._object(calendar_packet._json(data))


def _read(path: Path) -> Row:
    return _object(raw(path))


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _sealed(path: Path) -> bytes:
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o400
        and info.st_nlink == 1
        and info.st_uid == os.geteuid(),
        "unsealed_registration",
    )
    return raw(path)


def _schema(cell: Cell) -> str:
    if isinstance(cell, team_views.RoleCell):
        return team_views.ROLE_SCHEMA
    return team_views.SCHEMA if isinstance(cell, team_views.Cell) else _REGISTRATION


def _target(tables: Tables) -> int:
    a, b = tables
    shared = set(calendar_packet.starts(a)).intersection(calendar_packet.starts(b))
    require(len(shared) == 1, "stock_shared_slot")
    return shared.pop()


def _participants(cell: Cell, tables: Tables) -> list[Row]:
    private_views = isinstance(cell, team_views.Cell)
    study = team_views if private_views else p
    control_slot = None
    if private_views and cell.control != "live":
        target = _target(tables)
        control_slot = target if cell.control == "oracle" else int(target == 0)
    return [
        {
            "seat": seat,
            "import_path": study.AGENT_IMPORT,
            **({"model_name": study.MODEL} if cell.control == "live" else {}),
            "kwargs": {
                "world": cell.world,
                "arm": cell.arm,
                "seat": seat,
                "control": cell.control,
                **(
                    {"calendar_sha256": p.digest(p.wire(table))}
                    if private_views
                    else {}
                ),
                **({"control_slot": control_slot} if control_slot is not None else {}),
            },
        }
        for seat, table in zip(p.SEATS, tables, strict=True)
    ]


def _attempt(directory: Path, cell: Cell) -> Row:
    attempt = _object(_sealed(directory / "attempt.json"))
    require(
        (
            attempt["schema"],
            attempt["world"],
            attempt["arm"],
            attempt["control"],
            attempt["run"],
        )
        == (_schema(cell), cell.world, cell.arm, cell.control, cell.run),
        "attempt_binding",
    )
    if isinstance(cell, team_views.Cell):
        require(
            attempt.keys()
            == {"schema", "world", "arm", "control", "run", "config_sha256", "source"},
            "attempt_binding",
        )
    return attempt


def _registration(
    directory: Path,
    cell: Cell,
    row: Row,
    tables: Tables | None,
    *,
    attempt: Row | None = None,
) -> tuple[Row, Tables]:
    if attempt is None:
        attempt = _attempt(directory, cell)
    config_data = _sealed(directory / "config.json")
    require(p.digest(config_data) == attempt["config_sha256"], "config_hash")
    config = _object(config_data)
    task = Path(config["task"]["path"])
    if isinstance(cell, team_views.Cell):
        require(task.is_absolute(), "config_binding")
        tables = team_source.tables(task, cell)
    if tables is None:
        raise BindingError("missing_stock_tables")
    trial_root = directory / "trials"
    environment = {
        "type": "docker",
        "force_build": True,
        "delete": True,
        "stream": False,
    }
    require(
        p.wire(config)
        == p.wire(
            {
                "schema_version": 1,
                "trial_name": cell.run,
                "trials_dir": str(trial_root),
                "task": {"path": str(task)},
                "environment": environment,
                "verifier": {"disable": False},
                "participants": _participants(cell, tables),
            }
        )
        and task.is_absolute(),
        "config_binding",
    )
    verified = (
        team_source.identity(task, cell)
        if isinstance(cell, team_views.Cell)
        else calendar_replica_source.verify(task, cell.index)
    )
    source = {
        "task_sha256": verified.native_sha256,
        "semantics_sha256": verified.semantics_sha256,
    }
    require(attempt["source"] == source, "stock_source_binding")
    row["_source"] = source
    return config, tables


def _result_binding(
    directory: Path, cell: Cell, result: Row, config: Row, row: Row
) -> None:
    task = Path(config["task"]["path"])
    trial_root = directory / "trials"
    trial = trial_root / cell.run
    environment = config["environment"]
    source = row["_source"]
    actual = result["config"]
    require(
        all(actual["environment"].get(k) == v for k, v in environment.items())
        and all(
            not actual["environment"].get(k)
            for k in ("env", "mounts", "extra_docker_compose", "import_path", "kwargs")
        )
        and all(not actual["verifier"].get(k) for k in ("env", "import_path", "kwargs"))
        and result["task_id"] == {"path": str(task)}
        and result["trial_uri"] == trial.as_uri()
        and all(entry.name == cell.run for entry in trial_root.iterdir()),
        "native_trial_location_or_config",
    )
    require(
        _native_binding(result, config, cell.task_name, source["task_sha256"])
        == row["trial_uuid"],
        "native_trial_binding",
    )
    if isinstance(cell, team_views.Cell):
        for participant, expected in zip(
            actual["participants"], config["participants"], strict=True
        ):
            require(
                p.wire(participant["kwargs"]) == p.wire(expected["kwargs"])
                and not participant.get("env"),
                "native_roster_binding",
            )


def _timestamp(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value < float("inf")
    )


def _messages(trial: Path, cell: Cell) -> list[Row]:
    root = trial / "artifacts/logs/world"
    messages = [_object(line) for line in raw(root / "messages.jsonl").splitlines()]
    directions = (
        (cell.sender, cell.receiver),
        (cell.receiver, cell.sender),
        (cell.sender, cell.receiver),
    )
    require(len(messages) <= len(directions), "extra_messages")
    for i, (msg, direction) in enumerate(zip(messages, directions), 1):
        require(
            msg.keys() == {"id", "from", "to", "text", "t"}
            and msg["id"] == f"m{i}"
            and (msg["from"], msg["to"]) == direction
            and isinstance(msg["text"], str)
            and 0 < len(msg["text"]) <= 4000
            and _timestamp(msg["t"]),
            "message_scope",
        )
    return messages


def _score(
    trial: Path, cell: Cell, row: Row, messages: list[Row], target: int
) -> Row | None:
    logs = trial / "verifier"
    frozen = _read(logs / "world-state.json")
    submissions = frozen["submissions"]
    require(isinstance(submissions, list) and len(submissions) <= 1, "submission_count")
    require(
        p.wire(frozen)
        == p.wire(
            {
                **_WORLD,
                "frozen": True,
                "messages": messages,
                "submissions": submissions,
                "sent": {
                    seat: sum(msg["from"] == seat for msg in messages)
                    for seat in p.SEATS
                },
            }
        ),
        "frozen_world_binding",
    )
    submit = submissions[0] if submissions else None
    answer = submit["answer"] if submit is not None else None
    if submit is not None:
        require(
            submit.keys() == {"n", "seat", "answer", "t"}
            and type(submit["n"]) is int
            and submit["n"] == 1
            and submit["seat"] == cell.receiver
            and _timestamp(submit["t"])
            and answer in map(calendar_protocol.answer, range(35)),
            "native_submission_shape",
        )
    won = int(answer == calendar_protocol.answer(target))
    file_reward = _read(logs / "reward.json")
    score = _read(logs / "score.json")
    require(
        file_reward.keys() == {"reward"}
        and _binary(file_reward["reward"]) == won
        and row["reward"] == won
        and _binary(score["score"]) == won,
        "stock_reward_mismatch",
    )
    require(
        p.wire({k: v for k, v in score.items() if k != "score"})
        == p.wire(
            {
                "deceived": None,
                "error": None,
                "family": "schedule",
                "instance": cell.index,
                "condition": "talk",
                "seats": list(p.SEATS),
                "world_version": 2,
                "fields_correct": won,
                "fields_total": 1,
                "answer": answer,
                "submitted_by": cell.receiver if submit is not None else None,
                "n_submissions": len(submissions),
                "n_messages": len(messages),
            }
        ),
        "stock_score_binding",
    )
    return submit


def _timeline(
    trial: Path,
    messages: list[Row],
    submit: Row | None,
    *,
    unscored_receiver: str | None = None,
) -> tuple[set[str], dict[str, str] | None]:
    events = raw(trial / "artifacts/logs/world/events.jsonl").splitlines()
    require(events, "missing_world_events")
    identified: set[str] = set()
    delivered: set[str] = set()
    sent, previous = 0, 0
    answer: dict[str, str] | None = None
    for i, line in enumerate(events, 1):
        event = _object(line)
        kind, seat, t, data = (event[k] for k in ("kind", "seat", "t", "data"))
        require(
            event.keys() == {"seq", "kind", "seat", "t", "data"}
            and type(event["seq"]) is int
            and event["seq"] == i
            and _timestamp(t)
            and t >= previous,
            "world_event_sequence",
        )
        if i == 1:
            require((kind, seat, data) == ("start", None, _WORLD), "world_start")
        elif kind == "health":
            require((seat, data) == (None, {}), "world_health")
        elif kind == "whoami":
            require(
                seat in p.SEATS and seat not in identified and data == {},
                "world_identity",
            )
            identified.add(seat)
        else:
            require(seat in identified and answer is None, "world_event_actor")
            if kind == "send":
                require(
                    sent < len(messages) and (sent == 0 or f"m{sent}" in delivered),
                    "send_order",
                )
                msg = messages[sent]
                require(
                    previous <= msg["t"] <= t
                    and seat == msg["from"]
                    and data
                    == {"id": msg["id"], "to": msg["to"], "chars": len(msg["text"])},
                    "send_binding",
                )
                sent += 1
            elif kind == "deliver":
                ids = [msg["id"] for msg in messages[:sent] if msg["to"] == seat]
                require(data == {"ids": ids}, "deliver_binding")
                delivered.update(ids)
            elif kind == "submit":
                if unscored_receiver is None:
                    require(
                        submit is not None
                        and "m3" in delivered
                        and seat == submit["seat"]
                        and previous <= submit["t"] <= t
                        and data == {"n": 1, "answer": submit["answer"]},
                        "submit_event_binding",
                    )
                else:
                    require(
                        "m3" in delivered
                        and seat == unscored_receiver
                        and data.keys() == {"n", "answer"}
                        and type(data["n"]) is int
                        and data["n"] == 1
                        and data["answer"] in map(calendar_protocol.answer, range(35)),
                        "submit_event_binding",
                    )
                answer = data["answer"]
            else:
                raise BindingError("unexpected_world_event")
        previous = t
    require(
        sent == len(messages)
        and (
            unscored_receiver is not None
            or (answer is not None) == (submit is not None)
        ),
        "world_log_binding",
    )
    return delivered, answer


def _actor(cell: Cell, phase: str) -> str:
    return cell.sender if phase in ("initial", "revision") else cell.receiver


def _receipts(trial: Path, cell: Cell) -> dict[str, Row]:
    receipts = {}
    for phase in p.PHASES:
        for seat in p.SEATS:
            path = trial / "seats" / seat / "agent" / f"{phase}.json"
            if _present(path):
                require(seat == _actor(cell, phase), "unexpected_turn_actor")
                receipts[phase] = _read(path)
    return receipts


def _check_turn(
    receipt: Row, row: Row, cell: Cell, phase: str, prompt: str, call: Row | None
) -> Any:
    control = cell.control != "live"
    role = None if control else p.role(cell, phase)
    status = receipt["status"]
    text = receipt["response_text"]
    require(
        (
            receipt["trial_uuid"],
            receipt["run"],
            receipt["seat"],
            receipt["phase"],
            receipt["ledger_role"],
        )
        == (row["trial_uuid"], cell.run, _actor(cell, phase), phase, role)
        and receipt["prompt_sha256"] == p.digest(prompt)
        and status in ("accepted", "model_invalid", "model_failed"),
        "turn_binding",
    )
    usage = receipt["usage"]
    require(
        usage is None or isinstance(usage, list) and len(usage) == 3, "turn_usage_shape"
    )
    _usage(receipt, call, control)
    if status == "model_failed":
        require(
            text is None
            and receipt["response_sha256"] is None
            and receipt["value"] is None,
            "failed_turn_response",
        )
        return None
    require(
        isinstance(text, str) and receipt["response_sha256"] == p.digest(text),
        "turn_response_hash",
    )
    try:
        value = p.decode(text, phase)
    except (ValueError, RecursionError):
        require(
            status == "model_invalid" and receipt["value"] is None, "turn_parse_status"
        )
        return None
    require(
        status == "accepted" and p.wire(value) == p.wire(receipt["value"]),
        "turn_value_binding",
    )
    return value


def _match_message(messages: list[Row], offset: int, expected: str) -> str | None:
    if offset >= len(messages):
        return None
    text = messages[offset]["text"]
    require(text == expected, "message_turn_binding")
    return text


def _turn_path(
    cell, row, receipts, calls, sender, receiver, messages, delivered, target
):
    arm = cell.arm if isinstance(cell, team_views.Cell) else None
    prompts = {
        "initial": p.initial_prompt(sender)
        if arm is None
        else team_views.initial_prompt(sender, arm)
    }
    expects = {
        "initial": list(
            calendar_packet.starts((*sender[:2], (0,) * len(calendar_protocol._DAYS)))
        ),
        "revision": list(calendar_packet.starts(sender)),
        "pick": [0, 1],
        "choice": target if cell.control == "oracle" else int(target == 0),
    }
    receiver_starts = set(calendar_packet.starts(receiver))
    values, bound, candidates = {}, 0, set()
    for phase, receipt in receipts.items():
        require(phase in prompts, "turn_without_scoped_inputs")
        value = _check_turn(
            receipt, row, cell, phase, prompts[phase], calls.get(p.role(cell, phase))
        )
        if value is None:
            row["error"] = receipt["status"]
            continue
        if cell.control != "live":
            require(value == expects[phase], "control_value_binding")
        values[phase] = value
        if phase == "initial":
            row.update(
                initial_report_exact=value == expects[phase],
                joint_slot_in_initial=target in value,
            )
            prompts["revision"] = (
                p.revision_prompt(sender, value)
                if arm is None
                else team_views.revision_prompt(sender, value, arm)
            )
        elif phase == "revision":
            old = values["initial"]
            row.update(
                revision_report_exact=value == expects[phase],
                joint_slot_in_revision=target in value,
            )
            row["changed_days"] = len(p.changed_days(old, value))
            wire_arm = cell.arm if arm is None else "named"
            index_text = _match_message(messages, 0, p.index_wire(old, value, wire_arm))
            bound += index_text is not None
            if index_text is not None and "m1" in delivered:
                index = _object(index_text)
                prompts["pick"] = (
                    p.pick_prompt(receiver, index)
                    if arm is None
                    else team_views.pick_prompt(receiver, index, arm)
                )
        elif phase == "pick":
            request = _match_message(messages, 1, p.request_wire(value))
            bound += request is not None
            if request is None or "m2" not in delivered:
                continue
            old, new = values["initial"], values["revision"]
            report_text = _match_message(messages, 2, p.read_wire(new, value))
            bound += report_text is not None
            if report_text is None or "m3" not in delivered:
                continue
            opened = _object(report_text)
            changed = set(p.changed_days(old, new))
            composite = {
                slot
                for slot in old
                if slot // calendar_protocol._STARTS_PER_DAY not in value
            }
            composite.update(
                slot for report in opened["reports"] for slot in report["starts"]
            )
            candidates = composite & receiver_starts
            row.update(
                opened_changed=len(changed.intersection(value)),
                opened_changed_with_joint_slot=any(
                    report["day"] in changed and target in report["starts"]
                    for report in opened["reports"]
                ),
                final_candidate_count=len(candidates),
                joint_slot_in_final_report=target in composite,
            )
            prompts["choice"] = (
                p.choice_prompt(receiver, old, opened)
                if arm is None
                else team_views.choice_prompt(receiver, old, opened, arm)
            )
        else:
            row.update(
                chosen_locally_fits=value in receiver_starts,
                choice_in_final_candidates=value in candidates,
            )
    require(len(messages) == bound, "message_without_turn")
    return values.get("choice")


def _model_usage(context: Row, turns: list[Row]) -> None:
    expected = {}
    for turn in turns:
        if turn["served_model"] is not None:
            counts = expected.setdefault(turn["served_model"], dict.fromkeys(_USAGE, 0))
            for key, value in zip(_USAGE, turn["usage"], strict=True):
                counts[key] += value
    actual = context.get("model_usage") or {}
    require(
        p.wire(
            {model: {k: usage[k] for k in _USAGE} for model, usage in actual.items()}
        )
        == p.wire(expected),
        "native_model_usage_mismatch",
    )


def _seat_path(result, cell, receipts, tables, messages, delivered):
    private_views = isinstance(cell, team_views.Cell)
    study = team_views if private_views else p
    if private_views and result.get("seat_results") is None:
        require(not receipts and not messages, "missing_native_seat_roster")
        return False
    seats = result["seat_results"]
    require([seat["seat"] for seat in seats] == list(p.SEATS), "native_seat_roster")
    contexts = {}
    complete = True
    for record in seats:
        seat = record["seat"]
        info = record["agent_info"]
        model = (
            {"name": deepseek.MODEL, "provider": "deepseek"}
            if cell.control == "live"
            else None
        )
        require(
            record["under_test"] is True
            and (info["name"], info["version"], info["model_info"])
            == (study.AGENT_NAME, study.AGENT_VERSION, model),
            "native_seat_identity",
        )
        context = record["agent_result"]
        own = [turn for turn in receipts.values() if turn["seat"] == seat]
        require(context is not None or not own, "missing_seat_context")
        if context is None:
            complete = False
            continue
        contexts[seat] = context
        _model_usage(context, own)
        state = context["metadata"]["calendar_notice"]
        execution = record["agent_execution"]
        require(
            (state["run"], state["seat"]) == (cell.run, seat), "seat_metadata_binding"
        )
        calendar_sha256 = p.digest(p.wire(tables[seat]))
        if private_views and (own or state["calendar_sha256"] is not None):
            require(
                state["calendar_sha256"] == calendar_sha256, "seat_calendar_binding"
            )
        if state["stage"] != "complete":
            complete = False
            continue
        require(
            state["error"] is None
            and state["phase"] == ("revision" if seat == cell.sender else "choice")
            and state["calendar_sha256"] == calendar_sha256
            and state["sent"] == [msg for msg in messages if msg["from"] == seat]
            and state["received"]
            == [msg for msg in messages if msg["to"] == seat and msg["id"] in delivered]
            and record["exception_info"] is None
            and execution
            and execution["started_at"]
            and execution["finished_at"],
            "terminal_seat_binding",
        )
    _context_usage(receipts, contexts)
    return complete


def _binary(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value not in (0, 1)
    ):
        return None
    return int(value)


def _transport(
    trial: Path, cell: Cell, row: Row, target: int
) -> tuple[list[Row], set[str], dict[str, str] | None]:
    private_views = isinstance(cell, team_views.Cell)
    world = trial / "artifacts/logs/world"
    messages = (
        _messages(trial, cell)
        if not private_views or _present(world / "messages.jsonl")
        else []
    )
    if not private_views or row["reward"] is not None:
        submit = _score(trial, cell, row, messages, target)
        delivered, answer = _timeline(trial, messages, submit)
        row["reward_verified"] = row["reward_valid"]
        return messages, delivered, answer
    if not _present(world / "events.jsonl"):
        require(not messages, "missing_world_events")
        return messages, set(), None
    delivered, answer = _timeline(
        trial, messages, None, unscored_receiver=cell.receiver
    )
    return messages, delivered, answer


def _inspect(
    trial: Path,
    cell: Cell,
    row: Row,
    result: Row,
    stock_tables: Tables,
    ledger: list[Row],
) -> None:
    tables = dict(zip(p.SEATS, stock_tables, strict=True))
    target = _target(stock_tables)
    messages, delivered, answer = _transport(trial, cell, row, target)
    receipts = _receipts(trial, cell)
    calls = {call["role"]: call for call in ledger}
    require(
        len(calls) == len(ledger)
        and calls.keys() <= {p.role(cell, phase) for phase in receipts}
        if cell.control == "live"
        else not calls,
        "ledger_role_binding",
    )
    private_views = isinstance(cell, team_views.Cell)
    seats_done = (
        _seat_path(result, cell, receipts, tables, messages, delivered)
        if private_views
        else False
    )
    choice = _turn_path(
        cell,
        row,
        receipts,
        calls,
        tables[cell.sender],
        tables[cell.receiver],
        messages,
        delivered,
        target,
    )
    if answer is not None:
        require(
            choice is not None and calendar_protocol.answer(choice) == answer,
            "choice_submission_binding",
        )
    if not private_views:
        seats_done = _seat_path(result, cell, receipts, tables, messages, delivered)
    outcome = _outcome(trial.parent.parent, cell)
    row["complete"] = bool(
        row["reward_verified"]
        and result["started_at"]
        and result["finished_at"]
        and result["exception_info"] is None
        and not result.get("step_results")
        and result.get("agent_result") is None
        and type(outcome["returncode"]) is int
        and outcome["returncode"] == 0
        and outcome["error"] is None
        and seats_done
        and answer is not None
        and len(receipts) == len(p.PHASES)
        and delivered == {"m1", "m2", "m3"}
        and row["error"] is None
    )
    if not row["complete"] and row["error"] is None:
        category = (
            "missing_native_reward"
            if private_views and row["reward"] is None
            else "behavior_incomplete"
        )
        row["error"] = _launch_error(outcome) or category


def _cost(calls: list[Row]) -> Row:
    settled = [call for call in calls if call["estimated_usd_high"] is not None]
    low = sum(Decimal(call["estimated_usd_low"]) for call in settled)
    high = sum(Decimal(call["estimated_usd_high"]) for call in settled)
    hold = sum(Decimal(call["unsettled_hold_usd"]) for call in calls)
    hit, miss, output = (
        sum(call[key] for call in settled)
        for key in ("cache_hit", "cache_miss", "output")
    )
    return {
        "settled_calls": len(settled),
        "held_calls": len(calls) - len(settled),
        "known_usd": f"{low:.9f}",
        "known_usd_high": f"{high:.9f}",
        "upper_usd_including_holds": f"{high + hold:.9f}",
        "usage": {
            "input": hit + miss,
            "cache_hit": hit,
            "cache_miss": miss,
            "output": output,
        },
    }


def _outcome(directory: Path, cell: Cell) -> Row:
    outcome = _object(_sealed(directory / "outcome.json"))
    require(
        (outcome["schema"], outcome["run"]) == (_schema(cell), cell.run),
        "outcome_binding",
    )
    if isinstance(cell, team_views.Cell):
        require(
            outcome.keys() == {"schema", "run", "returncode", "error"},
            "outcome_binding",
        )
    return outcome


def _launch_error(outcome: Row) -> str | None:
    category = outcome["error"]
    if category in (
        None,
        "source_setup_failed",
        "native_config_failed",
        "first_arm_binding_failed",
        "native_launch_failed",
        "native_nonzero_exit",
        "native_timeout",
        "native_interrupted",
    ):
        return category
    return "invalid_launch_outcome"


def _cell(
    job: Path,
    cell: Cell,
    tables: Tables | None,
    ledger: list[Row],
    ledger_error: str | None,
) -> Row:
    directory = job / ("cells" if cell.control == "live" else "controls") / cell.run
    trial = directory / "trials" / cell.run
    row = {
        "run": cell.run,
        "world": cell.world,
        "arm": cell.arm,
        "control": cell.control,
        "attempted": _present(directory),
        "trial_uuid": None,
        "result_sha256": None,
        "reward": None,
        "reward_valid": False,
        "reward_verified": False,
        "complete": False,
        "error": None,
        "changed_days": None,
        "opened_changed": None,
        "opened_changed_with_joint_slot": None,
        "initial_report_exact": None,
        "revision_report_exact": None,
        "joint_slot_in_initial": None,
        "joint_slot_in_revision": None,
        "chosen_locally_fits": None,
        "final_candidate_count": None,
        "joint_slot_in_final_report": None,
        "choice_in_final_candidates": None,
        "_source": None,
        **_cost(ledger),
    }
    if not row["attempted"]:
        row["error"] = "ledger_without_attempt" if ledger else "not_attempted"
        return row
    try:
        if not _present(trial / "result.json"):
            private_views = isinstance(cell, team_views.Cell)
            attempt_path = directory / "attempt.json"
            if private_views and _present(attempt_path):
                attempt = _attempt(directory, cell)
                if attempt["config_sha256"] is not None:
                    _registration(directory, cell, row, tables, attempt=attempt)
            outcome_path = directory / "outcome.json"
            if outcome_path.exists() or (private_views and outcome_path.is_symlink()):
                row["error"] = _launch_error(_outcome(directory, cell))
            row["error"] = row["error"] or "missing_native_result"
            return row
        data = raw(trial / "result.json")
        row["result_sha256"] = p.digest(data)
        result = _object(data)
        identifier = UUID(result["id"])
        require(
            str(identifier) == result["id"] and identifier.version == 4,
            "native_trial_uuid",
        )
        row["trial_uuid"] = str(identifier)
        reward = (result.get("verifier_result") or {}).get("rewards")
        row["reward"] = (
            _binary(reward.get("reward")) if isinstance(reward, dict) else None
        )
        row["reward_valid"] = (
            result.get("reward_valid") is True and row["reward"] is not None
        )
        config, tables = _registration(directory, cell, row, tables)
        _result_binding(directory, cell, result, config, row)
        _inspect(trial, cell, row, result, tables, ledger)
        if ledger_error and cell.control == "live":
            row.update(complete=False, error=ledger_error)
    except BindingError as exc:
        row["error"] = str(exc)
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        RuntimeError,
    ):
        row["error"] = "missing_or_invalid_evidence"
    return row


def _reconcile(rows: list[Row]) -> None:
    ids = Counter(row["trial_uuid"] for row in rows if row["trial_uuid"] is not None)
    sources: dict[str, list[Row]] = {}
    for row in rows:
        if row["_source"] is not None:
            sources.setdefault(row["world"], []).append(row)
        if ids[row["trial_uuid"]] > 1:
            row.update(
                complete=False,
                reward_valid=False,
                reward_verified=False,
                trial_uuid=None,
                discarded_duplicate_reward=row["reward"],
                reward=None,
                error="duplicate_native_trial_uuid",
            )
    for matched in sources.values():
        if any(row["_source"] != matched[0]["_source"] for row in matched[1:]):
            for row in matched:
                row.update(
                    complete=False,
                    reward_verified=False,
                    error="matched_stock_source_mismatch",
                )
    for row in rows:
        row["source"] = row.pop("_source")


def _counts(rows: list[Row]) -> Row:
    successes = sum(row["complete"] and row["reward"] == 1 for row in rows)
    attempted = sum(row["attempted"] for row in rows)
    return {
        "enrolled": len(rows),
        "attempted": attempted,
        "not_attempted": len(rows) - attempted,
        "complete": sum(row["complete"] for row in rows),
        "known_native_rewards": sum(row["reward"] is not None for row in rows),
        "verified_native_rewards": sum(row["reward_verified"] for row in rows),
        "complete_successes": successes,
        "verified_native_losses": sum(
            row["reward_verified"] and row["reward"] == 0 for row in rows
        ),
        "incomplete_attempts": sum(
            row["attempted"] and not row["complete"] for row in rows
        ),
    }


def report(
    job: Path,
    spend_dir: Path,
    *,
    controls: bool = False,
    private_views: bool = False,
    role_views: bool = False,
) -> Row:
    if private_views and role_views:
        raise ValueError("conflicting_view_modes")
    job = job.expanduser().absolute()
    if role_views:
        cell_type, world_ids, arms = (
            team_views.RoleCell,
            team_views.WORLDS,
            team_views.ROLE_ARMS,
        )
        prefix, schema = "crv-", ROLE_VIEWS_SCHEMA
    elif private_views:
        cell_type, world_ids, arms = team_views.Cell, team_views.WORLDS, team_views.ARMS
        prefix, schema = "cpv-", PRIVATE_VIEWS_SCHEMA
    else:
        cell_type, world_ids, arms = p.Cell, p.WORLDS, p.ARMS
        prefix, schema = "cdn-", SCHEMA
    worlds = (
        None
        if private_views or role_views
        else calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)
    )
    control_types = ("oracle", "wrong") if controls else ("live",)
    cells = [
        cell_type(world, arm, control)
        for world in world_ids
        for arm in arms
        for control in control_types
    ]
    if controls:
        cells = [cell for cell in cells if _present(job / "controls" / cell.run)]
    run_ids = {cell.run for cell in cells}
    ledger_error = None
    try:
        calls = spend.report(spend_dir.expanduser().absolute(), run_prefix=prefix)[
            "calls"
        ]
    except (ValueError, OSError, sqlite3.Error):
        calls = []
        ledger_error = "ledger_unavailable"
    if controls:
        selected = [call for call in calls if call["run"] in run_ids]
    else:
        selected = [
            call for call in calls if not call["run"].startswith(f"{prefix}ctl-")
        ]
    rows = [
        _cell(
            job,
            cell,
            worlds[cell.index].tables if worlds is not None else None,
            [call for call in selected if call["run"] == cell.run],
            ledger_error,
        )
        for cell in cells
    ]
    _reconcile(rows)
    return {
        "schema": schema,
        "mode": "controls" if controls else "live",
        "cells": rows,
        "counts": {
            arm: _counts([row for row in rows if row["arm"] == arm]) for arm in arms
        },
        "ledger_error": ledger_error,
        "unassigned_calls": sum(call["run"] not in run_ids for call in selected),
        **_cost(selected),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit first calendar notice attempts offline."
    )
    parser.add_argument("job", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    parser.add_argument("--controls", action="store_true")
    views = parser.add_mutually_exclusive_group()
    views.add_argument("--private-views", action="store_true")
    views.add_argument("--role-views", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            report(
                args.job,
                args.spend_dir,
                controls=args.controls,
                private_views=args.private_views,
                role_views=args.role_views,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
