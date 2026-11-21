import json
from pathlib import Path
from typing import Any
from uuid import UUID

from ..visibility import (
    calendar_packet,
    calendar_protocol,
    calendar_replica_source,
    deepseek,
)
from . import protocol as p

MAX_RECORD_BYTES = 1024 * 1024


class BindingError(ValueError):
    pass


def require(condition: Any, category: str) -> None:
    if not condition:
        raise BindingError(category)


def raw(path: Path) -> bytes:
    require(
        path.resolve(strict=True) == path and path.is_file(), "invalid_artifact_path"
    )
    require(path.stat().st_size <= MAX_RECORD_BYTES, "artifact_too_large")
    return path.read_bytes()


def record(path: Path) -> dict[str, Any]:
    value = json.loads(raw(path))
    require(isinstance(value, dict), "not_a_record")
    return value


def _log(data: bytes) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in data.splitlines()]
    require(all(isinstance(row, dict) for row in rows), "invalid_world_log")
    return rows


def _native_binding(
    result: dict[str, Any], config: dict[str, Any], task_name: str, checksum: str
) -> str:
    identifier = str(UUID(result["id"]))
    actual = result["config"]
    require(
        result["trial_name"] == config["trial_name"]
        and result["task_name"] == f"flotilla/{task_name}"
        and result["task_checksum"] == checksum
        and actual["task"]["path"] == config["task"]["path"]
        and actual["trials_dir"] == config["trials_dir"]
        and actual["environment"]["type"] == "docker"
        and actual["verifier"]["disable"] is False,
        "native_trial_binding",
    )
    expected_roster = config["participants"]
    roster = actual["participants"]
    require(len(roster) == len(expected_roster) == 2, "native_roster_size")
    for actual_agent, expected in zip(roster, expected_roster, strict=True):
        require(
            all(
                actual_agent.get(key) == expected.get(key)
                for key in ("seat", "import_path", "model_name", "kwargs")
            ),
            "native_roster_binding",
        )
    return identifier


def _one_event(
    events: list[dict[str, Any]], kind: str, seat: str, data: dict[str, Any]
) -> dict[str, Any]:
    matches = [
        row
        for row in events
        if (row["kind"], row["seat"], row["data"]) == (kind, seat, data)
    ]
    require(len(matches) == 1, f"invalid_world_{kind}_event")
    return matches[0]


def _message_proof(
    messages: list[dict[str, Any]], events: list[dict[str, Any]], cell: p.Cell
) -> tuple[dict[str, Any], dict[str, Any]]:
    require(len(messages) <= 2, "extra_messages")
    require(
        [row["seq"] for row in events] == list(range(1, len(events) + 1)),
        "event_sequence",
    )
    require(
        sum(row["kind"] == "send" for row in events) == len(messages), "extra_sends"
    )
    out = {}
    actual = {}
    last = 0
    for i, (phase, src, dst) in enumerate(
        (("question", cell.receiver, cell.peer), ("answer", cell.peer, cell.receiver))
    ):
        if i == len(messages):
            break
        message = messages[i]
        mid = f"m{i + 1}"
        require(
            message.keys() == {"id", "from", "to", "text", "t"}
            and (message["id"], message["from"], message["to"]) == (mid, src, dst)
            and isinstance(message["text"], str),
            "message_scope",
        )
        value = p.decode(message["text"], phase)
        canonical = (
            p.question_wire(value) if phase == "question" else p.answer_wire(value)
        )
        require(canonical == message["text"], "message_codec_binding")
        sent = _one_event(
            events, "send", src, {"id": mid, "to": dst, "chars": len(canonical)}
        )
        receives = [
            e for e in events if e["kind"] == "deliver" and mid in e["data"]["ids"]
        ]
        require(
            len(receives) <= 1 and (not receives or receives[0]["seat"] == dst),
            "message_receipt_scope",
        )
        received = receives[0] if receives else None
        require(
            last < sent["seq"] and (received is None or received["seq"] > sent["seq"]),
            "message_order",
        )
        last = received["seq"] if received else sent["seq"]
        if phase == "answer":
            require(
                out["question"]["receive"] is not None,
                "answer_precedes_question_receipt",
            )
        out[phase] = {
            "message": message,
            "send": sent,
            "receive": received,
            "wire_sha256": p.digest(canonical),
        }
        actual[phase] = value
    if cell.arm in p.REPAIR_ARMS or cell.arm in p.QUESTION_ARMS:
        allowed = [[], *([proof["message"]["id"]] for proof in out.values())]
        require(
            all(
                row["data"]["ids"] in allowed
                for row in events
                if row["kind"] == "deliver"
            ),
            "unexpected_world_delivery",
        )
    return out, actual


