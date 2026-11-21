import json
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from runpy import run_path
from sqlite3 import connect
from typing import Any
from uuid import UUID

from examples.seatlab import team_driver, team_report
from examples.seatlab.capsule import view_hash
from examples.seatlab.visibility import dispatch_mix_evidence as mix
from examples.seatlab.visibility import dispatch_mix_plan as plan
from examples.seatlab.visibility import spend


def _trial(root: Path, cell: plan.Cell) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    trial = team_driver._trial_dir(root, cell)
    config = team_driver._trial_config(root, {**cell, "pairing": "MM"})
    for participant in config["participants"]:
        role = participant["seat"]
        participant.update(
            import_path="examples.seatlab.visibility.dispatch_mix_agent:MixedModelSeat",
            model_name=plan.model_for(cell, role),
            kwargs={"run_id": cell["cell_id"]},
        )
    world = run_path(str(team_report._WORLD_PATH))["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": cell["seed"],
            "episodes": plan.EPISODES,
            "condition": cell["condition"],
        }
    )
    pairs = mix._native_pairs()
    records, receipts = [], {}
    optimal = 0
    for role in plan.ROLES:
        view = world.view(role)
        if role == "inventory":
            rows = view["cases"]
        else:
            rows = [{"id": item["id"], "bay": "A"} for item in view["cases"]]
            optimal = sum(
                "A" == max(case["after"], key=case["utilities"].__getitem__)
                for case in world.evidence()["cases"]
            )
        world.submit(role, {"rows": rows})
        provider, model = plan.model_for(cell, role).split("/", 1)
        digest = view_hash(view)
        receipts[role] = {"ack_sha256": digest}
        records.append(
            {
                "seat": role,
                "under_test": True,
                "exception_info": None,
                "agent_execution": {
                    "started_at": "2026-11-21T00:00:00Z",
                    "finished_at": "2026-11-21T00:00:01Z",
                },
                "agent_info": {
                    "name": "seatlab-mixed-model",
                    "version": "1.0.0",
                    "model_info": {"name": model, "provider": provider},
                },
                "agent_result": {
                    "n_input_tokens": 10,
                    "n_cache_tokens": 2,
                    "n_output_tokens": 3,
                    "metadata": {
                        "schema": pairs._SEAT_SCHEMA,
                        "stage": "complete",
                        "model_turns": 1,
                        "run_id": cell["cell_id"],
                        "role": role,
                        "requested_model": model,
                        "served_model": model,
                        "view_sha256": digest,
                        "prompt_sha256": pairs._prompt_hash(
                            pairs._prompt(role, view["cases"])
                        ),
                        "decision_sha256": pairs._decision_hash(role, rows),
                    },
                },
            }
        )
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({**world.evidence(), "observation_receipts": receipts})
    )
    native = {
        "id": str(UUID(int=1 + plan.grid().index(cell))),
        "trial_name": trial.name,
        "trial_uri": trial.as_uri(),
        "task_id": {"path": config["task"]["path"]},
        "config": config,
        "finished_at": "2026-11-21T00:00:01Z",
        "exception_info": None,
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": optimal / plan.EPISODES}},
        "seat_results": records,
    }
    _save(trial, native)
    return trial, config, native


def _save(trial: Path, native: dict[str, Any]) -> None:
    (trial / "result.json").write_text(json.dumps(native))


def test_mixed_evidence_rejects_wrong_assignment_in_each_arm(tmp_path: Path) -> None:
    for cell in plan.grid()[:4]:
        trial, config, native = _trial(tmp_path, cell)
        row = mix.inspect_cell(tmp_path, cell, config, 0)
        assert row["status"] == "valid"
        assert row["inventory_exact"] == 6
        assert row["native_score"] == native["verifier_result"]["rewards"]["reward"]
        assert len(row["actions"]) == 6
        left, right = native["seat_results"]
        left["agent_info"], right["agent_info"] = (
            right["agent_info"],
            left["agent_info"],
        )
        _save(trial, native)
        row = mix.inspect_cell(tmp_path, cell, config, 0)
        assert row["status"] == "invalid_evidence"


