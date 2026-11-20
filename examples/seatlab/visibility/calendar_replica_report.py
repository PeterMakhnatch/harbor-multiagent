"""Replay one public Calendar Feedback replica block without trusting adapter summaries."""

from __future__ import annotations

import argparse
import json
import stat
from datetime import datetime
from pathlib import Path
from uuid import UUID

from harbor.models.trial.config import TrialConfig

from . import calendar_packet as packet
from . import calendar_protocol as protocol
from . import calendar_replica_source as source_identity
from . import calendar_seat as actor
from . import replica_io as relay
from .calendar_driver import _trial_config
from .calendar_report import (
    _ARMS,
    _BAD_EVIDENCE,
    _ROLES,
    _SEATS,
    _by_seat,
    _bytes,
    _check,
    _EvidenceError,
    _failure,
    _json,
    _object,
    _read,
    _Row,
    _same,
    _sha,
    _timeline,
    _verify_reward,
)

_IMPORT = "examples.seatlab.visibility.calendar_replica_seat:RelayCalendarSeat"
_IDENTITY = {
    "name": "seatlab-calendar-external",
    "version": "1.0.0",
    "model_info": None,
}
_SCORED = {"complete", "model_invalid_scored_zero"}
_REPLAY_ERRORS = (*_BAD_EVIDENCE, RuntimeError)


def _arm(status: str, *, trial_digest: str | None = None) -> _Row:
    return {
        "status": status,
        "reward": None,
        "slot": None,
        "sender_prompt_sha256": None,
        "receiver_prompt_sha256": None,
        "trial_id_sha256": trial_digest,
        "native_timeline_verified": False,
        "usage": dict.fromkeys(_SEATS),
    }


def _report() -> _Row:
    return {
        "schema": "seatlab.calendar-replica-report/v1",
        "world": None,
        "direction": None,
        "repetition": None,
        "native_sha256": None,
        "semantics_sha256": None,
        "status": "not_started",
        "arms": {arm: _arm("not_started") for arm in _ARMS},
        "seeds": {seat: {"status": "not_started"} for seat in _SEATS},
        "equal_final_prompt": None,
        "equal_choice": None,
        "counted_turns": 0,
    }


def _entries(path: Path, allowed: set[str]) -> set[str]:
    _check(path.resolve() == path, "nonphysical_directory")
    _check(stat.S_ISDIR(path.lstat().st_mode), "not_directory")
    names = {entry.name for entry in path.iterdir()}
    _check(names <= allowed, "unregistered_entries")
    return names


def _turn(
    root: Path, run: str, role: str, seat: str, prompt: str, turns: dict[str, _Row]
) -> str:
    path = root / f"{run}_{role}.json"
    row = _object(_bytes(path, relay.MAX_FRAME_BYTES))
    text = row.get("response_text")
    if not isinstance(text, str):
        raise _EvidenceError("response_shape")
    prompt_hash = _sha(prompt.encode())
    raw = text.encode()
    _check(len(raw) <= relay.MAX_TEXT_BYTES, "oversized_relay_response")
    response_hash = _sha(raw)
    _same(
        row,
        {
            "schema": relay.TURN_SCHEMA,
            "run": run,
            "role": role,
            "seat": seat,
            "prompt_sha256": prompt_hash,
            "response_sha256": response_hash,
            "response_text": text,
        },
        "relay_turn_binding",
    )
    turns[f"{run}:{role}"] = {
        "prompt_sha256": prompt_hash,
        "response_sha256": response_hash,
    }
    return text