def _seat_proof(
    seats: list[dict[str, Any]],
    messages: dict[str, Any],
    tables: dict[str, Any],
    cell: p.Cell,
) -> tuple[dict[str, Any], dict[str, Any]]:
    require([s["seat"] for s in seats] == list(p.SEATS), "seat_results_roster")
    states = {}
    contexts = {}
    for seat in seats:
        name = seat["seat"]
        info = seat["agent_info"]
        want_model = (
            None if cell.control else {"name": deepseek.MODEL, "provider": "deepseek"}
        )
        require(
            seat["under_test"] is True
            and (info["name"], info["version"], info["model_info"])
            == (p.AGENT_NAME, p.AGENT_VERSION, want_model),
            "seat_agent_identity",
        )
        context = seat["agent_result"]
        if context is None:
            continue
        state = context["metadata"]["native_calendar_answers"]
        require(
            (state["schema"], state["run"], state["seat"])
            == (p.SEAT_SCHEMA, cell.run, name),
            "seat_metadata_binding",
        )
        phase = "question" if name == cell.receiver else "answer"
        incoming = "answer" if name == cell.receiver else "question"
        sent = messages.get(phase)
        received = messages.get(incoming)
        require(
            state["sent"] == (sent["message"] if sent else None)
            and state["received"]
            == (received["message"] if received and received["receive"] else None),
            "seat_message_binding",
        )
        expected_digest = p.digest(p.wire(tables[name]))
        require(
            state["calendar_sha256"] in (None, expected_digest), "seat_calendar_binding"
        )
        contexts[name] = context
        states[name] = {
            key: state[key] for key in ("stage", "error", "calendar_sha256")
        }
    return states, contexts


def _usage(
    receipt: dict[str, Any], ledger: dict[str, Any] | None, control: bool
) -> None:
    usage = receipt["usage"]
    if usage is None:
        require(receipt["served_model"] is None, "unexpected_model_usage")
        if ledger is not None:
            require(ledger["served_model"] is None, "missing_settled_usage")
        require(control or receipt["status"] == "model_failed", "missing_turn_usage")
        return
    if control or ledger is None:
        raise BindingError("unexpected_control_usage")
    total, cached, output = usage
    require(
        all(type(n) is int and n >= 0 for n in usage)
        and cached <= total
        and (cached, total - cached, output)
        == (ledger["cache_hit"], ledger["cache_miss"], ledger["output"])
        and ledger["served_model"].casefold() in deepseek._ACCEPTED_MODELS
        and (
            receipt["served_model"] == ledger["served_model"]
            or receipt["status"] == "model_failed"
        ),
        "turn_usage_mismatch",
    )