def test_mixed_evidence_rejects_foreign_scope_and_artifact_alias(
    tmp_path: Path,
) -> None:
    cell = plan.grid()[2]
    trial, config, native = _trial(tmp_path, cell)
    metadata = native["seat_results"][1]["agent_result"]["metadata"]
    original = metadata["prompt_sha256"]
    other_scope = native["seat_results"][0]["agent_result"]["metadata"]
    metadata["prompt_sha256"] = other_scope["prompt_sha256"]
    _save(trial, native)
    row = mix.inspect_cell(tmp_path, cell, config, 0)
    assert row["status"] == "invalid_evidence"
    assert row["seats"]["dispatch"]["prompt_sha256"] is None
    assert row["native_score"] == native["verifier_result"]["rewards"]["reward"]

    metadata["prompt_sha256"] = original
    _save(trial, native)
    artifact = trial / "artifacts/logs/world/handoff.json"
    alias = tmp_path / "other-cell.json"
    artifact.rename(alias)
    artifact.symlink_to(alias)
    row = mix.inspect_cell(tmp_path, cell, config, 0)
    assert row["status"] == "invalid_evidence"
    assert row["native_score"] is None


def test_mixed_evidence_rejects_an_unverified_reward(tmp_path: Path) -> None:
    cell = plan.grid()[0]
    trial, config, native = _trial(tmp_path, cell)
    rewards = native["verifier_result"]["rewards"]
    rewards["reward"] = 0.0 if rewards["reward"] != 0.0 else 1.0
    _save(trial, native)
    row = mix.inspect_cell(tmp_path, cell, config, 0)
    assert row["status"] == "invalid_evidence"
    assert row["native_score"] is None


def test_mixed_evidence_never_invents_missing_output_or_charge(tmp_path: Path) -> None:
    cell = plan.grid()[0]
    root = tmp_path / "study"
    assert mix.inspect_cell(root, cell, {}, None)["status"] == "missing"
    trial, config, native = _trial(root, cell)
    native["seat_results"][0]["agent_result"]["metadata"] = None
    _save(trial, native)
    row = mix.inspect_cell(root, cell, config, 0)
    assert row["status"] == "incomplete"
    assert row["native_score"] == native["verifier_result"]["rewards"]["reward"]

    native["seat_results"][0]["agent_result"] = None
    _save(trial, native)
    row = mix.inspect_cell(root, cell, config, 0)
    assert row["status"] == "incomplete"
    assert row["native_score"] == native["verifier_result"]["rewards"]["reward"]
    assert row["seats"]["inventory"]["n_input_tokens"] is None

    (trial / "artifacts/logs/world/handoff.json").unlink()
    row = mix.inspect_cell(root, cell, config, 0)
    assert row["status"] == "incomplete"
    assert row["native_score"] is None
    assert row["inventory_exact"] is None
    assert row["actions"] is None

    ledger = mix.reconcile([row], tmp_path / "missing-ledger")
    assert ledger["matched"] == 0
    assert ledger["unresolved"] == 1
    assert ledger["known_usd_low"] is None
    assert ledger["upper_usd_including_holds"] is None


def test_mixed_ledger_matches_role_identity_and_tokens_only(tmp_path: Path) -> None:
    cell, second = plan.grid()[:2]
    root = tmp_path / "study"
    _, config, _ = _trial(root, cell)
    row = mix.inspect_cell(root, cell, config, 0)
    other = mix.inspect_cell(root, second, {}, None)
    directory = tmp_path / "spend"
    directory.mkdir(mode=0o700)
    spend.initialize(directory, Decimal("10"), Decimal("1"))
    model = "deepseek-flash"
    date = "2026-11-21T00:00:00+00:00"
    file = directory / "direct-flash.sqlite3"
    with closing(connect(file)) as db, db:
        db.executemany(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    cell["cell_id"],
                    "dispatch",
                    date,
                    date,
                    date,
                    model,
                    2,
                    8,
                    3,
                    3006,
                    3006,
                ),
                (
                    cell["cell_id"],
                    "inventory",
                    date,
                    date,
                    date,
                    model,
                    2,
                    8,
                    3,
                    3006,
                    3006,
                ),
                (
                    second["cell_id"],
                    "inventory",
                    date,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "other-study",
                    "dispatch",
                    date,
                    date,
                    date,
                    model,
                    2,
                    8,
                    3,
                    3006,
                    3006,
                ),
            ],
        )
    assert mix.reconcile([row, other], directory) == {
        "ledger_status": "available",
        "matched": 1,
        "unexpected": 1,
        "unresolved": 1,
        "known_usd_low": "0.000006012",
        "upper_usd_including_holds": "0.010006012",
    }
    with closing(connect(file)) as db, db:
        db.execute(
            "UPDATE calls SET hit=1, miss=9, output=3, cost_low=3153, cost_high=3153 "
            "WHERE run=? AND role='dispatch'",
            (cell["cell_id"],),
        )
    ledger = mix.reconcile([row, other], directory)
    assert ledger["matched"] == 0
    assert ledger["unresolved"] == 2
