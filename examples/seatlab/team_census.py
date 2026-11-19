"""Compare direct-raw Eval Lab trial reads against stock read-only trial census."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import UUID

if __package__:
    from . import team_driver, team_report
else:
    import team_driver
    import team_report

SCHEMA = "seatlab.native-team-census/v1"
_ROLES = ("inventory", "dispatch")
_TOKEN_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
_CENSUS_ERROR_EXACT = {
    "unknown native identity; no projection attempted": "unknown_native_identity",
    "unsafe native identity; no projection attempted": "unsafe_native_identity",
}


@dataclass(frozen=True)
class RawTrialSnapshot:
    """Normalized direct-raw trial view from Eval Lab's ``results.load_trial``."""

    source_trial_dir: str
    trial_id: str | None
    job_id: str | None
    primary_reward: float | None
    reward_valid: bool | None
    started_at: str | None
    finished_at: str | None
    seat_usage: dict[str, dict[str, int | None]]


@dataclass(frozen=True)
class CensusRowSnapshot:
    """Normalized stock census row from ``connect_trials(..., read_only=True)``."""

    source_trial_dir: str | None
    trial_id: str | None
    job_id: str | None
    reward: float | None
    projection_error: str | None
    raw_trial_id_present: bool = False


@dataclass(frozen=True)
class SlotObservation:
    """One registered study slot pairing verified team status with raw trial reads."""

    cell_id: str
    seed: int
    condition: str
    pairing: str
    model_actors: int
    attempted: bool
    team_status: Literal["measured", "unmeasured", "invalid", "not_attempted"]
    team_error: str | None
    verified_trial_id: str | None
    source_trial_dir: str | None
    raw_trial: RawTrialSnapshot | None
    raw_load_error: str | None = None