def _turn_proof(
    receipt: dict[str, Any],
    phase: str,
    prompt: str,
    identifier: str,
    cell: p.Cell,
    ledger: dict[str, Any] | None,
) -> None:
    role = p.LEDGER_ROLES[phase]
    status = receipt["status"]
    require(
        (
            receipt["schema"],
            receipt["trial_uuid"],
            receipt["run"],
            receipt["seat"],
            receipt["phase"],
        )
        == (p.TURN_SCHEMA, identifier, cell.run, cell.actor(phase), phase)
        and receipt["ledger_role"] == (None if cell.control else role)
        and receipt["prompt_sha256"] == p.digest(prompt)
        and status in ("accepted", "model_invalid", "model_failed"),
        "turn_binding",
    )
    _usage(receipt, ledger, cell.control)
    text = receipt["response_text"]
    if status == "model_failed":
        require(
            text is None
            and receipt["response_sha256"] is None
            and receipt["value"] is None,
            "failed_turn_has_response",
        )
        return
    require(
        isinstance(text, str) and p.digest(text) == receipt["response_sha256"],
        "turn_response_hash",
    )
    try:
        parsed = p.decode(text, phase)
    except ValueError:
        require(
            status == "model_invalid" and receipt["value"] is None, "wrong_parse_status"
        )
    else:
        require(
            status == "accepted" and p.wire(receipt["value"]) == p.wire(parsed),
            "wrong_turn_value",
        )


def _prompts(
    tables: dict[str, Any], transport: dict[str, Any], cell: p.Cell
) -> dict[str, str]:
    table = tables[cell.receiver]
    prompts = {"question": cell.question_prompt(table)}
    question = transport.get("question")
    answer = transport.get("answer")
    if question and question["receive"]:
        q = question["message"]["text"]
        prompts["answer"] = p.answer_prompt(tables[cell.peer], q)
        if answer and answer["receive"]:
            bits = p.decode(answer["message"]["text"], "answer")
            prompts["choice"] = p.choice_prompt(
                table, q, bits if cell.shows_answer else None
            )
    return prompts


def _question_control_proof(
    phase: str,
    receipt: dict[str, Any],
    tables: dict[str, calendar_packet.Table],
    actual: dict[str, Any],
    cell: p.Cell,
    sender_bits: list[bool] | None,
) -> None:
    if phase == "answer":
        require(sender_bits is not None, "control_draft_binding")
        expected = p.answer_wire([not bit for bit in sender_bits])
    elif phase == "question":
        table = tables[cell.receiver]
        if cell.arm == "local-question":
            starts = calendar_packet.starts(table)
            require(len(starts) >= 2, "control_local_start_count")
            expected = p.question_wire(list(starts[:2]))
        else:
            expected = p.control_reply(phase, table, None, None)
    else:
        expected = p.control_reply(
            phase, tables[cell.receiver], actual["question"], actual["answer"]
        )
    require(
        receipt["status"] == "accepted" and receipt["response_text"] == expected,
        "question_control_binding",
    )


def _turns(directory, identifier, tables, transport, actual, ledger, cell, sender_bits):
    turns = {}
    prompts = _prompts(tables, transport, cell)
    role_calls = {row["role"]: row for row in ledger if row["run"] == cell.run}
    require(role_calls.keys() <= set(p.LEDGER_ROLES.values()), "unexpected_ledger_role")
    for phase in p.PHASES:
        seat = cell.actor(phase)
        path = directory / "seats" / seat / "agent" / f"{phase}.json"
        if not path.exists():
            continue
        data = raw(path)
        receipt = json.loads(data)
        role = p.LEDGER_ROLES[phase]
        _turn_proof(
            receipt, phase, prompts[phase], identifier, cell, role_calls.get(role)
        )
        value = receipt["value"]
        if cell.control and cell.arm in p.QUESTION_ARMS:
            _question_control_proof(phase, receipt, tables, actual, cell, sender_bits)
        elif (
            phase == "answer"
            and value is not None
            and cell.control
            and sender_bits is not None
        ):
            require(value == [not bit for bit in sender_bits], "control_draft_binding")
        if phase in actual:
            expected = (
                sender_bits
                if phase == "answer"
                and (cell.arm == "local-report" or cell.arm in p.QUESTION_ARMS)
                else value
            )
            require(
                receipt["status"] == "accepted" and expected == actual[phase],
                "native_turn_message_mismatch",
            )
        turns[phase] = {
            key: receipt[key]
            for key in (
                "seat",
                "ledger_role",
                "prompt_sha256",
                "response_sha256",
                "served_model",
                "usage",
                "value",
                "status",
                "error",
            )
        } | {"receipt_sha256": p.digest(data), "ledger": role_calls.get(role)}
    return turns


