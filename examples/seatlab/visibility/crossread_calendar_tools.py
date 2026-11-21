from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib import import_module
from itertools import product
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

_WORLDS = range(12)
_DIRECTIONS = _REPETITIONS = range(2)
_ROOMS = ("blind", "peer")
_SEATS = ("p1", "p2")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_ARMS = {"availability": ("off", "on"), "role-access": ("sender", "receiver")}
_SCHEMAS = {
    "seatlab.calendar-local-tools/v1": "availability",
    "seatlab.calendar-role-tools/v1": "role-access",
}
_SCORED = ("complete", "model_invalid_scored_zero")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_USD = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,9})?")
_MAX_JSON_BYTES = 4 * 1024 * 1024

type Obj = dict[str, object]
type Block = tuple[int, int, int]
type Policy = tuple[Block, str]
type Cell = tuple[Block, str, str]


class _Trial(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def result(self) -> Obj: ...

    @property
    def config(self) -> Obj: ...

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def primary_reward(self) -> float | None: ...


type Loader = Callable[[Path], _Trial]


class _Mismatch(ValueError):
    pass


@dataclass
class _Arm:
    world: int
    direction: int
    repetition: int
    policy: str
    room: str
    status: str = "unclaimed"
    reason: str | None = None
    reward: int | None = None
    slot: int | None = None
    trial_id_sha256: str | None = None
    native_seats_with_model_token_usage: int | None = None
    native_seats_with_unknown_model_token_usage: int | None = None


def _stock_loader() -> Loader:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return cast(Loader, import_module("evallab.results").load_trial)
    finally:
        sys.dont_write_bytecode = previous


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise _Mismatch(reason)


def _obj(value: object) -> Obj:
    if not isinstance(value, dict):
        raise _Mismatch("expected_json_object")
    return cast(Obj, value)


def _array(value: object, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise _Mismatch("expected_bounded_json_array")
    return cast(list[object], value)


def _unique(pairs: list[tuple[str, object]]) -> Obj:
    obj = dict(pairs)
    _check(len(obj) == len(pairs), "duplicate_json_key")
    return obj


def _nonfinite(_value: str) -> None:
    raise _Mismatch("nonfinite_json_value")


def _file(path: Path) -> None:
    info = path.lstat()
    _check(
        stat.S_ISREG(info.st_mode) and path.resolve(strict=True) == path,
        "nonphysical_evidence_file",
    )
    _check(info.st_size <= _MAX_JSON_BYTES, "oversized_evidence_file")


def _read(path: Path) -> Obj:
    _file(path)
    with path.open("rb") as stream:
        raw = stream.read(_MAX_JSON_BYTES + 1)
    _check(len(raw) <= _MAX_JSON_BYTES, "oversized_evidence_file")
    return _obj(json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite))


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _Mismatch("invalid_digest")
    return value


def _int(value: object, bounds: range) -> int:
    if type(value) is not int or value not in bounds:
        raise _Mismatch("unregistered_coordinate_or_slot")
    return value


def _block(row: Obj) -> Block:
    return (
        _int(row.get("world"), _WORLDS),
        _int(row.get("direction"), _DIRECTIONS),
        _int(row.get("repetition"), _REPETITIONS),
    )


def _enum(value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _Mismatch("unregistered_policy_or_room")
    return value


def _design(report: Obj) -> str:
    schema = report.get("schema")
    if not isinstance(schema, str) or schema not in _SCHEMAS:
        raise _Mismatch("unsupported_public_report_schema")
    design = _SCHEMAS[schema]
    _check(report.get("design", design) == design, "public_design_schema_mismatch")
    return design


def _index(
    report: Obj, arms: tuple[str, str]
) -> tuple[dict[Policy, Obj], dict[Cell, Obj], Counter[str]]:
    proofs: dict[Policy, Obj] = {}
    rows: dict[Cell, Obj] = {}
    hashes: Counter[str] = Counter()
    for entry in _array(report.get("policies"), 96):
        proof = _obj(entry)
        key = (_block(proof), _enum(proof.get("policy"), arms))
        _check(key not in proofs, "duplicate_public_policy")
        proofs[key] = proof
        if (native := proof.get("native")) is None:
            continue
        room_arms = _obj(_obj(native).get("arms"))
        _check(set(room_arms) <= set(_ROOMS), "unregistered_native_room")
        for room in room_arms.values():
            if (digest := _obj(room).get("trial_id_sha256")) is not None:
                hashes[_digest(digest)] += 1
    for entry in _array(report.get("rows"), 192):
        row = _obj(entry)
        cell = (
            _block(row),
            _enum(row.get("policy"), arms),
            _enum(row.get("room"), _ROOMS),
        )
        _check(cell not in rows, "duplicate_public_row")
        rows[cell] = row
    return proofs, rows, hashes


def _public_arm(proof: Obj | None, room: str) -> tuple[Obj, Obj]:
    if proof is None or proof.get("native") is None:
        return {}, {}
    native = _obj(proof["native"])
    arms = _obj(native.get("arms"))
    return native, _obj(arms.get(room, {}))


def _run(block: Block) -> str:
    world, direction, repetition = block
    return f"rx-w{world:02d}-d{direction}-r{repetition}"


def _load_native(trial: Path, loader: Loader, seen: Counter[str]) -> tuple[_Trial, str]:
    _check(trial.resolve() == trial, "nonphysical_stock_trial")
    for name in ("result.json", "config.json"):
        _file(trial / name)
    for name in ("lock.json", "artifacts/manifest.json"):
        try:
            _file(trial / name)
        except FileNotFoundError:
            pass
    record = loader(trial)
    raw = record.result
    raw_id = raw.get("id")
    if not isinstance(raw_id, str):
        raise _Mismatch("native_trial_uuid")
    uid = UUID(raw_id)
    _check(str(uid) == raw_id == record.id and uid.version == 4, "native_trial_uuid")
    digest = sha256(raw_id.encode()).hexdigest()
    seen[digest] += 1
    _check(
        record.path == trial
        and raw.get("trial_name") == record.name == trial.name
        and raw.get("trial_uri") == trial.as_uri(),
        "native_trial_location",
    )
    return record, digest


def _roster(value: object) -> dict[str, Obj]:
    seats: dict[str, Obj] = {}
    for entry in _array(value, len(_SEATS)):
        row = _obj(entry)
        seat = _enum(row.get("seat"), _SEATS)
        _check(seat not in seats, "duplicate_native_seat")
        seats[seat] = row
    _check(len(seats) == len(_SEATS), "missing_native_seat")
    return seats


def _identity(record: _Trial, root: Path, cell: Cell, native: Obj) -> None:
    block, _, room = cell
    world, direction, repetition = block
    binding = _read(root / "block.json")
    _check(
        binding.get("schema") == "seatlab.calendar-replica/v1"
        and native.get("schema") == "seatlab.calendar-replica-report/v1"
        and _block(binding) == _block(native) == block,
        "native_block_identity",
    )
    raw = record.result
    task = _obj(raw.get("task_id"))
    source = binding.get("source")
    if not isinstance(source, str):
        raise _Mismatch("native_task_location")
    path = Path(source)
    _check(
        path.is_absolute()
        and ".." not in path.parts
        and path.name == f"schedule-{world:03d}-talk"
        and task == {"path": source}
        and raw.get("task_name") == f"flotilla/{path.name}",
        "native_task_location",
    )
    _check(
        _digest(binding.get("native_sha256"))
        == _digest(native.get("native_sha256"))
        == _digest(raw.get("task_checksum"))
        and _digest(binding.get("semantics_sha256"))
        == _digest(native.get("semantics_sha256")),
        "native_task_checksum",
    )
    config = _obj(raw.get("config"))
    _check(
        config.get("trial_name") == record.name
        and config.get("trials_dir") == str(record.path.parent)
        and _obj(config.get("task")).get("path") == source
        and _obj(record.config.get("task")).get("path") == source,
        "native_config_location",
    )
    participants = _roster(config.get("participants"))
    _check(
        participants == _roster(record.config.get("participants")),
        "native_config_roster",
    )
    for seat, participant in participants.items():
        kwargs = _obj(participant.get("kwargs"))
        _check(
            _block(kwargs) == (world, direction, repetition)
            and kwargs.get("seat") == seat
            and kwargs.get("arm") == room
            and kwargs.get("control") is False,
            "native_participant_role",
        )


def _public_award(row: Obj, arm: Obj) -> tuple[int, int | None]:
    status = row.get("native_status")
    reward = _int(row.get("reward"), range(2))
    slot = None if row["slot"] is None else _int(row["slot"], range(35))
    native_slot = None if arm["slot"] is None else _int(arm["slot"], range(35))
    _check(
        (status == "complete" and slot is not None)
        or (status == "model_invalid_scored_zero" and slot is None and reward == 0),
        "public_outcome_shape",
    )
    _check(
        arm.get("status") == status
        and arm.get("native_timeline_verified") is True
        and _int(arm.get("reward"), range(2)) == reward
        and native_slot == slot,
        "public_native_arm_disagreement",
    )
    return reward, slot


def _answer(slot: int | None) -> Obj | None:
    if slot is None:
        return None
    day, hour = divmod(slot, 7)
    return {"day": _DAYS[day], "start": f"{9 + hour:02d}:00"}


def _score(record: _Trial, block: Block, reward: int, slot: int | None) -> int:
    raw = record.result
    _check(
        raw.get("reward_valid") is True
        and raw["exception_info"] is None
        and isinstance(raw.get("finished_at"), str)
        and bool(raw["finished_at"])
        and raw.get("step_results") is None,
        "stock_reward_not_valid",
    )
    scores = _obj(_obj(raw.get("verifier_result")).get("rewards"))
    verifier = record.path / "verifier"
    reward_file = _read(verifier / "reward.json")
    _check(set(scores) == set(reward_file) == {"reward"}, "stock_reward_shape")
    score = _read(verifier / "score.json")
    native_reward = record.primary_reward
    if native_reward is None:
        raise _Mismatch("stock_primary_reward_unavailable")
    verifier_values = (
        scores.get("reward"),
        reward_file.get("reward"),
        score.get("score"),
    )
    _check(
        native_reward == reward
        and all(
            type(value) in (int, float) and value == reward for value in verifier_values
        ),
        "stock_primary_reward_disagreement",
    )
    receiver = _SEATS[1 - block[1]]
    _check(
        score.get("family") == "schedule"
        and score.get("condition") == "talk"
        and _int(score.get("instance"), _WORLDS) == block[0]
        and score["error"] is None
        and score.get("seats") == list(_SEATS)
        and score.get("n_messages") == 2
        and _int(score.get("n_submissions"), range(2)) == int(slot is not None)
        and score["answer"] == _answer(slot)
        and score["submitted_by"] == (receiver if slot is not None else None),
        "stock_score_or_submission_disagreement",
    )
    return int(native_reward)


def _message_ids(
    trial: Path, block: Block, slot: int | None
) -> dict[str, tuple[str, str]]:
    state = _read(trial / "verifier" / "world-state.json")
    sender, receiver = _SEATS[block[1]], _SEATS[1 - block[1]]
    messages = _array(state.get("messages"), 2)
    submissions = _array(state.get("submissions"), 1)
    _check(
        state.get("frozen") is True
        and state.get("condition") == "talk"
        and state.get("seats") == list(_SEATS)
        and len(messages) == 2
        and len(submissions) == int(slot is not None),
        "stock_world_state",
    )
    if submissions:
        submission = _obj(submissions[0])
        _check(
            submission.get("seat") == receiver
            and submission.get("answer") == _answer(slot),
            "stock_world_submission",
        )
    for entry, expected in zip(
        messages, (("m1", receiver, sender), ("m2", sender, receiver)), strict=True
    ):
        row = _obj(entry)
        actual = (row.get("id"), row.get("from"), row.get("to"))
        _check(actual == expected, "stock_message_roles")
    return {sender: ("m1", "m2"), receiver: ("m2", "m1")}


def _turn(root: Path, public: Obj, run: str, role: str, seat: str) -> str:
    expected = _obj(public.get(f"{run}:{role}"))
    local = _read(root / "turns" / f"{run}_{role}.json")
    prompt = _digest(local.get("prompt_sha256"))
    response = local.get("response_text")
    if not isinstance(response, str):
        raise _Mismatch("stock_turn_response_missing")
    response_digest = sha256(response.encode()).hexdigest()
    _check(
        local.get("schema") == "seatlab.relay-turn/v1"
        and local.get("run") == run
        and local.get("role") == role
        and local.get("seat") == seat
        and prompt == _digest(expected.get("prompt_sha256"))
        and response_digest == _digest(local.get("response_sha256"))
        and response_digest == _digest(expected.get("response_sha256")),
        "stock_public_turn_disagreement",
    )
    return prompt


def _token_usage(context: Obj) -> bool:
    def recorded(values: Obj) -> bool:
        keys = ("n_input_tokens", "n_output_tokens", "n_cache_tokens")
        counters = (values.get(key) for key in keys)
        return any(type(value) is int and value >= 0 for value in counters)

    if recorded(context):
        return True
    usage = context.get("model_usage")
    if not isinstance(usage, dict):
        return False
    return any(
        isinstance(item, dict) and recorded(_obj(item)) for item in usage.values()
    )


def _seats(
    record: _Trial, root: Path, block: Block, native: Obj, arm: Obj, slot: int | None
) -> int:
    branch = record.name
    run = _run(block)
    sender, receiver = _SEATS[block[1]], _SEATS[1 - block[1]]
    turns = _obj(native.get("turns"))
    for seat in _SEATS:
        _turn(root, turns, run, f"cal_initial_{seat}", seat)
    sender_prompt = _turn(root, turns, branch, "cal_recheck", sender)
    sender_invalid = f"{branch}:cal_decide" not in turns
    receiver_prompt = None
    if sender_invalid:
        _check(slot is None, "accepted_slot_without_decision")
        decision = root / "turns" / f"{branch}_cal_decide.json"
        _check(
            not decision.exists() and not decision.is_symlink(),
            "unreported_stock_decision",
        )
    else:
        receiver_prompt = _turn(root, turns, branch, "cal_decide", receiver)
    _check(
        arm.get("sender_prompt_sha256") == sender_prompt
        and arm["receiver_prompt_sha256"] == receiver_prompt,
        "public_arm_prompt_disagreement",
    )
    ids = _message_ids(record.path, block, slot)
    seats = _roster(record.result.get("seat_results"))
    usage = 0
    if sender_invalid:
        stages = {sender: "model_invalid", receiver: "peer_model_invalid"}
    else:
        receiver_stage = "model_invalid" if slot is None else "complete"
        stages = {sender: "complete", receiver: receiver_stage}
    prompts = {sender: sender_prompt, receiver: receiver_prompt}
    for seat, row in seats.items():
        _check(
            row.get("under_test") is True and row["exception_info"] is None,
            "stock_seat_not_valid",
        )
        context = _obj(row.get("agent_result"))
        meta = _obj(_obj(context.get("metadata")).get("calendar"))
        incoming, outgoing = ids[seat]
        _check(
            meta.get("schema") == "seatlab.calendar-seat/v1"
            and meta.get("run") == branch
            and meta.get("seat") == seat
            and meta.get("control") is False
            and meta.get("stage") == stages[seat]
            and meta["own_prompt_sha256"] == prompts[seat]
            and meta.get("incoming_id") == incoming
            and meta.get("outgoing_id") == outgoing,
            "stock_seat_or_turn_disagreement",
        )
        usage += int(_token_usage(context))
    return usage


def _inspect(
    study: Path,
    cell: Cell,
    row: Obj | None,
    proof: Obj | None,
    loader: Loader,
    public_ids: Counter[str],
    stock_ids: Counter[str],
) -> _Arm:
    block, policy, room = cell
    output = _Arm(*block, policy, room)
    try:
        run = _run(block)
        root = study / run / policy / "block"
        trial = root / "native" / f"{run}-{room}"
        native, arm = _public_arm(proof, room)
        claimed = (
            arm.get("status") in _SCORED
            or arm.get("reward") is not None
            or (
                row is not None
                and (
                    row.get("native_status") in _SCORED or row.get("reward") is not None
                )
            )
        )
        if not claimed:
            if (trial / "result.json").exists():
                record, _ = _load_native(trial, loader, stock_ids)
                _check(
                    record.result.get("exception_info") is not None
                    or record.primary_reward is None,
                    "unreported_stock_reward",
                )
            if row is None:
                output.status = "missing_public_row"
            elif proof is None:
                output.status = "missing_public_policy"
            else:
                output.reason = _enum(
                    row.get("native_status"),
                    (
                        "not_started",
                        "missing",
                        "missing_evidence",
                        "invalid_evidence",
                        "native_failure",
                        "invalid_seed",
                        "partial",
                    ),
                )
                _check(row["slot"] is None, "unclaimed_public_slot")
            return output
        record, digest = _load_native(trial, loader, stock_ids)
        if row is None:
            output.status = "missing_public_row"
            return output
        _check(
            digest == _digest(arm.get("trial_id_sha256")),
            "public_trial_uuid_disagreement",
        )
        _check(public_ids[digest] == 1, "reused_public_trial_uuid")
        published_reward, slot = _public_award(row, arm)
        _identity(record, root, cell, native)
        native_reward = _score(record, block, published_reward, slot)
        usage = _seats(record, root, block, native, arm, slot)
        output.status = "verified"
        output.reward = native_reward
        output.slot = slot
        output.trial_id_sha256 = digest
        output.native_seats_with_model_token_usage = usage
        output.native_seats_with_unknown_model_token_usage = len(_SEATS) - usage
    except FileNotFoundError:
        output.status = "missing_stock_evidence"
    except _Mismatch as exc:
        output.status, output.reason = "mismatch", str(exc)
    except (
        AttributeError,
        KeyError,
        OSError,
        OverflowError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        output.status, output.reason = "mismatch", "unreadable_stock_or_public_evidence"
    return output


def _counts(rows: list[_Arm]) -> Obj:
    return {
        "registered": len(rows),
        "statuses": dict(Counter(row.status for row in rows)),
        "missing_rewards": sum(row.reward is None for row in rows),
        "verified_reward_0": sum(row.reward == 0 for row in rows),
        "verified_reward_1": sum(row.reward == 1 for row in rows),
        "verified_zero_without_slot": sum(
            row.reward == 0 and row.slot is None for row in rows
        ),
        "native_seats_with_model_token_usage": sum(
            row.native_seats_with_model_token_usage or 0 for row in rows
        ),
        "native_seats_with_unknown_model_token_usage": sum(
            row.native_seats_with_unknown_model_token_usage or 0 for row in rows
        ),
    }


def _host_estimate(value: object) -> Obj | None:
    if not isinstance(value, dict) or value.get("ledger_status") != "available":
        return None
    spend = _obj(value.get("spend"))
    low = spend.get("known_usd_low")
    high = spend.get("upper_usd_including_holds")
    for value in (low, high):
        if not isinstance(value, str) or _USD.fullmatch(value) is None:
            raise _Mismatch("invalid_public_host_estimate")
    _check(spend.get("currency") == "USD", "invalid_public_host_currency")
    return {
        "scope": "whole_input_report_not_filtered",
        "currency": "USD",
        "known_usd_low": low,
        "upper_usd_including_holds": high,
    }


def _collect(
    study: Path,
    report: Obj,
    filters: tuple[int | None, int | None, int | None],
    loader: Loader,
) -> tuple[Obj, bool]:
    design = _design(report)
    policies = _ARMS[design]
    proofs, rows, public_ids = _index(report, policies)
    stock_ids: Counter[str] = Counter()
    out = [
        _inspect(
            study,
            (block, policy, room),
            rows.get((block, policy, room)),
            proofs.get((block, policy)),
            loader,
            public_ids,
            stock_ids,
        )
        for block in product(_WORLDS, _DIRECTIONS, _REPETITIONS)
        if all(
            wanted is None or wanted == part
            for part, wanted in zip(block, filters, strict=True)
        )
        for policy, room in product(policies, _ROOMS)
    ]
    for n, arm in enumerate(out):
        if arm.trial_id_sha256 is not None and stock_ids[arm.trial_id_sha256] > 1:
            out[n] = _Arm(
                arm.world,
                arm.direction,
                arm.repetition,
                arm.policy,
                arm.room,
                status="mismatch",
                reason="reused_stock_trial_uuid",
            )
    result = {
        "schema": "seatlab.calendar-tools-crossread/v1",
        "design": design,
        "read_only": True,
        "scope": (
            "Stock Eval Lab readback for one registered design. "
            "Native usage covers verified seats only. "
            "Host estimates are not native charges or invoices."
        ),
        "aggregate": _counts(out),
        "by_policy": {
            policy: _counts([arm for arm in out if arm.policy == policy])
            for policy in policies
        },
        "host_meter_estimate": _host_estimate(report.get("cost")),
        "arms": [asdict(arm) for arm in out],
    }
    failed = {
        "mismatch",
        "missing_stock_evidence",
        "missing_public_row",
        "missing_public_policy",
    }
    return result, any(arm.status in failed for arm in out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-read a completed SeatLab calendar tools study."
    )
    parser.add_argument("study", type=Path, metavar="ABSOLUTE_STUDY")
    parser.add_argument("report", type=Path, metavar="PUBLIC_REPORT_JSON")
    parser.add_argument("--world", type=int, choices=_WORLDS)
    parser.add_argument("--direction", type=int, choices=_DIRECTIONS)
    parser.add_argument("--repetition", type=int, choices=_REPETITIONS)
    args = parser.parse_args()
    if not args.study.is_absolute():
        parser.error("study directory must be absolute")
    try:
        study = args.study.resolve(strict=True)
        if not study.is_dir():
            parser.error("study must be a directory")
        report = _read(args.report.resolve(strict=True))
        loader = _stock_loader()
        filters = (args.world, args.direction, args.repetition)
        result, failed = _collect(study, report, filters, loader)
    except ImportError:
        parser.error(
            "use a Python environment with the stock Eval Lab reader installed"
        )
    except _Mismatch as exc:
        parser.error(str(exc))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        parser.error("invalid study directory, public report, or stock reader")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