def _normalize_uuid(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return None if value.int == 0 else str(value)
    if isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError:
            return None
        return str(parsed) if parsed.int != 0 and str(parsed) == value else None
    return None


def _normalize_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _finite_number(value: object) -> float | None:
    if type(value) in (int, float):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _nonnegative_int(value: object) -> int | None:
    if type(value) is int and 0 <= value < 2**53:
        return value
    return None


def _empty_role_usage() -> dict[str, int | None]:
    return {
        "n_input_tokens": None,
        "n_cache_tokens": None,
        "n_output_tokens": None,
    }


def _extract_seat_usage(record: Any) -> dict[str, dict[str, int | None]]:
    usage = {role: _empty_role_usage() for role in _ROLES}
    raw_seats = getattr(record, "seat_results", None)
    if raw_seats is None:
        result = getattr(record, "result", None)
        if isinstance(result, Mapping):
            raw_seats = result.get("seat_results")
    if not isinstance(raw_seats, Sequence) or isinstance(raw_seats, (str, bytes)):
        return usage
    seen: set[str] = set()
    for seat in raw_seats:
        if isinstance(seat, Mapping):
            role = seat.get("seat")
            context = seat.get("agent_result")
        else:
            role = getattr(seat, "seat", None)
            context = getattr(seat, "agent_result", None)
        if not isinstance(role, str) or role not in usage or role in seen:
            continue
        seen.add(role)
        if context is None:
            continue
        if isinstance(context, Mapping):
            n_input = context.get("n_input_tokens")
            n_cache = context.get("n_cache_tokens")
            n_output = context.get("n_output_tokens")
        else:
            n_input = getattr(context, "n_input_tokens", None)
            n_cache = getattr(context, "n_cache_tokens", None)
            n_output = getattr(context, "n_output_tokens", None)
        usage[role] = {
            "n_input_tokens": _nonnegative_int(n_input),
            "n_cache_tokens": _nonnegative_int(n_cache),
            "n_output_tokens": _nonnegative_int(n_output),
        }
    return usage


def normalize_trial_record(record: Any, trial_dir: Path) -> RawTrialSnapshot:
    """Normalize an Eval Lab ``TrialRecord`` without fabricating missing fields."""
    result = getattr(record, "result", None)
    result_map: Mapping[str, Any] = result if isinstance(result, Mapping) else {}
    raw_id: object = None
    try:
        raw_id = record.id
    except (AttributeError, KeyError, TypeError):
        raw_id = None
    if raw_id is None:
        raw_id = result_map.get("id")
    trial_id = _normalize_uuid(raw_id)

    config = result_map.get("config")
    raw_job_id = config.get("job_id") if isinstance(config, Mapping) else None
    if raw_job_id is None:
        record_config = getattr(record, "config", None)
        if isinstance(record_config, Mapping):
            raw_job_id = record_config.get("job_id")
    job_id = _normalize_uuid(raw_job_id)

    raw_reward = getattr(record, "primary_reward", None)
    primary_reward = _finite_number(raw_reward)
    raw_valid = result_map.get("reward_valid")
    reward_valid = raw_valid if type(raw_valid) is bool else None

    return RawTrialSnapshot(
        source_trial_dir=str(trial_dir.resolve()),
        trial_id=trial_id,
        job_id=job_id,
        primary_reward=primary_reward,
        reward_valid=reward_valid,
        started_at=_normalize_timestamp(result_map.get("started_at")),
        finished_at=_normalize_timestamp(result_map.get("finished_at")),
        seat_usage=_extract_seat_usage(record),
    )


def classify_census_error(error: str | None) -> str | None:
    """Classify a stock Eval Lab census ``projection_error`` into a bounded code."""
    if error is None:
        return None
    normalized = error.strip()
    if not normalized:
        return "unknown_projection_error"
    exact = _CENSUS_ERROR_EXACT.get(normalized)
    if exact is not None:
        return exact
    if "read-only projection failed" in normalized:
        return "read_only_projection_failed"
    if "missing projections:" in normalized:
        return "missing_projections"
    if "stale projections:" in normalized:
        return "stale_projections"
    if "conflicting" in normalized:
        return "conflicting_projections"
    if "unreadable" in normalized:
        return "unreadable_projections"
    if "backfill failed" in normalized:
        return "backfill_failed"
    return "unknown_projection_error"


def _census_error_for_rows(rows: Sequence[CensusRowSnapshot]) -> str | None:
    codes = [
        code
        for row in rows
        if (code := classify_census_error(row.projection_error)) is not None
    ]
    if not codes:
        return None
    unique = sorted(set(codes))
    return unique[0] if len(unique) == 1 else "multiple_projection_errors"


def _summarize_role_usage(
    slots: Sequence[dict[str, Any]],
) -> dict[str, dict[str, int | None]]:
    summary: dict[str, dict[str, int | None]] = {}
    for role in _ROLES:
        token_sums: dict[str, int | None] = {}
        token_known_counts: dict[str, int] = {}
        token_null_counts: dict[str, int] = {}
        for field in _TOKEN_FIELDS:
            values = [
                value
                for slot in slots
                if (value := slot["seats"][role][field]) is not None
            ]
            token_known_counts[field] = len(values)
            token_null_counts[field] = len(slots) - len(values)
            token_sums[field] = sum(values) if values else None
        summary[role] = {
            "slots_with_known_input_tokens": token_known_counts["n_input_tokens"],
            "slots_with_null_input_tokens": token_null_counts["n_input_tokens"],
            "known_input_tokens": token_sums["n_input_tokens"],
            "slots_with_known_cache_tokens": token_known_counts["n_cache_tokens"],
            "slots_with_null_cache_tokens": token_null_counts["n_cache_tokens"],
            "known_cache_tokens": token_sums["n_cache_tokens"],
            "slots_with_known_output_tokens": token_known_counts["n_output_tokens"],
            "slots_with_null_output_tokens": token_null_counts["n_output_tokens"],
            "known_output_tokens": token_sums["n_output_tokens"],
        }
    return summary


def compare_census(
    slots: Sequence[SlotObservation],
    census_rows: Sequence[CensusRowSnapshot],
    *,
    registered_plan_sha256: str | None = None,
    census_rows_scanned: int | None = None,
) -> dict[str, Any]:
    """Compare direct-raw registered team slots against stock read-only census rows.

    Returns compact public JSON with no local paths, raw model text, credentials,
    cost strings, or source trial directories.
    """
    census_by_path: dict[str, list[CensusRowSnapshot]] = {}
    census_by_uuid: dict[str, list[CensusRowSnapshot]] = {}
    census_by_pair: dict[tuple[str, str], list[CensusRowSnapshot]] = {}
    for row in census_rows:
        if row.source_trial_dir is not None:
            census_by_path.setdefault(row.source_trial_dir, []).append(row)
        if row.trial_id is not None:
            census_by_uuid.setdefault(row.trial_id, []).append(row)
        if row.job_id is not None and row.trial_id is not None:
            census_by_pair.setdefault((row.job_id, row.trial_id), []).append(row)

    # Detect reused native trial UUIDs across registered slots.
    uuid_counts: dict[str, int] = {}
    for slot in slots:
        native_id = (
            slot.raw_trial.trial_id
            if slot.raw_trial is not None and slot.raw_trial.trial_id is not None
            else slot.verified_trial_id
        )
        if native_id is not None:
            uuid_counts[native_id] = uuid_counts.get(native_id, 0) + 1
    reused_uuids = {uid for uid, count in uuid_counts.items() if count > 1}

    output_slots: list[dict[str, Any]] = []
    for slot in slots:
        raw = slot.raw_trial
        native_uuid = (
            raw.trial_id
            if raw is not None and raw.trial_id is not None
            else slot.verified_trial_id
        )
        native_job_id = raw.job_id if raw is not None else None
        reused_native_uuid = native_uuid is not None and native_uuid in reused_uuids

        id_mismatch = (
            raw is not None
            and slot.verified_trial_id is not None
            and raw.trial_id is not None
            and raw.trial_id != slot.verified_trial_id
        )

        # Team status: measured vs unavailable (never turn an invalid attempt into a score).
        team_measured = (
            slot.team_status == "measured"
            and native_uuid is not None
            and not reused_native_uuid
            and not id_mismatch
            and raw is not None
            and slot.raw_load_error is None
        )
        registered_status: Literal["measured", "unavailable"] = (
            "measured" if team_measured else "unavailable"
        )
        # Never expose an invalid or reused attempt's raw reward as a measured team score.
        native_shared_reward = (
            raw.primary_reward
            if team_measured and raw is not None and raw.reward_valid is True
            else None
        )
        # Separate local exact-path join vs native UUID join vs native (job_id, trial_id) join.
        path_matches = (
            census_by_path.get(slot.source_trial_dir, [])
            if slot.source_trial_dir is not None
            else []
        )
        uuid_matches = (
            census_by_uuid.get(native_uuid, [])
            if native_uuid is not None and not reused_native_uuid
            else []
        )
        job_uuid_matches = (
            census_by_pair.get((native_job_id, native_uuid), [])
            if native_job_id is not None
            and native_uuid is not None
            and not reused_native_uuid
            else []
        )

        # Path conflict: census at the exact real path reports a different native UUID,
        # a non-UUID/synthetic trial ID, or a different job UUID.
        path_identity_conflict = any(
            (row.raw_trial_id_present and row.trial_id is None)
            or (native_uuid is not None and row.trial_id != native_uuid)
            or (native_job_id is not None and row.job_id != native_job_id)
            for row in path_matches
        )

        # Census reward is trusted only from an exact native (job_id, trial_id) match.
        job_uuid_rewards = [
            row.reward for row in job_uuid_matches if row.reward is not None
        ]
        census_reward = (
            job_uuid_rewards[0]
            if len(job_uuid_matches) == 1
            and len(job_uuid_rewards) == 1
            and not path_identity_conflict
            else None
        )
        census_reward_matches_native = (
            census_reward is not None
            and native_shared_reward is not None
            and census_reward == native_shared_reward
        )

        # Census error classification: prefer exact job+uuid row, else exact path row, else uuid row.
        error_source = job_uuid_matches or path_matches or uuid_matches
        census_error = _census_error_for_rows(error_source)

        seats = {
            role: (
                dict(raw.seat_usage[role])
                if raw is not None and not reused_native_uuid and not id_mismatch
                else _empty_role_usage()
            )
            for role in _ROLES
        }

        if reused_native_uuid:
            slot_reason = "duplicate_native_trial_id"
        elif slot.raw_load_error is not None:
            slot_reason = slot.raw_load_error
        elif id_mismatch:
            slot_reason = "native_trial_id_mismatch"
        elif slot.team_status == "measured":
            slot_reason = None
        elif slot.team_error is not None:
            slot_reason = slot.team_error
        else:
            slot_reason = slot.team_status
        detail_status = (
            "invalid"
            if reused_native_uuid or id_mismatch or slot.raw_load_error is not None
            else slot.team_status
        )
        output_slots.append(
            {
                "cell_id": slot.cell_id,
                "seed": slot.seed,
                "condition": slot.condition,
                "pairing": slot.pairing,
                "model_actors": slot.model_actors,
                "attempted": slot.attempted,
                "registered_team_status": registered_status,
                "detail_status": detail_status,
                "detail_reason": slot_reason,
                "native_trial_id": native_uuid,
                "native_job_id": native_job_id,
                "native_trial_id_reused": reused_native_uuid,
                "native_shared_reward": native_shared_reward,
                "native_started_at": raw.started_at if raw is not None else None,
                "native_finished_at": raw.finished_at if raw is not None else None,
                "census": {
                    "path_matches": len(path_matches),
                    "uuid_matches": len(uuid_matches),
                    "job_uuid_matches": len(job_uuid_matches),
                    "path_identity_conflict": path_identity_conflict,
                    "reward": census_reward,
                    "reward_matches_native": census_reward_matches_native,
                    "error": census_error,
                },
                "seats": seats,
            }
        )

    counts = {
        "registered": len(output_slots),
        "attempted": sum(slot["attempted"] for slot in output_slots),
        "measured": sum(
            slot["registered_team_status"] == "measured" for slot in output_slots
        ),
        "unavailable": sum(
            slot["registered_team_status"] == "unavailable" for slot in output_slots
        ),
        "invalid": sum(slot["detail_status"] == "invalid" for slot in output_slots),
        "unmeasured": sum(
            slot["detail_status"] == "unmeasured" for slot in output_slots
        ),
        "not_attempted": sum(
            slot["detail_status"] == "not_attempted" for slot in output_slots
        ),
        "with_native_trial_id": sum(
            slot["native_trial_id"] is not None for slot in output_slots
        ),
        "with_reused_native_trial_id": sum(
            slot["native_trial_id_reused"] for slot in output_slots
        ),
        "with_native_job_id": sum(
            slot["native_job_id"] is not None for slot in output_slots
        ),
        "with_native_shared_reward": sum(
            slot["native_shared_reward"] is not None for slot in output_slots
        ),
        "census_rows_scanned": (
            len(census_rows) if census_rows_scanned is None else census_rows_scanned
        ),
        "census_matched_by_path": sum(
            slot["census"]["path_matches"] > 0 for slot in output_slots
        ),
        "census_matched_by_uuid": sum(
            slot["census"]["uuid_matches"] > 0 for slot in output_slots
        ),
        "census_matched_by_job_and_uuid": sum(
            slot["census"]["job_uuid_matches"] == 1 for slot in output_slots
        ),
        "census_path_identity_conflicts": sum(
            slot["census"]["path_identity_conflict"] for slot in output_slots
        ),
        "census_rewards_known": sum(
            slot["census"]["reward"] is not None for slot in output_slots
        ),
        "census_rewards_matching_native": sum(
            slot["census"]["reward_matches_native"] for slot in output_slots
        ),
        "census_errors_by_code": _count_error_codes(output_slots),
    }

    return {
        "schema": SCHEMA,
        "registered_plan_sha256": registered_plan_sha256,
        "reward_attribution": "shared_trial_reward_not_assigned_per_role",
        "counts": counts,
        "role_usage": _summarize_role_usage(output_slots),
        "slots": output_slots,
    }


def _count_error_codes(slots: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for slot in slots:
        code = slot["census"]["error"]
        if code is not None:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _load_census_rows(
    repo: Path, scratch: Path, roots: Sequence[Path]
) -> tuple[list[CensusRowSnapshot], int]:
    from evallab.storage.trials import connect_trials

    con, info = connect_trials(
        repo_root=repo,
        roots=list(roots),
        derived_root=scratch,
        read_only=True,
    )
    with closing(con):
        raw_rows = (
            con.execute(
                """
                SELECT source_trial_dir, trial_id, job_id, reward, projection_error
                FROM trials
                ORDER BY source_trial_dir NULLS LAST, job_id NULLS LAST, trial_id NULLS LAST
                """
            )
            .to_arrow_table()
            .to_pylist()
        )
        n_rows = int(info["n_rows"])
    snapshots = []
    for row in raw_rows:
        raw_tid = row.get("trial_id")
        snapshots.append(
            CensusRowSnapshot(
                source_trial_dir=(
                    str(Path(path).resolve())
                    if isinstance(path := row.get("source_trial_dir"), str) and path
                    else None
                ),
                trial_id=_normalize_uuid(raw_tid),
                job_id=_normalize_uuid(row.get("job_id")),
                reward=_finite_number(row.get("reward")),
                projection_error=(
                    err if isinstance(err := row.get("projection_error"), str) else None
                ),
                raw_trial_id_present=(
                    bool(raw_tid) if isinstance(raw_tid, str) else raw_tid is not None
                ),
            )
        )
    return snapshots, n_rows


def inspect_study(
    study_root: Path,
    evallab_root: Path,
    scratch_dir: Path | None = None,
    *,
    census_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Read a registered native team study and compare raw trials vs stock census."""
    import evallab
    from evallab.results import load_trial

    repo = evallab_root.expanduser().resolve()
    if not evallab.__file__ or not Path(evallab.__file__).resolve().is_relative_to(
        repo
    ):
        raise ValueError("run the CLI with the Python environment of --evallab-root")

    with ExitStack() as stack:
        if scratch_dir is None:
            scratch = Path(
                stack.enter_context(TemporaryDirectory(prefix="seatlab-team-census-"))
            ).resolve()
        else:
            candidate = scratch_dir.expanduser()
            if candidate.is_symlink():
                raise ValueError("--scratch-dir must not be a symbolic link")
            if candidate.exists():
                if not candidate.is_dir() or any(candidate.iterdir()):
                    raise ValueError("--scratch-dir must be a new or empty directory")
            else:
                candidate.mkdir(parents=True, mode=0o700)
            scratch = candidate.resolve()

        root = study_root.expanduser().resolve()
        plan = team_driver._load_plan(root)
        checked_report = team_report.collect(root)
        report_by_cell = {row["cell_id"]: row for row in checked_report["cells"]}

        observations: list[SlotObservation] = []
        positive_trial_dirs: list[Path] = []

        for cell in plan["cells"]:
            cell_id = cell["cell_id"]
            checked = report_by_cell[cell_id]
            trial_dir: Path | None = None
            try:
                trial_dir = team_driver._checked_cell(root, plan, cell)
            except (OSError, ValueError, KeyError):
                trial_dir = None

            source_trial_dir: str | None = None
            raw_snapshot: RawTrialSnapshot | None = None
            raw_load_error: str | None = None

            if trial_dir is not None and trial_dir.is_dir():
                resolved_trial = trial_dir.resolve()
                source_trial_dir = str(resolved_trial)
                positive_trial_dirs.append(resolved_trial)
                if (resolved_trial / "result.json").is_file():
                    try:
                        record = load_trial(resolved_trial)
                        raw_snapshot = normalize_trial_record(record, resolved_trial)
                    except (OSError, ValueError, KeyError, TypeError):
                        raw_load_error = "raw_trial_load_failed"

            observations.append(
                SlotObservation(
                    cell_id=cell_id,
                    seed=cell["seed"],
                    condition=cell["condition"],
                    pairing=cell["pairing"],
                    model_actors=cell["pairing"].count("M"),
                    attempted=bool(checked["attempted"]),
                    team_status=checked["status"],
                    team_error=checked["error"],
                    verified_trial_id=_normalize_uuid(checked["trial_id"]),
                    source_trial_dir=source_trial_dir,
                    raw_trial=raw_snapshot,
                    raw_load_error=raw_load_error,
                )
            )

        if census_roots:
            resolved_roots = [path.expanduser().resolve() for path in census_roots]
            if not all(path.is_dir() for path in resolved_roots):
                raise ValueError("every --census-root must be an existing directory")
        else:
            resolved_roots = positive_trial_dirs

        census_rows, n_scanned = _load_census_rows(repo, scratch, resolved_roots)
        return compare_census(
            observations,
            census_rows,
            registered_plan_sha256=checked_report["registered_plan_sha256"],
            census_rows_scanned=n_scanned,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--study",
        type=Path,
        required=True,
        help="registered native team study root",
    )
    parser.add_argument(
        "--evallab-root",
        type=Path,
        required=True,
        help="checkout that provides the optional Eval Lab Python environment",
    )
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=None,
        help=(
            "optional new or existing empty directory for the isolated read-only "
            "census root; defaults to a new empty temporary directory"
        ),
    )
    parser.add_argument(
        "--census-root",
        action="append",
        type=Path,
        help=(
            "explicit census scan root; repeatable. Defaults to existing positive "
            "registered trial directories."
        ),
    )
    args = parser.parse_args(argv)
    try:
        report = inspect_study(
            args.study,
            args.evallab_root,
            args.scratch_dir,
            census_roots=args.census_root,
        )
    except ImportError:
        parser.exit(
            2,
            f"{parser.prog}: run with the configured Eval Lab Python environment\n",
        )
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