def _context_usage(turns: dict[str, Any], contexts: dict[str, Any]) -> None:
    for seat, context in contexts.items():
        usage = [
            t["usage"]
            for t in turns.values()
            if t["seat"] == seat and t["usage"] is not None
        ]
        expected = (
            [sum(group) for group in zip(*usage, strict=True)]
            if usage
            else [None, None, None]
        )
        require(
            [
                context[k]
                for k in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
            ]
            == expected,
            "native_usage_mismatch",
        )


def _score(trial, result, cell, messages, events, transport, choice, target):
    checked = result.get("verifier_result")
    if checked is None:
        return None
    reward = checked["rewards"]["reward"]
    require(type(reward) in (int, float) and reward in (0, 1), "invalid_native_reward")
    logs = trial / "verifier"
    native = record(logs / "reward.json")
    score = record(logs / "score.json")
    frozen = record(logs / "world-state.json")
    require(
        native["reward"] == score["score"] == reward
        and score["error"] is None
        and frozen["messages"] == messages
        and score["n_messages"] == len(messages),
        "native_verifier_binding",
    )
    submissions = frozen["submissions"]
    submit_events = [row for row in events if row["kind"] == "submit"]
    require(
        len(submissions) == len(submit_events) == score["n_submissions"] <= 1,
        "submission_count",
    )
    if not submissions:
        require(
            score["answer"] is None and score["submitted_by"] is None and reward == 0,
            "empty_submission_reward",
        )
        return {"reward": reward, "submit": None}
    answer = calendar_protocol.answer(choice)
    submission = submissions[0]
    require(
        (submission["n"], submission["seat"], submission["answer"])
        == (1, cell.receiver, answer)
        and score["answer"] == answer
        and score["submitted_by"] == cell.receiver,
        "native_submission_binding",
    )
    if target is not None:
        require(reward == int(choice == target), "stock_booking_reward")
    submit = _one_event(events, "submit", cell.receiver, {"n": 1, "answer": answer})
    require(
        submit["seq"] > transport["answer"]["receive"]["seq"], "premature_submission"
    )
    return {"reward": reward, "submit": submit, "submitted": answer}


def _repair_metrics(row, turns, actual, sender_bits, target) -> None:
    question = turns.get("question", {}).get("value")
    draft = turns.get("answer", {}).get("value")
    row.update(
        target_nominated=(target in question if question is not None else None),
        draft_matches_sender=(draft == sender_bits if draft is not None else None),
        delivered_matches_sender=(
            actual["answer"] == sender_bits if row["answer_received_by_seat"] else None
        ),
    )


def _artifact_hashes(trial: Path, hashes: dict[str, str]) -> None:
    for name in (
        "lock.json",
        "config.json",
        "verifier/reward.json",
        "verifier/score.json",
        "verifier/world-state.json",
    ):
        path = trial / name
        if path.exists():
            hashes[name] = p.digest(raw(path))


def _complete(result, states, turns, receiver_got_answer, score) -> bool:
    return bool(
        result["finished_at"]
        and result["exception_info"] is None
        and result["reward_valid"] is True
        and all(seat["exception_info"] is None for seat in result["seat_results"])
        and all(
            states.get(seat, {}).get("stage") == "complete"
            and states[seat]["error"] is None
            for seat in p.SEATS
        )
        and list(turns) == list(p.PHASES)
        and all(turn["status"] == "accepted" for turn in turns.values())
        and receiver_got_answer
        and score is not None
        and score["submit"] is not None
    )


def _transport(trial, cell, hashes):
    world_dir = trial / "artifacts/logs/world"
    messages_data, events_data = (
        raw(world_dir / f"{n}.jsonl") for n in ("messages", "events")
    )
    hashes.update(messages=p.digest(messages_data), events=p.digest(events_data))
    messages = _log(messages_data)
    events = _log(events_data)
    transport, actual = _message_proof(messages, events, cell)
    return messages, events, transport, actual


