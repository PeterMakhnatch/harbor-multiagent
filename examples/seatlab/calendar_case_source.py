import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .calendar_notices import audit, heldout, team_source, team_views
from .calendar_notices import protocol as p
from .confirmation_custody import _select_charge, _Turn
from .visibility import calendar_packet, calendar_protocol, spend


@dataclass(frozen=True, slots=True)
class SourceCase:
    trial_uuid: str
    result_sha256: str
    stock_semantics_sha256: str
    world: str
    arm: str
    native_reply_sha256: str
    native_reward: int
    target: int
    target_visible: bool
    prompts: dict[str, str]
    inquiry: dict[str, object]


_CELL_TYPES = {
    "cpv": team_views.Cell,
    "crv": team_views.RoleCell,
    "cvj": team_views.JoinCell,
}


def _private(path: Path) -> None:
    info = path.lstat()
    audit.require(
        info.st_uid == os.geteuid() and not info.st_mode & 0o077,
        "private_source_path_required",
    )


def _location(trial: Path) -> tuple[Path, Path, team_views.Cell]:
    heldout._physical_directory(trial)
    directory = trial.parent.parent
    job = directory.parent.parent
    audit.require(
        trial.parent.name == "trials"
        and directory.parent.name == "cells"
        and directory.name == trial.name,
        "invalid_native_source_location",
    )
    prefix, world, arm = trial.name.split("-", 2)
    cell = _CELL_TYPES[prefix](world, arm)
    audit.require(trial.name == cell.run, "unsupported_native_source_cell")
    for path in (job, directory.parent, directory, trial.parent):
        _private(path)
    return job, directory, cell


def _artifacts(trial: Path, cell: team_views.Cell) -> tuple[Path, ...]:
    world = trial / "artifacts/logs/world"
    verifier = trial / "verifier"
    seats = trial / "seats"
    return (
        trial / "result.json",
        world / "messages.jsonl",
        world / "events.jsonl",
        verifier / "world-state.json",
        verifier / "reward.json",
        verifier / "score.json",
        *(
            seats / audit._actor(cell, phase) / "agent" / f"{phase}.json"
            for phase in p.PHASES
        ),
    )


def _stamp(
    path: Path, *, directory: bool, track_contents: bool = True
) -> tuple[int, ...]:
    info = path.lstat()
    audit.require(
        stat.S_ISDIR(info.st_mode)
        if directory
        else stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
        "unsafe_native_source_path",
    )
    identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    if directory:
        return identity
    file_identity = (*identity, info.st_nlink)
    if not track_contents:
        return file_identity
    return (*file_identity, info.st_ctime_ns, info.st_mtime_ns, info.st_size)


def _snapshot(root: Path, files: tuple[Path, ...]) -> dict[Path, tuple[int, ...]]:
    heldout._physical_directory(root)
    audit.require(
        all(root in path.parents for path in files), "uncontained_source_path"
    )
    directories = {
        parent
        for path in files
        for parent in path.parents
        if parent.is_relative_to(root)
    }
    stamps = {path: _stamp(path, directory=True) for path in sorted(directories)}
    stamps.update((path, _stamp(path, directory=False)) for path in files)
    return stamps


def _ledger(spend_dir: Path) -> tuple[Path, tuple[int, ...], tuple[int, ...]]:
    heldout._physical_directory(spend_dir)
    path = spend._location(spend_dir)
    _private(path)
    audit.require(
        not any(
            audit._present(path.with_name(path.name + suffix))
            for suffix in ("-wal", "-shm")
        ),
        "unexpected_native_source_ledger_format",
    )
    return (
        path,
        _stamp(spend_dir, directory=True),
        _stamp(path, directory=False, track_contents=False),
    )


def _calls(path: Path, cell: team_views.Cell) -> list[audit.Row]:
    with path.open("rb") as stream:
        header = stream.read(20)
    audit.require(
        header.startswith(b"SQLite format 3\x00") and header[18:20] == b"\x01\x01",
        "unexpected_native_source_ledger_format",
    )
    phases = {p.role(cell, phase): phase for phase in p.PHASES}
    calls = []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        recorded = db.execute(
            "SELECT role, reserved_at, started_at, finished_at "
            "FROM calls WHERE run = ? ORDER BY role LIMIT ?",
            (cell.run, len(phases) + 1),
        ).fetchall()
        audit.require(
            len(recorded) == len(phases)
            and {record[0] for record in recorded} == phases.keys(),
            "missing_or_extra_native_source_calls",
        )
        for role, reserved_at, started_at, finished_at in recorded:
            phase = phases[role]
            turn = _Turn(cell.run, role, audit._actor(cell, phase), phase)
            status, charge = _select_charge(db, turn)
            if status != "settled" or charge is None:
                raise audit.BindingError("unsettled_native_source_call")
            calls.append(
                {
                    "run": cell.run,
                    "reserved_at": reserved_at,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "role": role,
                    "served_model": charge.model,
                    "cache_hit": charge.hit,
                    "cache_miss": charge.miss,
                    "output": charge.output,
                    "estimated_usd_low": spend._usd(charge.low),
                    "estimated_usd_high": spend._usd(charge.high),
                    "unsettled_hold_usd": spend._usd(0),
                }
            )
    return calls