def _native(
    trial: Path,
    native: _Row,
    source: Path,
    checksum: str,
    block: protocol.Block,
    arm: str,
    seeds: tuple[tuple[int, ...], tuple[int, ...]],
) -> tuple[dict[str, _Row], tuple[datetime, datetime]]:
    config = _trial_config(
        trial.parent.parent, source, block, arm, seeds, control=False
    )
    config.update(trial_name=trial.name, trials_dir=str(trial.parent))
    for participant in config["participants"]:
        participant.update(import_path=_IMPORT, model_name=None)
    expected = TrialConfig.model_validate(config).model_dump(mode="json")
    _same(native["config"], expected, "native_trial_policy")
    _same(
        _read(trial / "config.json"),
        {key: value for key, value in expected.items() if key != "agent"},
        "native_config_copy",
    )
    _same(
        {
            key: native[key]
            for key in (
                "schema_version",
                "task_checksum",
                "trial_name",
                "trial_uri",
                "task_id",
                "task_name",
                "source",
                "verifier_environment_mode",
                "agent_info",
            )
        },
        {
            "schema_version": 1,
            "task_checksum": checksum,
            "trial_name": trial.name,
            "trial_uri": trial.as_uri(),
            "task_id": {"path": str(source)},
            "task_name": f"flotilla/schedule-{block.world:03d}-talk",
            "source": None,
            "verifier_environment_mode": "shared",
            "agent_info": _IDENTITY,
        },
        "native_identity",
    )
    if native["exception_info"] is not None:
        raise _EvidenceError("native_trial_exception", "native_failure")
    if native["finished_at"] is None:
        raise _EvidenceError("native_unfinished", "missing_evidence")
    start = datetime.fromisoformat(native["started_at"])
    finish = datetime.fromisoformat(native["finished_at"])
    _check(
        start.utcoffset() is not None
        and finish.utcoffset() is not None
        and start <= finish,
        "native_time",
    )
    _same(native.get("step_results"), None, "unexpected_native_steps")
    _same(native.get("agent_result"), None, "unexpected_native_context")
    _check(
        _entries(trial / "seats", set(_SEATS)) == set(_SEATS), "native_seat_directories"
    )
    contexts = {}
    for seat, row in _by_seat(native["seat_results"]).items():
        _check(row["under_test"] is True, "native_roster")
        _same(row["agent_info"], _IDENTITY, "native_agent_identity")
        if row["exception_info"] is not None:
            raise _EvidenceError("native_seat_exception", "native_failure")
        _entries(trial / "seats" / seat, {"agent"})
        _entries(trial / "seats" / seat / "agent", set())
        context = row["agent_result"]
        if context is None:
            raise _EvidenceError("native_context_absent", "missing_evidence")
        _check(isinstance(context, dict), "native_context_shape")
        for key in (
            "n_input_tokens",
            "n_cache_tokens",
            "n_output_tokens",
            "cost_usd",
            "model_usage",
            "rollout_details",
        ):
            _check(context.get(key) is None, "unattested_native_usage")
        contexts[seat] = context
    return contexts, (start, finish)


def _actions(
    turn_root: Path,
    run: str,
    files: set[str],
    block: protocol.Block,
    tables: tuple[packet.Table, packet.Table],
    seeds: tuple[tuple[int, ...], tuple[int, ...]],
    peer: bool,
    turns: dict[str, _Row],
) -> tuple[str, str | None, tuple[str, str], int | None, bool]:
    sender, receiver = block.sender, block.receiver
    sender_idx, receiver_idx = block.direction, 1 - block.direction
    feedback = seeds[receiver_idx] if peer else None
    recheck = protocol.recheck_prompt(tables[sender_idx], seeds[sender_idx], feedback)
    decide_file = f"{run}_cal_decide.json"
    has_recheck = f"{run}_cal_recheck.json" in files
    _check(has_recheck or decide_file not in files, "decide_without_recheck")
    if not has_recheck:
        raise FileNotFoundError
    revised_text = _turn(turn_root, run, "cal_recheck", sender, recheck, turns)
    try:
        revised = protocol.decode_list(revised_text)
    except ValueError:
        _check(decide_file not in files, "decide_after_invalid_recheck")
        return (
            recheck,
            None,
            (protocol.feedback_wire(feedback), actor.INVALID_WIRE),
            None,
            True,
        )
    decide = protocol.decision_prompt(
        tables[receiver_idx], seeds[receiver_idx], revised
    )
    if decide_file not in files:
        raise FileNotFoundError
    decide_text = _turn(turn_root, run, "cal_decide", receiver, decide, turns)
    try:
        slot = protocol.decode_slot(decide_text)
    except ValueError:
        slot = None
    return (
        recheck,
        decide,
        (protocol.feedback_wire(feedback), protocol.feedback_wire(revised)),
        slot,
        False,
    )


def _seat_metadata(
    contexts: dict[str, _Row],
    block: protocol.Block,
    tables: tuple[packet.Table, packet.Table],
    run: str,
    sender_hash: str,
    receiver_hash: str | None,
    slot: int | None,
    invalid_sender: bool,
) -> None:
    for seat, stage, prompt_hash, incoming, outgoing in (
        (
            block.sender,
            "model_invalid" if invalid_sender else "complete",
            sender_hash,
            "m1",
            "m2",
        ),
        (
            block.receiver,
            "peer_model_invalid"
            if invalid_sender
            else "model_invalid"
            if slot is None
            else "complete",
            receiver_hash,
            "m2",
            "m1",
        ),
    ):
        _same(
            contexts[seat]["metadata"],
            {
                "calendar": {
                    "schema": actor.SEAT_SCHEMA,
                    "run": run,
                    "seat": seat,
                    "control": False,
                    "stage": stage,
                    "own_table_sha256": _sha(
                        _json(tables[_SEATS.index(seat)]).encode()
                    ),
                    "own_prompt_sha256": prompt_hash,
                    "incoming_id": incoming,
                    "outgoing_id": outgoing,
                }
            },
            "seat_transcript_binding",
        )


