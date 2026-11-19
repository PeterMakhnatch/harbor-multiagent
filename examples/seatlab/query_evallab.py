"""Query completed Dispatch Handoff evidence using Eval Lab's in-memory census."""

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID

# Aggregate the census candidates before attaching them to the outcome. The two
# seat records stay in separate columns on that outcome, never separate votes.
_JOIN_SQL = """
WITH uuid_matches AS (
    SELECT e.trial_id,
           count(t.trial_id) AS census_uuid_rows,
           count(*) FILTER (WHERE t.source_trial_dir = e.source_trial_dir) AS census_path_rows,
           count(*) FILTER (WHERE t.job_id = e.job_id) AS census_job_trial_rows,
           max(t.reward) FILTER (WHERE t.job_id = e.job_id) AS census_reward,
           bool_or(t.projection_error IS NOT NULL)
               FILTER (WHERE t.job_id = e.job_id) AS census_job_trial_projection_error
    FROM dispatch_evidence e LEFT JOIN trials t USING (trial_id)
    GROUP BY e.trial_id
), path_conflicts AS (
    SELECT DISTINCT e.trial_id
    FROM dispatch_evidence e JOIN trials t USING (source_trial_dir)
    WHERE t.trial_id IS DISTINCT FROM e.trial_id
       OR (e.job_id IS NOT NULL AND t.job_id IS DISTINCT FROM e.job_id)
)
SELECT e.* EXCLUDE (source_trial_dir), m.* EXCLUDE (trial_id),
       p.trial_id IS NOT NULL AS census_path_conflict
FROM dispatch_evidence e JOIN uuid_matches m USING (trial_id)
LEFT JOIN path_conflicts p USING (trial_id)
ORDER BY e.panel NULLS LAST, e.condition, e.seed, e.trial_id
"""

_CONDITION_SQL = """
SELECT panel, policy, condition, count(*) AS native_trials,
       sum(episodes)::BIGINT AS episodes,
       sum(optimal)::BIGINT AS optimal,
       sum(feasible)::BIGINT AS feasible,
       sum(stable_episodes)::BIGINT AS stable_episodes,
       sum(stable_optimal)::BIGINT AS stable_optimal,
       sum(stable_feasible)::BIGINT AS stable_feasible,
       sum(changed_episodes)::BIGINT AS changed_episodes,
       sum(changed_optimal)::BIGINT AS changed_optimal,
       sum(changed_feasible)::BIGINT AS changed_feasible,
       avg(reward) AS mean_trial_reward
FROM dispatch_evidence
GROUP BY panel, policy, condition
ORDER BY panel NULLS LAST, policy, condition
"""


def _panel_trials(root: Path) -> Iterator[tuple[Path, str, dict[str, Any]]]:
    if __package__:
        from .paired import read_panel
    else:
        from paired import read_panel

    panel = read_panel(root)
    # The paired reader verifies the plan, completion receipt and native world.
    # Use the receipt again only to recover the location of each verified trial.
    verified = {trial["trial_id"]: trial for trial in panel.trials}
    receipt = json.loads((root / "runs.json").read_text(encoding="utf-8"))
    for cell in receipt["cells"]:
        path = Path(cell["trial_path"])
        trial_path = (root / path).resolve()
        if path.is_absolute() or not trial_path.is_relative_to(root):
            raise ValueError("completion receipt points outside its panel")
        native = cell["evidence"]
        if verified.pop(native["trial_id"], None) != native:
            raise ValueError("completion receipt changed or reused a native trial UUID")
        yield trial_path, panel.policy, native
    if verified:
        raise ValueError("completion receipt no longer contains every verified trial")