def _read_case(
    trial: Path,
    task: Path,
    cell: team_views.Cell,
    row: audit.Row,
    calls: list[audit.Row],
) -> SourceCase:
    result_data = audit.raw(trial / "result.json")
    result = audit._object(result_data)
    source = row["source"]
    result_sha = calendar_packet._check_digest(row["result_sha256"])
    task_sha = calendar_packet._check_digest(source["task_sha256"])
    semantics_sha = calendar_packet._check_digest(source["semantics_sha256"])
    audit.require(
        p.digest(result_data) == result_sha
        and result["id"] == row["trial_uuid"]
        and result["trial_name"] == cell.run
        and result["task_checksum"] == task_sha
        and result["task_id"] == {"path": str(task)},
        "native_source_result_binding",
    )
    identity, tables = team_source._checked(task, cell)
    audit.require(
        (identity.native_sha256, identity.semantics_sha256)
        == (task_sha, semantics_sha),
        "native_source_stock_binding",
    )
    target = audit._target(tables)
    receiver = tables[p.SEATS.index(cell.receiver)]
    messages = audit._messages(trial, cell)
    notice_text, request_text, opened_text = (message["text"] for message in messages)
    notice = audit._object(notice_text)
    initial = notice["initial"]
    receipts = audit._receipts(trial, cell)
    audit.require(
        receipts.keys() == set(p.PHASES)
        and all(turn["status"] == "accepted" for turn in receipts.values()),
        "native_source_accepted_turns_required",
    )
    revision_receipt = receipts["revision"]
    revision = p.decode(revision_receipt["response_text"], "revision")
    audit.require(
        p.wire(revision_receipt["value"]) == p.wire(revision),
        "native_source_revision_binding",
    )
    days = p.decode(receipts["pick"]["response_text"], "pick")
    audit.require(
        request_text == p.request_wire(days)
        and opened_text == p.read_wire(revision, days),
        "native_source_opened_report_binding",
    )
    opened = audit._object(opened_text)
    prompts = {
        "factual": team_views.choice_prompt(receiver, initial, opened, cell.arm),
        "lists": team_views.choice_prompt(receiver, initial, opened, "lists"),
        "checked": team_views.choice_prompt(receiver, initial, opened, "checked"),
    }
    receipt = receipts["choice"]
    choice_call = next(call for call in calls if call["role"] == p.role(cell, "choice"))
    choice = audit._check_turn(
        receipt, row, cell, "choice", prompts["factual"], choice_call
    )
    submit = audit._score(trial, cell, row, messages, target)
    audit.require(
        type(choice) is int
        and submit is not None
        and submit["answer"] == calendar_protocol.answer(choice),
        "native_source_submission_binding",
    )
    visible = team_views.visible_sets(receiver, initial, opened)
    target_visible = (
        target in visible["opened"] or target in visible["unopened_initial"]
    )
    audit.require(
        target_visible is row["joint_slot_in_final_report"],
        "native_source_visibility_binding",
    )
    return SourceCase(
        trial_uuid=row["trial_uuid"],
        result_sha256=result_sha,
        stock_semantics_sha256=semantics_sha,
        world=cell.world,
        arm=cell.arm,
        native_reply_sha256=calendar_packet._check_digest(receipt["response_sha256"]),
        native_reward=row["reward"],
        target=target,
        target_visible=target_visible,
        prompts=prompts,
        inquiry={
            "schema": "seatlab.calendar-inquiry-source/v1",
            "trial_uuid": row["trial_uuid"],
            "result_sha256": result_sha,
            "stock_semantics_sha256": semantics_sha,
            "world": cell.world,
            "arm": cell.arm,
            "receiver_table": [list(masks) for masks in receiver],
            "notice": notice,
            "revision": revision,
            "native_days": days,
            "native_opened_sha256": p.digest(opened_text),
            "native_final_prompt_sha256": calendar_packet._check_digest(
                receipt["prompt_sha256"]
            ),
            "target": target,
        },
    )


def _load(trial: Path, spend_dir: Path) -> SourceCase:
    trial = trial.expanduser().absolute()
    spend_dir = spend_dir.expanduser().absolute()
    job, directory, cell = _location(trial)
    config = directory / "config.json"
    native_files = (
        directory / "attempt.json",
        config,
        directory / "outcome.json",
        *_artifacts(trial, cell),
    )
    native_before = _snapshot(job, native_files)
    registration = audit._object(audit._sealed(config))
    task = Path(registration["task"]["path"])
    audit.require(task.is_absolute(), "native_source_stock_location")
    stock_files = tuple(task / name for name in sorted(calendar_packet._SOURCE_FILES))
    stock_before = _snapshot(task.parent, stock_files)
    ledger_before = _ledger(spend_dir)
    calls = _calls(ledger_before[0], cell)
    row = audit._cell(job, cell, None, calls, None)
    audit._reconcile([row])
    audit.require(
        row["complete"] is True
        and row["error"] is None
        and row["reward_valid"] is True
        and row["reward_verified"] is True
        and type(row["reward"]) is int
        and row["reward"] in (0, 1)
        and row["settled_calls"] == len(p.PHASES)
        and row["held_calls"] == 0,
        "incomplete_or_invalid_native_source",
    )
    source = _read_case(trial, task, cell, row, calls)
    audit.require(
        _ledger(spend_dir) == ledger_before
        and _calls(ledger_before[0], cell) == calls
        and _ledger(spend_dir) == ledger_before
        and _snapshot(job, native_files) == native_before
        and _snapshot(task.parent, stock_files) == stock_before,
        "native_source_evidence_changed",
    )
    return source


def load(trial: Path, spend_dir: Path) -> SourceCase:
    try:
        return _load(trial, spend_dir)
    except audit.BindingError:
        raise
    except (
        ValueError,
        OSError,
        LookupError,
        TypeError,
        AttributeError,
        RuntimeError,
        sqlite3.Error,
    ):
        raise audit.BindingError("missing_or_invalid_native_source_evidence") from None