def _branch(
    trial: Path,
    native: _Row | None,
    source: Path,
    checksum: str,
    block: protocol.Block,
    arm: str,
    tables: tuple[packet.Table, packet.Table],
    seeds: tuple[tuple[int, ...], tuple[int, ...]],
    files: set[str],
    turns: dict[str, _Row],
) -> tuple[_Row, str | None, tuple[datetime, datetime]]:
    actions = None
    try:
        # A partial native run can still have real relay receipts. Replay them
        # before checking the result, but do not count a receipt as provider usage.
        actions = _actions(
            trial.parent.parent / "turns",
            trial.name,
            files,
            block,
            tables,
            seeds,
            arm == "peer",
            turns,
        )
    except FileNotFoundError:
        pass
    if native is None:
        raise _EvidenceError("native_result_absent", "missing_evidence")
    contexts, interval = _native(trial, native, source, checksum, block, arm, seeds)
    if actions is None:
        raise _EvidenceError("relay_turn_absent", "missing_evidence")
    recheck, decide, wires, slot, invalid_sender = actions
    sender_hash, receiver_hash = (
        _sha(recheck.encode()),
        _sha(decide.encode()) if decide is not None else None,
    )
    _seat_metadata(
        contexts,
        block,
        tables,
        trial.name,
        sender_hash,
        receiver_hash,
        slot,
        invalid_sender,
    )
    _timeline(trial, block, wires, slot)
    (target,) = set(packet.starts(tables[0])).intersection(packet.starts(tables[1]))
    reward = _verify_reward(trial, native, block, slot, target)
    row = _arm("complete" if slot is not None else "model_invalid_scored_zero")
    row.update(
        reward=reward,
        slot=slot,
        sender_prompt_sha256=sender_hash,
        receiver_prompt_sha256=receiver_hash,
        native_timeline_verified=True,
    )
    return row, decide, interval


