import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..briefing.protocol import REPETITIONS, SEEDS
from ..handoff.external_audit import _receipts
from ..visibility import deepseek, route_run, spend
from ..visibility.clarify_run import _directory, _is_digest, _private
from ..visibility.omp_seat import _validate_model
from . import protocol
from .packet import _object

_REPORT = {"schema", "transport", "packet_sha256", "rows", "summary"}
_ROW = {"source_id", "seed", "repetition", "arm", "state", "outcome", "turns"}
_TURN = {"stage", "role", "run", "prompt_sha256", "response_sha256", "usage", "status"}
_USAGE = {"served_model", "input_tokens", "cache_tokens", "output_tokens"}
_STAGES = (("push", "inventory"), ("decide", "dispatch"))
_CELLS = tuple(
    (seed, rep, arm)
    for seed in SEEDS
    for rep in REPETITIONS
    for arm in protocol.arms(seed, rep)
)
_LEDGER = {
    "schema",
    "currency",
    "calls",
    "unsettled_calls",
    "known_usd_low",
    "upper_usd_including_holds",
    "price_source",
}
_CALL = {
    "run",
    "role",
    "served_model",
    "cache_hit",
    "cache_miss",
    "output",
    "estimated_usd_low",
    "estimated_usd_high",
    "unsettled_hold_usd",
}
_PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_USD = re.compile(r"(?:0|[1-9][0-9]{0,1})\.[0-9]{9}")
type _Document = dict[str, Any]
type _Rows = dict[tuple[int, int, str], _Document]
type _Identity = tuple[str, str, str]
type _CallKey = tuple[str, str]


def _answer(usage: _Document) -> dict[str, str | int]:
    _, checked = route_run._validate_answer(route_run.Answer("", **usage))
    if checked is None:
        raise ValueError
    return checked


def _turn(raw: object, stage: str, role: str, run: str, transport: str) -> bool:
    turn = _object(raw, *_TURN)
    if (
        (turn["stage"], turn["role"], turn["run"]) != (stage, role, run)
        or not _is_digest(turn["prompt_sha256"])
    ):
        raise ValueError
    status, reply, usage = turn["status"], turn["response_sha256"], turn["usage"]
    if status == "callback_error" and reply is None and usage is None:
        return False
    if status != "received" or not _is_digest(reply):
        raise ValueError
    if transport == "commands":
        if usage is not None:
            raise ValueError
    else:
        _answer(_object(usage, *_USAGE))
    return True


def _row(row: _Document, source_id: str, arm: str, digest: str, transport: str) -> None:
    state, turns, outcome = row["state"], row["turns"], row["outcome"]
    if not isinstance(turns, list) or len(turns) > len(_STAGES):
        raise ValueError
    if state in ("unclaimed", "no_source"):
        if turns or outcome is not None:
            raise ValueError
        return
    if state == "complete":
        if len(turns) != len(_STAGES) or not isinstance(outcome, dict):
            raise ValueError
    elif state != "incomplete" or outcome is not None:
        raise ValueError
    for index, (stage, role) in enumerate(_STAGES[: len(turns)]):
        run = protocol.run_id(digest, source_id, arm, stage)
        received = _turn(turns[index], stage, role, run, transport)
        if not received and (state == "complete" or index + 1 != len(turns)):
            raise ValueError


def _report_rows(report: _Document, transport: str) -> tuple[str, _Rows]:
    try:
        return _validated_rows(report, transport)
    except (TypeError, ValueError):
        raise ValueError("invalid_turn_fork_report") from None