def inspect(trial, config, cell, checksum, ledger, row) -> None:
    result_data = raw(trial / "result.json")
    result = json.loads(result_data)
    row["artifact_sha256"]["result"] = p.digest(result_data)
    identifier = _native_binding(result, config, cell.task_name, checksum)
    row["trial_uuid"] = identifier
    result_reward = (
        (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
    )
    if type(result_reward) in (int, float) and result_reward in (0, 1):
        row["native_reward"] = result_reward
    row["native_reward_valid"] = result["reward_valid"] is True
    question_study = cell.arm in p.QUESTION_ARMS
    verified_study = cell.arm in p.REPAIR_ARMS or question_study
    if verified_study:
        task = Path(config["task"]["path"])
        source = calendar_replica_source.verify(task, cell.index)
        require(source.native_sha256 == checksum, "stock_source_binding")
    world = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)[
        cell.index
    ]
    tables = dict(zip(p.SEATS, world.tables, strict=True))
    messages, events, transport, actual = _transport(
        trial, cell, row["artifact_sha256"]
    )
    row["messages"] = (
        {
            phase: proof | {"message": proof["message"] | {"text": None}}
            for phase, proof in transport.items()
        }
        if verified_study
        else transport
    )
    if not verified_study and "question" in actual:
        row["question"] = [
            {"slot": slot, **calendar_protocol.answer(slot)}
            for slot in actual["question"]
        ]
    row["answer"] = actual.get("answer")
    receiver_got_answer = bool(transport.get("answer", {}).get("receive"))
    row["answer_received_by_seat"] = receiver_got_answer
    states, contexts = _seat_proof(result["seat_results"], transport, tables, cell)
    row["seats"] = states
    sender_bits = None
    if verified_study and "question" in actual:
        sender_starts = calendar_packet.starts(tables[cell.peer])
        sender_bits = [slot in sender_starts for slot in actual["question"]]
    target = calendar_packet._shared(world.tables) if verified_study else None
    turns = _turns(
        trial, identifier, tables, transport, actual, ledger, cell, sender_bits
    )
    row["turns"] = (
        {
            phase: turn if phase == "answer" else turn | {"value": None}
            for phase, turn in turns.items()
        }
        if verified_study
        else turns
    )
    _context_usage(turns, contexts)
    for phase in transport:
        require(phase in turns, "world_message_without_turn")
    choice = turns.get("choice", {}).get("value")
    if choice is not None and not verified_study:
        row["choice"] = {"slot": choice, **calendar_protocol.answer(choice)}
    rendered = "choice" in turns
    row["answer_at_final_prompt"] = (
        ("visible" if cell.shows_answer else "withheld") if rendered else "not_rendered"
    )
    row["final_prompt_answers"] = (
        actual["answer"] if rendered and cell.shows_answer else None
    )
    if verified_study:
        _repair_metrics(row, turns, actual, sender_bits, target)
    if question_study:
        for turn in turns.values():
            require(
                states.get(turn["seat"], {}).get("calendar_sha256") is not None,
                "turn_calendar_binding",
            )
        question = turns.get("question", {}).get("value")
        receiver_starts = calendar_packet.starts(tables[cell.receiver])
        row["question_both_locally_valid"] = (
            all(slot in receiver_starts for slot in question)
            if question is not None
            else None
        )
    score = _score(trial, result, cell, messages, events, transport, choice, target)
    _artifact_hashes(trial, row["artifact_sha256"])
    row["submitted"] = (
        score.get("submitted") if score is not None and not verified_study else None
    )
    submit = score["submit"] if score is not None else None
    if verified_study and submit is not None:
        submit = submit | {"data": {"n": submit["data"]["n"]}}
    row["submit_event"] = submit
    row["protocol_bound"] = True
    complete = _complete(result, states, turns, receiver_got_answer, score)
    row["protocol_complete"] = complete
    row["success"] = complete and row["native_reward"] == 1