def _analyze(out: Path, report: _Row, turns: dict[str, _Row]) -> None:
    try:
        root = _entries(out, {"block.json", "native", "turns", "host"})
    except FileNotFoundError:
        return
    if "host" in root:
        host = out / "host"
        _check(
            host.resolve() == host and stat.S_ISDIR(host.lstat().st_mode),
            "unsafe_host_scratch",
        )
    if "block.json" not in root:
        for name in root & {"native", "turns"}:
            _entries(out / name, set())
        return
    binding = _read(out / "block.json")
    _check(
        binding.keys()
        == {
            "schema",
            "world",
            "direction",
            "repetition",
            "source",
            "native_sha256",
            "semantics_sha256",
        }
        and binding["schema"] == "seatlab.calendar-replica/v1",
        "block_shape",
    )
    world, direction, repetition = (
        binding[key] for key in ("world", "direction", "repetition")
    )
    _check(
        all(type(value) is int for value in (world, direction, repetition))
        and world in range(protocol._WORLD_COUNT)
        and direction in (0, 1)
        and repetition in (0, 1),
        "unregistered_block",
    )
    report.update(world=world, direction=direction, repetition=repetition)
    block = protocol.Block(world, direction, repetition)
    run = f"rx-w{world:02d}-d{direction}-r{repetition}"
    task = Path(binding["source"])
    _check(
        task.is_absolute()
        and task.resolve() == task
        and str(task) == binding["source"]
        and task.name == f"schedule-{world:03d}-talk"
        and not (task.is_relative_to(out) or out.is_relative_to(task.parent)),
        "source_path",
    )
    identity = source_identity.verify(task, world)
    _same(binding["native_sha256"], identity.native_sha256, "native_source_checksum")
    _same(
        binding["semantics_sha256"],
        identity.semantics_sha256,
        "source_semantics_checksum",
    )
    report.update(
        native_sha256=identity.native_sha256, semantics_sha256=identity.semantics_sha256
    )
    tables = packet.load(packet.PACKET, packet.PACKET_SHA256)[world].tables
    seed_files = {seat: f"{run}_{_ROLES[seat]}.json" for seat in _SEATS}
    branch_names = {arm: f"{run}-{arm}" for arm in _ARMS}
    branch_files = {
        arm: {f"{name}_cal_recheck.json", f"{name}_cal_decide.json"}
        for arm, name in branch_names.items()
    }
    native = (
        _entries(out / "native", set(branch_names.values()))
        if "native" in root
        else set()
    )
    files = (
        _entries(out / "turns", set(seed_files.values()).union(*branch_files.values()))
        if "turns" in root
        else set()
    )
    if not files and not native:
        return
    _check(seed_files["p2"] not in files or seed_files["p1"] in files, "seed_order")
    seeds: list[tuple[int, ...]] = []
    for seat, table in zip(_SEATS, tables, strict=True):
        if seed_files[seat] in files:
            try:
                text = _turn(
                    out / "turns",
                    run,
                    _ROLES[seat],
                    seat,
                    protocol.initial_prompt(table),
                    turns,
                )
            except _REPLAY_ERRORS:
                report["seeds"][seat]["status"] = "invalid_evidence"
                raise
            try:
                seeds.append(protocol.decode_list(text))
            except ValueError:
                report["seeds"][seat]["status"] = "model_invalid"
            else:
                report["seeds"][seat]["status"] = "accepted"
    if len(seeds) != 2:
        _check(
            not native and files <= set(seed_files.values()),
            "native_without_accepted_seeds",
        )
        report["status"] = (
            "invalid_seed" if set(seed_files.values()) <= files else "partial"
        )
        return
    started = {arm for arm, name in branch_names.items() if name in native}
    for arm in _ARMS:
        _check(arm in started or not (files & branch_files[arm]), "turn_without_trial")
    first, second = block.arms
    _check(second not in started or first in started, "branch_order")
    trial_digests: dict[str, str] = {}
    intervals: dict[str, tuple[datetime, datetime]] = {}
    prompts: dict[str, str | None] = {}
    for arm in block.arms:
        if arm not in started:
            continue
        trial = out / "native" / branch_names[arm]
        entries = _entries(
            trial,
            {
                "agent",
                "artifacts",
                "config.json",
                "exception.txt",
                "lock.json",
                "result.json",
                "seats",
                "trial.log",
                "verifier",
            },
        )
        try:
            row = _read(trial / "result.json") if "result.json" in entries else None
            if row is not None:
                value = row["id"]
                uid = UUID(value)
                _check(str(uid) == value and uid.version == 4, "trial_uuid")
                trial_digests[arm] = _sha(value.encode())
            if "exception.txt" in entries and row is not None:
                _check(row["exception_info"] is not None, "unbound_exception_record")
            report["arms"][arm], prompts[arm], intervals[arm] = _branch(
                trial,
                row,
                task,
                identity.native_sha256,
                block,
                arm,
                tables,
                (seeds[0], seeds[1]),
                files,
                turns,
            )
        except _REPLAY_ERRORS as exc:
            failure = _failure(exc)
            report["arms"][arm] = {
                **_arm(failure["status"]),
                "reason": failure["reason"],
            }
        report["arms"][arm]["trial_id_sha256"] = trial_digests.get(arm)
    if len(set(trial_digests.values())) != len(trial_digests):
        raise _EvidenceError("duplicate_trial_uuid")
    _finish_pair(report, block, started, intervals, prompts)


def _finish_pair(
    report: _Row,
    block: protocol.Block,
    started: set[str],
    intervals: dict[str, tuple[datetime, datetime]],
    prompts: dict[str, str | None],
) -> None:
    first, second = block.arms
    if second in started and first not in intervals:
        _check(
            report["arms"][first]["status"] != "native_failure",
            "arm_after_native_failure",
        )
        status = report["arms"][first]["status"]
        report["arms"][second] = {
            **_arm(status, trial_digest=report["arms"][second]["trial_id_sha256"]),
            "reason": "first_arm_not_verified",
        }
    if len(intervals) == 2:
        _check(intervals[first][1] <= intervals[second][0], "overlapping_native_arms")
        if all(prompt is not None for prompt in prompts.values()):
            report["equal_final_prompt"] = prompts["blind"] == prompts["peer"]
        blind, peer = (report["arms"][arm]["slot"] for arm in _ARMS)
        if blind is not None and peer is not None:
            report["equal_choice"] = blind == peer
    statuses = {arm["status"] for arm in report["arms"].values()}
    if statuses <= _SCORED:
        report["status"] = "complete"
    elif "invalid_evidence" in statuses:
        report["status"] = "invalid_evidence"
    else:
        report["status"] = "partial"


def analyze(out: Path) -> _Row:
    """Return public replay evidence. A counted turn is a bound receipt, not a bill."""
    report = _report()
    turns: dict[str, _Row] = {}
    path = out.absolute()
    try:
        _analyze(path, report, turns)
    except _REPLAY_ERRORS as exc:
        failure = _failure(exc)
        status = (
            "partial" if failure["status"] == "missing_evidence" else failure["status"]
        )
        report.update(status=status, reason=failure["reason"])
        report["arms"] = {
            arm: _arm(
                failure["status"], trial_digest=report["arms"][arm]["trial_id_sha256"]
            )
            for arm in _ARMS
        }
    report["counted_turns"] = len(turns)
    report["turns"] = turns
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
