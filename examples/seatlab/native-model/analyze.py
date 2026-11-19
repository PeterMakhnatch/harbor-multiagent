"""Audit the registered native 2×2 handoff experiment. Never runs a trial or a model.

Use the collection's source revision. Stdout contains counts, fixed labels, opaque
identities and fingerprints only. Native verifier scores on failed worlds are
reported separately and never enter the measured team denominator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import claude_replay as provider  # noqa: E402
import collect  # noqa: E402
import evidence  # noqa: E402
import run_panel  # noqa: E402
from capsule import encode_view  # noqa: E402

ROLES = ("inventory", "dispatch")
STAGES = frozenset(
    "start generating submitting complete not_native_seat subscription_unverified generation_timeout cli_launch_failed model_unattested session_unattested budget_estimate_exceeded invalid_decisions submission_rejected unmeasured_seat unexpected_error budget_exhausted refused rate_limited unfinished cli_failed invalid_cli_result auth_unavailable cli_unavailable invalid_configuration".split()
)


def require(condition: object, problem: str) -> None:
    if not condition:
        raise ValueError(problem)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def obj(path: Path) -> dict[str, Any]:
    return provider._json_object(path.read_bytes())


def only_digest(value: Any) -> str | None:
    require(
        value is None
        or (isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)),
        "unrecognized digest",
    )
    return value


def number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def registration(root: Path) -> tuple[str, list[tuple[dict, dict]]]:
    raw = (root / "plan.json").read_bytes()
    plan = provider._json_object(raw)
    ledger = obj(root / "attempts.json")
    pin = sha(raw)
    require(
        ledger["plan_sha256"] == obj(root / "started.json")["plan_sha256"] == pin,
        "plan and ledgers disagree",
    )
    cells = [
        dict(
            position=i,
            **collect._cell(seed, team),
            team=team,
            roles=collect._roles(team),
        )
        for i, (seed, team) in enumerate(collect._order(), 1)
    ]
    require(
        plan["schema"] == collect.PLAN_SCHEMA
        and plan["condition"] == collect.CONDITION
        and plan["episodes"] == collect.EPISODES
        and plan["seeds"] == list(collect.SEEDS)
        and plan["teams"] == list(collect.TEAMS)
        and plan["cells"] == cells
        and plan["retries"] == 0
        and plan["import_path"] == collect.IMPORT_PATH
        and plan["launch_timeout_seconds"] == collect.LAUNCH_TIMEOUT_SECONDS
        and plan["role_policies"] == {t: collect._roles(t) for t in collect.TEAMS},
        "registered design changed",
    )
    require(
        plan["native"]["olam_base"] == run_panel.OLAM_BASE
        and plan["native"]["expected_version"] == run_panel.NATIVE_VERSION
        and only_digest(plan["native"]["harbor_executable_sha256"]),
        "unrecognized native version",
    )
    sources = run_panel._snapshot(collect.SOURCE_POLICY)
    for helper in ("claude_replay.py", "capsule.py", "paired.py"):
        sources[f"seatlab/{helper}"] = (HERE.parent / helper).read_bytes()
    require(
        plan["sha256"]
        == {
            "actor": {"native-model/native_actor.py": sha(collect.ACTOR.read_bytes())},
            "collector": {
                "native-model/collect.py": sha(collect.COLLECTOR.read_bytes())
            },
            "task_and_adapter": {k: sha(v) for k, v in sources.items()},
        },
        "wrong collection source revision",
    )
    rows = ledger["attempts"]
    require(
        ledger["schema"] == collect.ATTEMPTS_SCHEMA
        and ledger["preflight"] == "verified"
        and len(rows) == len(cells)
        and type(ledger["complete"]) is bool
        and ledger["complete"] == all(r["attempted"] is True for r in rows),
        "attempt ledger mismatch",
    )
    return pin, list(zip(cells, rows, strict=True))


def project(world: dict[str, Any]) -> tuple[dict, dict, dict]:
    # The independent world scorer has already checked the complete dossier,
    # delivery order, accepted reports, local case IDs and admissible bay values.
    cases = world["cases"]
    condition = world["settings"]["condition"]
    views = {r: {"role": r, "condition": condition, "cases": []} for r in ROLES}
    rows = {r: [] for r in ROLES}
    for c in cases:
        i = c["id"]
        reports = [
            {"revision": j, "available": c[k]} for j, k in ((1, "before"), (2, "after"))
        ]
        views["inventory"]["cases"].append({"id": i, "reports": reports})
        views["dispatch"]["cases"].append(
            {
                "id": i,
                "utilities": c["utilities"],
                "messages": c["visible"],
                "pooled_available": None,
            }
        )
        rows["inventory"].append({"id": i, "reports": c["reports"]})
        rows["dispatch"].append({"id": i, "bay": c["chosen"]})
    stats = {
        "accepted_reports": sum(len(c["reports"]) for c in cases),
        "delivered_messages": sum(len(c["visible"]) for c in cases),
        "delivered_equal_accepted": sum(
            m == c["reports"][m["revision"] - 1] for c in cases for m in c["visible"]
        ),
        "truthful_pairs": sum(
            c["reports"][0]["available"] == c["before"]
            and c["reports"][1]["available"] == c["after"]
            for c in cases
        ),
    }
    return (
        {r: encode_view(v) for r, v in views.items()},
        {r: sha(encode_view({"rows": v})) for r, v in rows.items()},
        stats,
    )


def seat(
    record: dict | None,
    role: str,
    policy: str,
    view: bytes | None,
    accepted: str | None,
) -> dict:
    fields = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")
    result = (record or {}).get("agent_result") or {}
    meta = result.get("metadata")
    model = policy == "first-party"
    out = {
        "policy": policy,
        "status": "unmeasured",
        "stage": None,
        "model_turns": None,
        "served_model": None,
        "view_sha256": None,
        "view_bytes": None,
        "model_view_sha256": None,
        "accepted_submission_sha256": None,
        "view_matches_world": None,
        "accepted_matches_world": None,
        "usage": None,
    }
    if record is None or meta is None:
        return out
    require(
        record["under_test"] is True
        and record["agent_info"]["name"] == "seatlab-native-handoff"
        and record["agent_info"]["version"] == "1.0.0",
        "wrong native participant",
    )
    require(
        meta["schema"] == "seatlab.native-handoff-seat/v1"
        and meta["role"] in (None, role)
        and meta["policy"] == policy,
        "seat identity mismatch",
    )
    stage = meta["stage"]
    out["stage"] = stage if stage in STAGES else "other_failure"
    turns = meta["model_turns"]
    require(type(turns) is int and turns in (0, 1), "invalid generation count")
    out["model_turns"] = turns
    size = meta["view_bytes"]
    require(size is None or (type(size) is int and size >= 0), "invalid view size")
    out["view_bytes"] = size
    for k in ("view_sha256", "model_view_sha256", "accepted_submission_sha256"):
        out[k] = only_digest(meta[k])
    if view is not None and out["view_sha256"] is not None:
        out["view_matches_world"] = (
            sha(view) == out["view_sha256"] and len(view) == size
        )
    if accepted is not None and out["accepted_submission_sha256"] is not None:
        out["accepted_matches_world"] = accepted == out["accepted_submission_sha256"]
    served = meta["served_model"]
    require(served in (None, provider._MODEL), "unexpected served model")
    out["served_model"] = served
    usage = meta["telemetry"]
    if model and usage is not None:
        require(
            isinstance(usage, dict) and set(usage) == set(provider._TELEMETRY),
            "invalid model telemetry",
        )
        for k, v in usage.items():
            require(
                v is None
                or (
                    number(v)
                    if k == "estimated_cost_usd"
                    else type(v) is int and v >= 0
                ),
                "bad telemetry value",
            )
        out["usage"] = {k: usage[k] for k in provider._TELEMETRY}
    if not model:
        require(
            turns == 0
            and usage is None
            and served is None
            and out["model_view_sha256"] is None,
            "model telemetry on a script",
        )
    if stage != "complete" or record.get("exception_info") is not None:
        return out
    if model:
        info = record["agent_info"].get("model_info") or {}
        ident = info.get("name")
        vendor = info.get("provider")
        cost = (usage or {}).get("estimated_cost_usd")
        require(
            turns == 1
            and served == provider._MODEL
            and ident in (provider._MODEL, "anthropic/" + provider._MODEL)
            and vendor in ("anthropic", None)
            and out["model_view_sha256"] == out["view_sha256"]
            and out["model_view_sha256"] is not None
            and usage is not None
            and (cost is None or cost <= float(provider._BUDGET_USD)),
            "model attestation mismatch",
        )
    else:
        require(
            all(type(result.get(k)) in (float, int) and result[k] == 0 for k in fields),
            "script claimed model usage",
        )
    require(
        meta["role"] == role
        and all(
            out[k] is not None
            for k in ("view_sha256", "view_bytes", "accepted_submission_sha256")
        ),
        "incomplete successful seat",
    )
    if out["view_matches_world"] is False or out["accepted_matches_world"] is False:
        raise ValueError("world and accepted seat evidence disagree")
    out["status"] = (
        "verified"
        if out["view_matches_world"] is True and out["accepted_matches_world"] is True
        else "accepted_not_world_verified"
    )
    return out


def attempt(root: Path, cell: dict, row: dict) -> dict:
    roles = cell["roles"]
    require(
        all(row[k] == cell[k] for k in ("position", "cell_id", "seed", "team", "roles"))
        and type(row["attempted"]) is bool,
        "cell coordinate mismatch",
    )
    blank = {
        "cell_id": cell["cell_id"],
        "seed": cell["seed"],
        "team": cell["team"],
        "attempted": row["attempted"],
        "trial_id": None,
        "state": "not_attempted",
        "native_reward": None,
        "measured": None,
        "world": None,
        "case_sha256": None,
        "seats": {r: seat(None, r, roles[r], None, None) for r in ROLES},
    }
    if not row["attempted"]:
        return blank
    rel = f"cells/{cell['cell_id']}"
    require(
        row["cell_path"] == rel and row["receipt_path"] == rel + "/receipt.json",
        "cell path mismatch",
    )
    receipt = obj(root / rel / "receipt.json")
    require(
        all(
            row[k] == receipt[k]
            for k in (
                "position",
                "cell_id",
                "returncode",
                "attempted",
                "timed_out",
                "launch_error",
            )
        ),
        "launch receipt mismatch",
    )
    config = obj(root / rel / "trial.json")
    name = config["trial_name"]
    require(
        isinstance(name, str)
        and re.fullmatch("seatlab-[0-9a-f]{24}", name)
        and row["trial_path"] == f"{rel}/native/{name}"
        and config["participants"] == collect._participants(cell["team"]),
        "native registration mismatch",
    )
    trial = root / row["trial_path"]
    require(
        not trial.is_symlink() and trial.resolve() == trial, "symlink under trial root"
    )
    result = trial / "result.json"
    blank["state"] = "no_native_result"
    if not result.is_file():
        return blank
    native = obj(result)
    blank["trial_id"] = evidence._trial_id(native)
    native_cfg = native["config"]
    require(
        native_cfg["trial_name"] == name
        and run_panel._config_matches(
            native_cfg["participants"], config["participants"]
        ),
        "native arm mismatch",
    )
    reward = ((native.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    require(reward is None or number(reward), "invalid native reward")
    blank["native_reward"] = reward
    blank["state"] = "unmeasured_native"
    views = {}
    accepted = {}
    try:
        checked, world = evidence.read_trial_with_world(trial)
    except (ValueError, OSError, TypeError, KeyError):
        pass
    else:
        score = checked["score"]
        require(
            checked["seed"] == cell["seed"]
            and checked["condition"] == collect.CONDITION
            and checked["trial_id"] == blank["trial_id"]
            and score["episodes"] == collect.EPISODES,
            "world does not belong to planned cell",
        )
        views, accepted, blank["world"] = project(world)
        blank["case_sha256"] = checked["fingerprint"]
        blank["state"] = "native_complete"
        blank["measured"] = {k: score[k] for k in ("episodes", "optimal", "feasible")}
    entries = {}
    for rec in native.get("seat_results") or []:
        r = rec["seat"]
        require(r in roles and r not in entries, "invalid seat map")
        entries[r] = rec
    blank["seats"] = {
        r: seat(entries.get(r), r, roles[r], views.get(r), accepted.get(r))
        for r in ROLES
    }
    if blank["state"] == "native_complete" and all(
        se["status"] == "verified" for se in blank["seats"].values()
    ):
        blank["state"] = "verified"
    return blank


def analyze(root: Path) -> dict:
    root = root.resolve(strict=True)
    pin, cells = registration(root)
    runs = [attempt(root, c, r) for c, r in cells]
    ids = [r["trial_id"] for r in runs if r["trial_id"] is not None]
    require(len(ids) == len(set(ids)), "reused native UUID")
    for seed in collect.SEEDS:
        require(
            len(
                {
                    r["case_sha256"]
                    for r in runs
                    if r["seed"] == seed and r["case_sha256"]
                }
            )
            <= 1,
            "seed selects different latent cases across teams",
        )
    teams = {}
    for team in collect.TEAMS:
        group = [r for r in runs if r["team"] == team]
        good = [r for r in group if r["measured"] is not None]
        teams[team] = {
            "roles": collect._roles(team),
            "planned": len(group),
            "native_measured": len(good),
            "both_seats_verified": sum(r["state"] == "verified" for r in group),
            **{
                k: sum(r["measured"][k] for r in good) if good else None
                for k in ("episodes", "optimal", "feasible")
            },
        }
    model = [
        se for r in runs for se in r["seats"].values() if se["policy"] == "first-party"
    ]
    gens = [se for se in model if se["model_turns"] == 1]
    costs = [
        se["usage"]["estimated_cost_usd"]
        for se in gens
        if se["usage"] is not None and se["usage"]["estimated_cost_usd"] is not None
    ]
    verified = sum(r["state"] == "verified" for r in runs)
    return {
        "schema": "seatlab.native-model-analysis/v1",
        "plan_sha256": pin,
        "registered_worlds": len(runs),
        "launched": sum(r["attempted"] for r in runs),
        "distinct_native_trials": len(ids),
        "verified_worlds": verified,
        "registered_model_seats": len(model),
        "observed_model_generations": len(gens),
        "unknown_generation_counts": sum(se["model_turns"] is None for se in model),
        "verified_model_seats": sum(s["status"] == "verified" for s in model),
        "subscription_cost_estimate": {
            "known_generations": len(costs),
            "unknown_for_observed_generations": len(gens) - len(costs),
            "sum_known_usd": round(sum(costs), 6) if costs else None,
            "cap_usd_per_generation": float(provider._BUDGET_USD),
            "is_invoice": False,
        },
        "teams": teams,
        "attempts": runs,
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("collection", type=Path)
    args = cli.parse_args()
    try:
        report = analyze(args.collection)
    except (KeyError, ValueError, TypeError, OSError, RecursionError):
        cli.exit(
            2, "analyze: invalid or incompatible collection; no report produced.\n"
        )
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