def _validated_rows(report: _Document, transport: str) -> tuple[str, _Rows]:
    if (
        not isinstance(report, dict)
        or report.keys() != _REPORT
        or report["schema"] != "seatlab.turn-fork-report/v1"
        or report["transport"] != transport
        or not _is_digest(report["packet_sha256"])
        or not isinstance(report["summary"], dict)
        or not isinstance(report["rows"], list)
        or len(report["rows"]) != len(_CELLS)
    ):
        raise ValueError
    digest: str = report["packet_sha256"]
    rows: _Rows = {}
    for raw, (seed, rep, arm) in zip(report["rows"], _CELLS, strict=True):
        row = _object(raw, *_ROW)
        source_id = f"s{seed}-r{rep}"
        if (
            row["source_id"] != source_id
            or type(row["seed"]) is not int
            or row["seed"] != seed
            or type(row["repetition"]) is not int
            or row["repetition"] != rep
            or row["arm"] != arm
        ):
            raise ValueError
        _row(row, source_id, arm, digest, transport)
        rows[seed, rep, arm] = row
    for seed in SEEDS:
        for rep in REPETITIONS:
            aligned = rows[seed, rep, "aligned"]
            repeated = rows[seed, rep, "repeat"]
            skipped = aligned["state"] == "no_source"
            if any(
                (rows[seed, rep, arm]["state"] == "no_source") != skipped
                for arm in protocol.arms(seed, rep)
            ):
                raise ValueError
            if (
                aligned["turns"]
                and repeated["turns"]
                and (
                    aligned["turns"][0]["prompt_sha256"]
                    != repeated["turns"][0]["prompt_sha256"]
                )
            ):
                raise ValueError
    return digest, rows


def _service_counts(rows: _Rows) -> tuple[Counter[_Identity], Counter[str]]:
    expected: Counter[_Identity] = Counter()
    errors: Counter[str] = Counter()
    missing: set[tuple[str, str]] = set()
    decide_prompts: dict[tuple[int, int, str], str] = {}
    absent_decisions: set[tuple[int, int, str]] = set()
    for (seed, rep, arm), row in rows.items():
        turns = row["turns"]
        if not turns and row["state"] == "incomplete" and arm in ("aligned", "repeat"):
            sibling = "repeat" if arm == "aligned" else "aligned"
            sibling_turns = rows[seed, rep, sibling]["turns"]
            if sibling_turns:
                missing.add(("inventory", sibling_turns[0]["prompt_sha256"]))
        if turns and turns[0]["status"] == "received":
            source_reply = (seed, rep, turns[0]["response_sha256"])
            if len(turns) == 1:
                absent_decisions.add(source_reply)
            else:
                prompt = turns[1]["prompt_sha256"]
                if decide_prompts.setdefault(source_reply, prompt) != prompt:
                    raise ValueError
        for turn in turns:
            role, prompt = turn["role"], turn["prompt_sha256"]
            if turn["status"] == "received":
                expected[role, prompt, turn["response_sha256"]] += 1
            else:
                errors[role] += 1
                missing.add((role, prompt))
    missing.update(
        ("dispatch", decide_prompts[key])
        for key in absent_decisions
        if key in decide_prompts
    )
    if any((role, prompt) in missing for role, prompt, _ in expected):
        raise ValueError
    return expected, errors


def subscriptions(
    report: _Document, inventory: Path, dispatch: Path, model: str
) -> _Document:
    digest, rows = _report_rows(report, "commands")
    try:
        _validate_model(model)
        if any(
            not path.is_absolute() or ".." in path.parts
            for path in (inventory, dispatch)
        ):
            raise ValueError
        expected, errors = _service_counts(rows)
        with _directory(inventory) as inv_fd, _directory(dispatch) as disp_fd:
            _private(inv_fd, mode=0o700)
            _private(disp_fd, mode=0o700)
            if os.path.samestat(os.fstat(inv_fd), os.fstat(disp_fd)):
                raise ValueError
            roles = {
                "inventory": _receipts(
                    inv_fd, "inventory", model, expected, errors["inventory"]
                ),
                "dispatch": _receipts(
                    disp_fd, "dispatch", model, expected, errors["dispatch"]
                ),
            }
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError("invalid_turn_fork_service_evidence") from None
    return {
        "schema": "seatlab.turn-fork-service/v1",
        "packet_sha256": digest,
        "requested_model": model,
        "verified_served_model": model
        if any(role["matched"] for role in roles.values())
        else None,
        "roles": roles,
    }


def _planned_calls(digest: str, rows: _Rows) -> dict[_CallKey, _Document | None]:
    planned: dict[_CallKey, _Document | None] = {}
    for row in rows.values():
        if row["state"] not in ("incomplete", "complete"):
            continue
        turns = row["turns"]
        for index, (stage, role) in enumerate(_STAGES):
            run = protocol.run_id(digest, row["source_id"], row["arm"], stage)
            turn = turns[index] if index < len(turns) else None
            planned[run, role] = turn
            if turn is None or turn["status"] != "received":
                break
    return planned


