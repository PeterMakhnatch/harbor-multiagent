"""Read-only stock Eval Lab join for one receipt-board Harbor job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

if __package__:
    from . import receipt_job, team_census
else:
    import receipt_job
    import team_census

SCHEMA = "seatlab.receipt-job-census/v1"
ROLES = receipt_job.SEATS
ARMS = receipt_job.ARMS
_DEFAULT_CASE = {"p1": ("amber", "cobalt"), "p2": ("mint", "coral")}
_KEYS = frozenset({"acknowledged_sha256", "action", "role", "turn", "view"})
_BLOCK = frozenset(
    "duplicate_seat_role error_policy_overridden incomplete_seat_roles "
    "malformed_seat_entry missing_finished_at "
    "missing_or_invalid_team_reward missing_seat_results "
    "not_multi_seat_shape primary_reward_mismatch reward_not_valid "
    "seat_exception trial_exception unexpected_seat_role "
    "verifier_disabled".split()
)


def _loads(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for key, value in items:
            if key in found:
                raise ValueError(f"duplicate key: {key!r}")
            found[key] = value
        return found

    def reject(token: str) -> Any:
        raise ValueError(f"non-finite constant: {token!r}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def _arm_spec(
    case: Mapping[str, Sequence[str]],
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, str]]]:
    views = {}
    for arm in ARMS:
        views[arm] = {}
        for role in ROLES:
            options = case[role] if arm == "full" else case[role][:1]
            views[arm][role] = list(options)
    actions = {arm: {role: views[arm][role][-1] for role in ROLES} for arm in ARMS}
    return views, actions


def _load_prepared(
    prepared: Path | None,
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, str]], str | None]:
    if prepared is None:
        return (*_arm_spec(_DEFAULT_CASE), None)
    path = prepared.expanduser()
    plan_file = path if path.is_file() else path / receipt_job.PLAN_FILENAME
    if path.is_symlink() or plan_file.is_symlink() or not plan_file.is_file():
        raise ValueError("--prepared must be a regular job-plan.json")
    raw = plan_file.read_bytes()
    parsed = _loads(raw.decode("utf-8"))
    slots = parsed.get("slots") if isinstance(parsed, dict) else None
    if not isinstance(slots, list) or len(slots) != len(ARMS):
        raise ValueError("prepared plan must define full and first")
    views, actions = {}, {}
    for item in slots:
        arm = item.get("arm") if isinstance(item, Mapping) else None
        if arm not in ARMS or arm in views:
            raise ValueError("prepared plan slot has an invalid or duplicate arm")
        expected_views = item.get("expected_views")
        expected_actions = item.get("expected_actions")
        if not isinstance(expected_views, Mapping):
            raise ValueError(f"prepared slot {arm!r} lacks views or actions")
        if not isinstance(expected_actions, Mapping):
            raise ValueError(f"prepared slot {arm!r} lacks views or actions")
        views[arm], actions[arm] = {}, {}
        for role in ROLES:
            view = expected_views.get(role)
            options = view.get("options") if isinstance(view, Mapping) else None
            action = expected_actions.get(role)
            if not isinstance(options, list) or not options:
                raise ValueError(f"invalid prepared view for {arm}/{role}")
            if any(not isinstance(item, str) or not item for item in options):
                raise ValueError(f"invalid prepared view for {arm}/{role}")
            if not isinstance(action, str) or action not in options:
                raise ValueError(f"invalid prepared view for {arm}/{role}")
            views[arm][role] = list(options)
            actions[arm][role] = action
    return views, actions, hashlib.sha256(raw).hexdigest()


def _bind_arm(
    trial_dir: Path,
    arm_views: Mapping[str, Mapping[str, Sequence[str]]],
    arm_actions: Mapping[str, Mapping[str, str]],
) -> tuple[str | None, dict[str, dict[str, Any]], bool | None, str | None]:
    relative = "artifacts/logs/world/receipts.jsonl"
    try:
        file = receipt_job._safe_under(trial_dir, relative, expect_dir=False)
        lines = file.read_text(encoding="utf-8").splitlines()
    except ValueError as exc:
        missing = "missing file" in str(exc)
        code = "missing_world_receipts" if missing else "unreadable_world_receipts"
        return None, {}, None, code
    except (OSError, UnicodeDecodeError):
        return None, {}, None, "unreadable_world_receipts"
    if not lines:
        return None, {}, None, "empty_world_receipts"
    by_role: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            row = _loads(line) if line.strip() else None
        except ValueError:
            return None, {}, None, "malformed_world_receipts"
        if not isinstance(row, dict) or set(row) != _KEYS:
            return None, {}, None, "malformed_world_receipts"
        role, view, action = row["role"], row["view"], row["action"]
        options = view.get("options") if isinstance(view, dict) else None
        digest = receipt_job._sha256_json(view) if isinstance(view, dict) else None
        valid = (
            role in ROLES
            and role not in by_role
            and type(row["turn"]) is int
            and row["turn"] == 1
            and isinstance(view, dict)
            and set(view) == {"options", "role"}
            and view.get("role") == role
            and isinstance(options, list)
            and isinstance(action, str)
            and action in options
            and row["acknowledged_sha256"] == digest
        )
        if not valid:
            return None, {}, None, "invalid_world_receipt"
        by_role[role] = {
            "acknowledged_sha256": digest,
            "action": action,
            "allowed_options": list(options),
        }
    if set(by_role) != set(ROLES):
        return None, by_role, None, "incomplete_world_receipts"
    matches = []
    for arm, expected in arm_views.items():
        if all(
            by_role[role]["allowed_options"] == list(expected[role]) for role in ROLES
        ):
            matches.append(arm)
    if len(matches) != 1:
        code = "ambiguous_arm_views" if matches else "unmatched_arm_views"
        return None, by_role, None, code
    arm = matches[0]
    ok = all(by_role[role]["action"] == arm_actions[arm][role] for role in ROLES)
    return arm, by_role, ok, None


def _seats() -> dict[str, dict[str, Any]]:
    empty = team_census._empty_role_usage()
    return {role: {"has_exception": None, "present": False, **empty} for role in ROLES}


def _blank(path: Path, code: str) -> dict[str, Any]:
    return {
        "_path": str(path),
        "arm": None,
        "arm_error": code,
        "choices_match_expected": None,
        "issues": [code],
        "job_id_matches_parent": False,
        "native_job_id": None,
        "native_team_reward": None,
        "native_trial_id": None,
        "seat_count": 0,
        "seats": _seats(),
        "world_receipts": {},
    }


def _read_trial(trial_dir, parent_job_id, load_trial, arm_views, arm_actions):
    try:
        record = load_trial(trial_dir)
    except Exception:
        return _blank(trial_dir, "load_trial_failed")
    snap = team_census.normalize_trial_record(record, trial_dir)
    result = getattr(record, "result", None)
    result = result if isinstance(result, Mapping) else {}
    arm, receipts, choices_ok, arm_error = _bind_arm(trial_dir, arm_views, arm_actions)
    issues = [arm_error] if arm_error else []
    seats, seen = _seats(), set()
    raw_seats = result.get("seat_results")
    if not isinstance(raw_seats, Sequence) or isinstance(raw_seats, (str, bytes)):
        issues.append("missing_seat_results")
    else:
        for item in raw_seats:
            role = item.get("seat") if isinstance(item, Mapping) else None
            if not isinstance(item, Mapping):
                issues.append("malformed_seat_entry")
                continue
            if role not in ROLES:
                issues.append("unexpected_seat_role")
                continue
            if role in seen:
                issues.append("duplicate_seat_role")
                continue
            seen.add(role)
            context = item.get("agent_result")
            context = context if isinstance(context, Mapping) else {}
            seats[role] = {
                "has_exception": item.get("exception_info") is not None,
                "present": True,
                **team_census._empty_role_usage(),
            }
            for field in team_census._TOKEN_FIELDS:
                seats[role][field] = team_census._nonnegative_int(context.get(field))
        if seen != set(ROLES):
            issues.append("incomplete_seat_roles")
    raw_config = result.get("config")
    config = raw_config if isinstance(raw_config, Mapping) else {}
    raw_verifier = result.get("verifier_result")
    verifier = raw_verifier if isinstance(raw_verifier, Mapping) else {}
    raw_rewards = verifier.get("rewards")
    rewards = raw_rewards if isinstance(raw_rewards, Mapping) else {}
    reward = None
    if isinstance(rewards, Mapping) and set(rewards) == {"reward"}:
        reward = team_census._finite_number(rewards.get("reward"))
    raw_gate = config.get("verifier")
    gate = raw_gate if isinstance(raw_gate, Mapping) else {}
    mismatch = bool(snap.job_id and parent_job_id and snap.job_id != parent_job_id)
    single = result.get("agent_result") is not None
    single = single or result.get("step_results") is not None
    flags = {
        "error_policy_overridden": bool(config.get("override_error_policy")),
        "missing_finished_at": result.get("finished_at") in (None, ""),
        "missing_or_invalid_job_id": snap.job_id is None,
        "missing_or_invalid_team_reward": reward is None,
        "missing_or_invalid_trial_id": snap.trial_id is None,
        "not_multi_seat_shape": single,
        "primary_reward_mismatch": reward is not None and snap.primary_reward != reward,
        "reward_not_valid": result.get("reward_valid") is not True,
        "seat_exception": any(seats[role]["has_exception"] for role in ROLES),
        "trial_exception": result.get("exception_info") is not None,
        "trial_job_id_mismatch": mismatch,
        "verifier_disabled": bool(gate.get("disable")),
    }
    issues.extend(code for code, failed in flags.items() if failed)
    reward_ok = not (set(issues) & _BLOCK)
    return {
        "_path": str(trial_dir),
        "arm": arm,
        "arm_error": arm_error,
        "choices_match_expected": choices_ok,
        "issues": sorted(set(issues)),
        "job_id_matches_parent": bool(parent_job_id and snap.job_id == parent_job_id),
        "native_job_id": snap.job_id,
        "native_team_reward": reward if reward_ok else None,
        "native_trial_id": snap.trial_id,
        "seat_count": len(seen),
        "seats": seats,
        "world_receipts": receipts,
    }


def compare_job_census(
    job,
    trials,
    census_rows,
    *,
    expected_arms=ARMS,
    prepared_plan_sha256=None,
    census_rows_scanned=None,
    derived_root_empty_after=None,
) -> dict[str, Any]:
    """Join raw trials to stock census rows. Does not invent an arm or identity."""
    by_path, by_pair = {}, {}
    for row in census_rows:
        if row.source_trial_dir is not None:
            by_path.setdefault(row.source_trial_dir, []).append(row)
        if row.job_id and row.trial_id:
            by_pair.setdefault((row.job_id, row.trial_id), []).append(row)
    seen: dict[str, int] = {}
    for trial in trials:
        trial_id = trial["native_trial_id"]
        if trial_id is not None:
            seen[trial_id] = seen.get(trial_id, 0) + 1
    reused = {trial_id for trial_id, count in seen.items() if count > 1}
    matched = set()
    index = {id(row): pos for pos, row in enumerate(census_rows)}
    rows = []
    for trial in trials:
        trial_id = trial["native_trial_id"]
        job_id = trial["native_job_id"]
        duplicate = trial_id in reused
        path_hits = by_path.get(trial["_path"], [])
        pair_hits = []
        if job_id and trial_id and not duplicate:
            pair_hits = by_pair.get((job_id, trial_id), [])
        for row in (*path_hits, *pair_hits):
            matched.add(index[id(row)])
        conflict = False
        for row in path_hits:
            bad_id = row.raw_trial_id_present and row.trial_id is None
            bad_trial = trial_id is not None and row.trial_id != trial_id
            bad_job = job_id is not None and row.job_id != job_id
            conflict = conflict or bad_id or bad_trial or bad_job
        ambiguous = len(pair_hits) > 1 or len(path_hits) > 1
        rewards = [row.reward for row in pair_hits if row.reward is not None]
        census_reward = None
        if len(pair_hits) == 1 and len(rewards) == 1 and not conflict:
            census_reward = rewards[0]
        native_reward = None if duplicate else trial["native_team_reward"]
        extra = []
        if duplicate:
            extra.append("duplicate_native_trial_id")
        if conflict:
            extra.append("census_path_identity_conflict")
        if ambiguous:
            extra.append("ambiguous_census_match")
        same = census_reward is not None and census_reward == native_reward
        census = {
            "ambiguous": ambiguous,
            "error": team_census._census_error_for_rows(pair_hits or path_hits),
            "job_trial_matches": len(pair_hits),
            "path_identity_conflict": conflict,
            "path_matches": len(path_hits),
            "reward": census_reward,
            "reward_matches_native": same,
        }
        public = {key: trial[key] for key in trial if key != "_path"}
        public["census"] = census
        public["issues"] = sorted(set(trial["issues"]) | set(extra))
        public["native_team_reward"] = native_reward
        public["native_trial_id_reused"] = duplicate
        rows.append(public)
    rows.sort(
        key=lambda row: (
            row["arm"] is None,
            row["arm"] or "",
            row["native_trial_id"] or "",
        )
    )
    arm_counts, raw_rewards, census_rewards = {}, {}, {}
    for row in rows:
        if row["arm"] is not None:
            arm_counts[row["arm"]] = arm_counts.get(row["arm"], 0) + 1
        fresh = row["native_trial_id"] and not row["native_trial_id_reused"]
        if fresh and row["native_team_reward"] is not None:
            raw_rewards[row["native_trial_id"]] = row["native_team_reward"]
        if fresh and row["census"]["reward"] is not None:
            census_rewards[row["native_trial_id"]] = row["census"]["reward"]
    usage = {}
    for role in ROLES:
        present = sum(row["seats"][role]["present"] for row in rows)
        usage[role] = {"present_trials": present}
        for field in team_census._TOKEN_FIELDS:
            values = []
            for row in rows:
                value = row["seats"][role][field]
                if value is not None:
                    values.append(value)
            short = field.removeprefix("n_")
            usage[role][f"known_{short}_trials"] = len(values)
            usage[role][f"null_{short}_trials"] = len(rows) - len(values)
            usage[role][f"sum_{short}"] = sum(values) if values else None
    scanned = len(census_rows) if census_rows_scanned is None else census_rows_scanned

    def hits(key, value) -> int:
        return sum(row["census"][key] == value for row in rows)

    missing = [arm for arm in expected_arms if not arm_counts.get(arm)]
    ambiguous_arms = [arm for arm, count in arm_counts.items() if count > 1]
    return {
        "arms": {
            "ambiguous": sorted(ambiguous_arms),
            "bound_counts": dict(sorted(arm_counts.items())),
            "expected": list(expected_arms),
            "missing": sorted(missing),
        },
        "counts": {
            "ambiguous_arms": len(ambiguous_arms),
            "census_ambiguous": hits("ambiguous", True),
            "census_errors_by_code": team_census._count_error_codes(rows),
            "census_matched_by_job_and_trial_uuid": hits("job_trial_matches", 1),
            "census_matched_by_path": sum(
                row["census"]["path_matches"] > 0 for row in rows
            ),
            "census_missing": hits("job_trial_matches", 0),
            "census_orphans": len(census_rows) - len(matched),
            "census_path_identity_conflicts": hits("path_identity_conflict", True),
            "census_rewards_matching_native": hits("reward_matches_native", True),
            "census_rows_scanned": scanned,
            "census_team_reward_sum": sum(census_rewards.values())
            if census_rewards
            else None,
            "census_team_rewards_known": len(census_rewards),
            "choices_matching_expected": sum(
                row["choices_match_expected"] is True for row in rows
            ),
            "distinct_native_trial_ids": len(seen),
            "expected_arms": len(expected_arms),
            "missing_arms": len(missing),
            "raw_team_reward_sum": sum(raw_rewards.values()) if raw_rewards else None,
            "raw_team_rewards_known": len(raw_rewards),
            "raw_trials": len(rows),
            "reused_native_trial_ids": len(reused),
            "seat_records_total": sum(row["seat_count"] for row in rows),
            "trials_matching_parent_job_id": sum(
                row["job_id_matches_parent"] for row in rows
            ),
            "unbound_trials": sum(row["arm"] is None for row in rows),
        },
        "derived_root_empty_after": derived_root_empty_after,
        "job": dict(job),
        "prepared_plan_sha256": prepared_plan_sha256,
        "reward_attribution": "shared_trial_reward_counted_once_per_native_trial_uuid",
        "role_usage": usage,
        "schema": SCHEMA,
        "trials": rows,
    }


def inspect_job(job_dir: Path, evallab_root: Path, *, prepared_dir: Path | None = None):
    """Read one completed Harbor job against stock read-only Eval Lab."""
    import evallab
    from evallab.results import load_trial

    repo = evallab_root.expanduser().resolve()
    loaded = Path(evallab.__file__).resolve() if evallab.__file__ else None
    if loaded is None or not loaded.is_relative_to(repo):
        raise ValueError("run the CLI with the Python environment of --evallab-root")
    raw_job = job_dir.expanduser()
    if raw_job.is_symlink() or not raw_job.is_dir():
        raise ValueError("--job must be an existing non-symlink directory")
    job_dir = raw_job.resolve()
    result_path = job_dir / "result.json"
    if result_path.is_symlink() or not result_path.is_file():
        raise ValueError("job directory is missing a regular result.json")
    job_raw = _loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(job_raw, dict):
        raise ValueError("job result.json must be a JSON object")
    job_id = team_census._normalize_uuid(job_raw.get("id"))
    n_total = job_raw.get("n_total_trials")
    valid_count = type(n_total) is int and n_total >= 0
    issues = []
    if job_id is None:
        issues.append("invalid_or_missing_job_id")
    if job_raw.get("finished_at") in (None, ""):
        issues.append("job_unfinished")
    if not valid_count:
        issues.append("invalid_n_total_trials")
    arm_views, arm_actions, plan_sha = _load_prepared(prepared_dir)
    trials = []
    names = sorted(job_dir.iterdir(), key=lambda item: item.name)
    for child in names:
        trial_result = child / "result.json"
        hidden = child.name.startswith(".") or child.is_symlink() or not child.is_dir()
        if hidden or trial_result.is_symlink() or not trial_result.is_file():
            continue
        trial_dir = child.resolve()
        try:
            probe = _loads(trial_result.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            trials.append(_blank(trial_dir, "unreadable_trial_result"))
            continue
        if isinstance(probe, dict) and "task_name" in probe and "trial_name" in probe:
            loaded_trial = _read_trial(
                trial_dir, job_id, load_trial, arm_views, arm_actions
            )
            trials.append(loaded_trial)
    if valid_count and len(trials) != n_total:
        issues.append("job_trial_count_mismatch")
    meta = {
        "discovered_trials": len(trials),
        "issues": sorted(set(issues)),
        "n_total_trials": n_total if valid_count else None,
        "native_job_id": job_id,
    }
    with TemporaryDirectory(prefix="seatlab-receipt-job-census-") as name:
        scratch = Path(name).resolve()
        census_rows, scanned = team_census._load_census_rows(repo, scratch, [job_dir])
        empty = not any(scratch.iterdir())
    return compare_job_census(
        meta,
        trials,
        census_rows,
        census_rows_scanned=scanned,
        derived_root_empty_after=empty,
        expected_arms=tuple(arm_views),
        prepared_plan_sha256=plan_sha,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--evallab-root", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        report = inspect_job(args.job, args.evallab_root, prepared_dir=args.prepared)
        encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.out is None:
            sys.stdout.write(encoded)
            return 0
        target = args.out.expanduser()
        if os.path.lexists(target):
            raise FileExistsError(f"--out already exists: {args.out}")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    except ImportError:
        parser.exit(2, f"{parser.prog}: use the Eval Lab Python environment\n")
    except (OSError, ValueError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
