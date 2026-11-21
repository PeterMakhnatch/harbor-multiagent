import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..attention import protocol as attention
from ..handoff.external_audit import Identity, _receipts
from ..turn_fork import attribution, protocol
from ..turn_fork.packet import _digest, _object
from ..visibility import deepseek
from ..visibility.clarify_run import _directory, _encode, _private
from ..visibility.omp_seat import _validate_model
from . import inputs

_REPORT = ("schema", "board_sha256", "transport", "rows")
_DRAW = ("index", "source_id", "obs_id", "prompt_sha256", "draw")
_TURN = ("status", "prompt_sha256", "response_sha256", "usage")
_OUTCOME = ("opened", "actions", "optimal", "feasible", "opened_sensitive")
_ROLE = "dispatch"
type _Document = dict[str, Any]
type _Observation = tuple[_Document, tuple[str, str]]


def _observations(packet: _Document, board: _Document) -> dict[str, _Observation]:
    sources = {source["source_id"]: source for source in packet["sources"]}
    result: dict[str, _Observation] = {}
    for raw in board["observations"]:
        obs = _object(raw, "source_id", "obs_id", "prompt_sha256", "opened")
        source = sources[obs["source_id"]]
        if not isinstance(obs["opened"], list):
            raise ValueError
        opened = attention._open_pair(obs["opened"])
        prompt = protocol.decide_prompt(source, opened)
        commitment = sha256(prompt.encode("utf-8")).hexdigest()
        obs_id = f"{source['source_id']}-{commitment[:12]}"
        if (
            obs["prompt_sha256"] != commitment
            or obs["obs_id"] != obs_id
            or obs_id in result
        ):
            raise ValueError
        result[obs_id] = source, opened
    return result


def _grade(raw: object, observation: _Observation) -> None:
    source, opened = observation
    outcome = _object(raw, *_OUTCOME)
    actions = outcome["actions"]
    if not isinstance(actions, list) or len(actions) != attention.EPISODES:
        raise ValueError
    choices: dict[int, str] = {}
    for raw_action in actions:
        action = _object(raw_action, "id", "choice", "optimal", "feasible")
        case_id, choice = action["id"], action["choice"]
        if (
            type(case_id) is not int
            or type(choice) is not str
            or type(action["optimal"]) is not bool
            or type(action["feasible"]) is not bool
        ):
            raise ValueError
        choices[case_id] = choice
    aliases = attention.aliases(source["cases"], source["seed"], source["repetition"])
    if choices.keys() != set(aliases.values()):
        raise ValueError
    text = json.dumps(
        {"choices": {alias: choices[case] for alias, case in aliases.items()}}
    )
    expected = protocol.grade(source, opened, text)
    if (
        any(type(outcome[key]) is not int for key in _OUTCOME[2:])
        or outcome != expected
    ):
        raise ValueError


def _received(raw: object, prompt: str, transport: str) -> bool:
    turn = _object(raw, *_TURN)
    if turn["prompt_sha256"] != prompt:
        raise ValueError
    status, response, usage = turn["status"], turn["response_sha256"], turn["usage"]
    if status == "callback_error" and response is None and usage is None:
        return False
    if status != "received":
        raise ValueError
    _digest(response)
    if transport == "commands":
        if usage is not None:
            raise ValueError
    else:
        attribution._answer(_object(usage, *attribution._USAGE))
    return True


def _verify_row(
    raw: object,
    draw: _Document,
    observation: _Observation,
    digest: str,
    transport: str,
) -> None:
    row = _object(raw, *_DRAW, "run", "state", "turn", "outcome")
    if (
        any(row[key] != draw[key] for key in _DRAW)
        or type(row["index"]) is not int
        or type(row["draw"]) is not int
        or row["run"] != inputs.run_id(digest, draw["obs_id"], draw["draw"])
    ):
        raise ValueError
    state, turn, outcome = row["state"], row["turn"], row["outcome"]
    if state == "unclaimed" and turn is None and outcome is None:
        return
    if state not in ("incomplete", "complete"):
        raise ValueError
    received = turn is not None and _received(turn, draw["prompt_sha256"], transport)
    if state == "complete" and received:
        _grade(outcome, observation)
    elif state != "incomplete" or outcome is not None:
        raise ValueError


def verify(
    packet: _Document, board: _Document, digest: str, report: _Document
) -> _Document:
    try:
        checked = _object(report, *_REPORT)
        transport, rows = checked["transport"], checked["rows"]
        if (
            checked["schema"] != "seatlab.decision-cross-report/v1"
            or checked["board_sha256"] != digest
            or sha256(_encode(board)).hexdigest() != digest
            or transport not in ("commands", "direct")
            or not isinstance(rows, list)
        ):
            raise ValueError
        observations = _observations(packet, board)
        for raw, draw in zip(rows, inputs.draws(board), strict=True):
            _verify_row(raw, draw, observations[draw["obs_id"]], digest, transport)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid_decision_cross_report") from None
    return report


def service(report: _Document, receipts: Path, model: str) -> _Document:
    try:
        if (
            report["transport"] != "commands"
            or not receipts.is_absolute()
            or ".." in receipts.parts
        ):
            raise ValueError
        _validate_model(model)
        expected: Counter[Identity] = Counter()
        errors = 0
        for row in report["rows"]:
            turn = row["turn"]
            if turn is None:
                continue
            if turn["status"] == "received":
                expected[_ROLE, turn["prompt_sha256"], turn["response_sha256"]] += 1
            else:
                errors += 1
        with _directory(receipts) as fd:
            _private(fd, mode=0o700)
            dispatch = _receipts(fd, _ROLE, model, expected, errors)
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid_decision_cross_service_evidence") from None
    return {
        "schema": "seatlab.decision-cross-service/v1",
        "board_sha256": report["board_sha256"],
        "requested_model": model,
        "verified_served_model": model if dispatch["matched"] else None,
        "roles": {_ROLE: dispatch},
    }


def meter(report: _Document, ledger: _Document) -> _Document:
    try:
        if report["transport"] != "direct":
            raise ValueError
        digest = report["board_sha256"]
        prefix = f"dx-{digest[:12]}-"
        planned = {
            (row["run"], _ROLE): row["turn"]
            for row in report["rows"]
            if row["state"] != "unclaimed"
        }
        costs = attribution._reconcile(ledger, prefix, planned)
    except (KeyError, TypeError, ValueError, deepseek.SeatUnavailable):
        raise ValueError("invalid_decision_cross_ledger") from None
    return {
        "schema": "seatlab.decision-cross-meter/v1",
        "board_sha256": digest,
        "run_prefix": prefix,
        **costs,
    }