def _nano(value: object) -> int:
    if not isinstance(value, str) or _USD.fullmatch(value) is None:
        raise ValueError
    return int(value.replace(".", ""))


def _charge(call: _Document) -> tuple[dict[str, str | int] | None, int, int, int]:
    hit, miss, output = call["cache_hit"], call["cache_miss"], call["output"]
    model, high = call["served_model"], call["estimated_usd_high"]
    hold = _nano(call["unsettled_hold_usd"])
    if high is None:
        if hold != spend._HOLD or any(
            value is not None
            for value in (model, hit, miss, output, call["estimated_usd_low"])
        ):
            raise ValueError
        return None, 0, 0, hold
    low, upper = _nano(call["estimated_usd_low"]), _nano(high)
    if (
        hold != 0
        or not 0 <= low <= upper <= spend._HOLD
        or any(type(n) is not int or n < 0 for n in (hit, miss, output))
    ):
        raise ValueError
    usage = _answer(
        {
            "served_model": deepseek._served_model(model),
            "input_tokens": hit + miss,
            "cache_tokens": hit,
            "output_tokens": output,
        }
    )
    return usage, low, upper, 0


def _reconcile(
    ledger: _Document, prefix: str, planned: dict[_CallKey, _Document | None]
) -> _Document:
    if (
        not isinstance(ledger, dict)
        or ledger.keys() != _LEDGER
        or ledger["schema"] != "seatlab.direct-flash-cost/v1"
        or ledger["currency"] != "USD"
        or ledger["price_source"] != _PRICE_SOURCE
        or not isinstance(ledger["calls"], list)
        or len(ledger["calls"]) > len(planned)
        or type(ledger["unsettled_calls"]) is not int
    ):
        raise ValueError
    received = {
        key
        for key, turn in planned.items()
        if turn is not None and turn["status"] == "received"
    }
    calls = []
    counts = dict.fromkeys(("matched", "ledger_only", "unsettled"), 0)
    low = high = holds = 0
    for call in ledger["calls"]:
        if not isinstance(call, dict) or call.keys() != _CALL:
            raise ValueError
        run, role = call["run"], call["role"]
        key = (run, role)
        if (
            not isinstance(run, str)
            or not run.startswith(prefix)
            or not isinstance(role, str)
            or key not in planned
        ):
            raise ValueError
        turn = planned.pop(key)
        usage, charge_low, charge_high, hold = _charge(call)
        if key in received:
            if turn is None or usage != turn["usage"]:
                raise ValueError
            received.remove(key)
            status = "matched"
        else:
            status = "ledger_only" if usage is not None else "unsettled"
        calls.append({**call, "status": status})
        counts[status] += 1
        low += charge_low
        high += charge_high
        holds += hold
    if received or (
        ledger["unsettled_calls"],
        ledger["known_usd_low"],
        ledger["upper_usd_including_holds"],
    ) != (counts["unsettled"], spend._usd(low), spend._usd(high + holds)):
        raise ValueError
    return {
        "currency": "USD",
        "price_source": _PRICE_SOURCE,
        "calls": calls,
        "matched_calls": counts["matched"],
        "ledger_only_calls": counts["ledger_only"],
        "unsettled_calls": counts["unsettled"],
        "known_usd_low": spend._usd(low),
        "known_usd_high": spend._usd(high),
        "unsettled_hold_usd": spend._usd(holds),
        "upper_usd_including_holds": spend._usd(high + holds),
    }


def direct(report: _Document, ledger: _Document) -> _Document:
    digest, rows = _report_rows(report, "direct")
    prefix = f"tf-{digest[:12]}-"
    try:
        costs = _reconcile(ledger, prefix, _planned_calls(digest, rows))
    except (TypeError, ValueError, deepseek.SeatUnavailable):
        raise ValueError("invalid_turn_fork_ledger") from None
    return {
        "schema": "seatlab.turn-fork-meter/v1",
        "packet_sha256": digest,
        "run_prefix": prefix,
        **costs,
    }