def _job_uuid(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if parsed.int != 0 and str(parsed) == value:
                return value
    raise ValueError("native trial config has an invalid job UUID")


def _outcome(
    path: Path, policy: str | None, panel: int | None, evidence: dict[str, Any]
) -> dict[str, Any]:
    from evallab.results import load_trial

    native = load_trial(path)
    score = evidence["score"]
    reward = native.primary_reward
    if (
        native.id != evidence["trial_id"]
        or reward != score["optimal"] / score["episodes"]
    ):
        raise ValueError(
            "Eval Lab trial UUID or reward differs from verified dispatch evidence"
        )
    row = {
        "trial_id": native.id,
        # Only the trial's own native job UUID can prove a job+trial join.
        # A census path hit does not turn a standalone trial into a job.
        "job_id": _job_uuid(native.result["config"].get("job_id")),
        "source_trial_dir": str(path),
        "task": native.result.get("task_name"),
        "policy": policy,
        "panel": panel,
        "condition": evidence["condition"],
        "seed": evidence["seed"],
        "fingerprint": evidence["fingerprint"],
        "reward": reward,
        "episodes": score["episodes"],
        "optimal": score["optimal"],
        "feasible": score["feasible"],
        "inventory": evidence["seats"]["inventory"],
        "dispatch": evidence["seats"]["dispatch"],
    }
    for name, changed in (("stable", False), ("changed", True)):
        episodes = [
            episode for episode in score["rows"] if episode["changed"] == changed
        ]
        row[f"{name}_episodes"] = len(episodes)
        row[f"{name}_optimal"] = sum(episode["optimal"] for episode in episodes)
        row[f"{name}_feasible"] = sum(episode["feasible"] for episode in episodes)
    return row


def _outcomes(
    panels: Sequence[Path], trials: Sequence[Path], policy: str | None
) -> list[dict[str, Any]]:
    if __package__:
        from .evidence import read_trial
    else:
        from evidence import read_trial

    outcomes = []
    for number, root in enumerate(panels, start=1):
        outcomes.extend(
            _outcome(path, panel_policy, number, evidence)
            for path, panel_policy, evidence in _panel_trials(root)
        )
    outcomes.extend(_outcome(path, policy, None, read_trial(path)) for path in trials)
    if len({row["trial_id"] for row in outcomes}) != len(outcomes):
        raise ValueError("input selections reuse a native trial UUID")
    return outcomes


def _query(
    repo: Path, scratch: Path, roots: Sequence[Path], outcomes: list[dict[str, Any]]
) -> dict[str, Any]:
    import pyarrow as pa
    from evallab.storage.trials import connect_trials

    con, info = connect_trials(repo_root=repo, roots=roots, derived_root=scratch)
    with closing(con):
        table = pa.Table.from_pylist(outcomes)
        # All standalone trials have null job IDs; keep their key typed as text.
        job_id = table.schema.get_field_index("job_id")
        table = table.set_column(job_id, "job_id", table["job_id"].cast(pa.string()))
        con.register("dispatch_evidence", table)
        rows = con.execute(_JOIN_SQL).to_arrow_table().to_pylist()
        conditions = con.execute(_CONDITION_SQL).to_arrow_table().to_pylist()
        native_census_rows = info["n_rows"]
    for row in rows:
        if row.pop("census_path_conflict"):
            raise ValueError(
                f"{row['trial_id']}: census path has different native identity"
            )
        if row["census_job_trial_rows"] > 1:
            raise ValueError(
                f"{row['trial_id']}: census returned duplicate job+trial rows"
            )
        if row["census_reward"] is not None and row["census_reward"] != row["reward"]:
            raise ValueError(
                f"{row['trial_id']}: census reward differs from native reward"
            )
    return {
        "schema": "seatlab.evallab-query/v1",
        "counts": {
            "dispatch_evidence_trials": len(rows),
            "seat_usage_rows": 2 * len(rows),
            "census_rows_scanned": native_census_rows,
            "census_evidence_trials_by_uuid": sum(
                row["census_uuid_rows"] > 0 for row in rows
            ),
            "census_evidence_trials_by_path": sum(
                row["census_path_rows"] > 0 for row in rows
            ),
            "census_evidence_trials_by_job_and_uuid": sum(
                row["census_job_trial_rows"] == 1 for row in rows
            ),
            "census_evidence_rewards_verified": sum(
                row["census_reward"] is not None for row in rows
            ),
        },
        "dispatch_evidence": rows,
        "by_condition": conditions,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("panel", nargs="*", type=Path, help="completed panel directory")
    parser.add_argument(
        "--trial",
        action="append",
        type=Path,
        help="explicit native trial directory; repeatable",
    )
    parser.add_argument(
        "--policy", help="optional policy label for explicit trials only"
    )
    parser.add_argument(
        "--evallab-root",
        type=Path,
        required=True,
        help="checkout that provides the Eval Lab Python environment",
    )
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        required=True,
        help="existing empty directory for the isolated census projection root",
    )
    parser.add_argument(
        "--census-root",
        action="append",
        type=Path,
        help="explicit census scan root; repeatable. Defaults to the input panels and native trial directories.",
    )
    args = parser.parse_args(argv)
    if not args.panel and not args.trial:
        parser.error("provide a completed panel directory or --trial")
    if args.policy is not None and (not args.trial or not args.policy.strip()):
        parser.error("--policy requires a non-empty label and at least one --trial")

    try:
        import evallab

        repo = args.evallab_root.expanduser().resolve()
        scratch = args.scratch_dir.expanduser().resolve()
        if not evallab.__file__ or not Path(evallab.__file__).resolve().is_relative_to(
            repo
        ):
            raise ValueError(
                "run the CLI with the Python environment of --evallab-root"
            )
        if not scratch.is_dir() or any(scratch.iterdir()):
            raise ValueError("--scratch-dir must be an existing empty directory")
        panels = [path.expanduser().resolve() for path in args.panel]
        trials = [path.expanduser().resolve() for path in args.trial or ()]
        roots = [path.expanduser().resolve() for path in args.census_root or ()] or [
            *panels,
            *trials,
        ]
        if not all(root.is_dir() for root in roots):
            raise ValueError("every census scan root must be an existing directory")
        report = _query(repo, scratch, roots, _outcomes(panels, trials, args.policy))
    except ImportError as error:
        parser.exit(
            2,
            f"{parser.prog}: use the configured Eval Lab Python environment: {error}\n",
        )
    except (OSError, ValueError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
