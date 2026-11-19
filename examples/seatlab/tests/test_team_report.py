import importlib
import json
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import uuid4

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    driver = importlib.import_module("team_driver")
    report = importlib.import_module("team_report")
    capsule = importlib.import_module("capsule")

_WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "tasks/dispatch-handoff/environment/seatworld.py"
)
World = run_path(str(_WORLD_PATH))["World"]


@pytest.fixture
def study(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = (tmp_path / "study").resolve()
    return root, driver._register(root)


def _cell(
    plan: dict[str, Any], pairing: str, *, seed: int = 2001, condition: str = "fresh"
) -> dict[str, Any]:
    return next(
        cell
        for cell in plan["cells"]
        if (cell["seed"], cell["condition"], cell["pairing"])
        == (seed, condition, pairing)
    )


def _attempt(root: Path, plan: dict[str, Any], cell: dict[str, Any]) -> Path:
    trial = driver._trial_dir(root, cell)
    driver._seal(
        trial.parent.parent / "attempt.json",
        driver._bytes(driver._attempt(plan, cell)),
    )
    trial.parent.mkdir(mode=0o700)
    trial.mkdir(mode=0o700)
    return trial


def _seat(role: str, actor: str, digest: str) -> dict[str, Any]:
    model = actor == "M"
    context: dict[str, Any] = {
        "n_input_tokens": 9 if model else 0,
        "n_cache_tokens": 2 if model else 0,
        "n_output_tokens": 3 if model else 0,
        "cost_usd": 0.12 if model else 0.0,
    }
    if model:
        context["model_usage"] = {driver.MODEL: dict(context)}
        context["metadata"] = {
            "schema": "seatlab.native-model-seat/v1",
            "role": role,
            "model_turns": 1,
            "requested_model": "claude-sonnet-5-5",
            "served_model": "claude-sonnet-5-5",
            "stage": "complete",
            "view_sha256": digest,
            "telemetry": {
                "input_tokens": 7,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 0,
                "output_tokens": 3,
                "duration_ms": 207,
                "duration_api_ms": 200,
                "estimated_cost_usd": 0.12,
            },
        }
    else:
        context["metadata"] = {
            "schema": "seatlab.seat/v1",
            "policy": "arrival",
            "role": role,
            "decision_source": "script",
            "model_turns": 0,
        }
    return {
        "seat": role,
        "under_test": True,
        "exception_info": None,
        "agent_execution": {"started_at": "start", "finished_at": "finish"},
        "agent_info": {
            "name": "seatlab-first-party" if model else "seatlab-dispatch-handoff",
            "version": "1.0.0",
            "model_info": {"name": "claude-sonnet-5-5", "provider": "anthropic"}
            if model
            else None,
        },
        "agent_result": context,
    }


class NativeFixture:
    def __init__(
        self,
        root: Path,
        plan: dict[str, Any],
        cell: dict[str, Any],
        *,
        wrong_report: int | None = None,
        world_seed: int | None = None,
        returncode: int | None = None,
    ) -> None:
        self.trial = _attempt(root, plan, cell)
        if returncode is not None:
            driver._seal(
                self.trial.parent.parent / "outcome.json",
                driver._bytes({"returncode": returncode, "launch_error": None}),
            )
        world = World(
            {
                "schema": "seatlab.scenario/v1",
                "episodes": plan["episodes"],
                "seed": cell["seed"] if world_seed is None else world_seed,
                "condition": cell["condition"],
            }
        )
        inventory = world.view("inventory")
        acks = {"inventory": capsule.view_hash(inventory)}
        if wrong_report is not None:
            revision = inventory["cases"][0]["reports"][wrong_report - 1]
            revision["available"] = sorted(set("ABCD") - set(revision["available"]))
        world.submit("inventory", {"rows": inventory["cases"]})
        dispatch = world.view("dispatch")
        acks["dispatch"] = capsule.view_hash(dispatch)
        world.submit(
            "dispatch",
            {
                "rows": [
                    {
                        "id": case["id"],
                        "bay": max(
                            case["messages"][-1]["available"],
                            key=case["utilities"].__getitem__,
                        )
                        if cell["pairing"][1] == "S"
                        else "A",
                    }
                    for case in dispatch["cases"]
                ]
            },
        )
        self.world = world.evidence()
        # Let a substituted latent world claim the registered seed.
        self.world["settings"]["seed"] = cell["seed"]
        self.world["observation_receipts"] = {
            role: {"ack_sha256": digest} for role, digest in acks.items()
        }
        optimal = sum(
            case["chosen"] == max(case["after"], key=case["utilities"].__getitem__)
            for case in self.world["cases"]
        )
        self.native = {
            "schema_version": 1,
            "id": str(uuid4()),
            "config": driver._trial_config(root, cell),
            "trial_name": self.trial.name,
            "trial_uri": self.trial.as_uri(),
            "finished_at": "finish",
            "reward_valid": True,
            "exception_info": None,
            "verifier_result": {"rewards": {"reward": optimal / plan["episodes"]}},
            "seat_results": [
                _seat(role, actor, acks[role])
                for role, actor in zip(acks, cell["pairing"], strict=True)
            ],
        }
        self.native["seat_results"].reverse()

    def write(self) -> None:
        dossier = self.trial / "artifacts/logs/world/handoff.json"
        dossier.parent.mkdir(parents=True, exist_ok=True)
        dossier.write_bytes(driver._bytes(self.world))
        (self.trial / "result.json").write_bytes(driver._bytes(self.native))

    def seat(self, role: str) -> dict[str, Any]:
        return next(
            seat for seat in self.native["seat_results"] if seat["seat"] == role
        )


def _comparison(
    result: dict[str, Any], kind: str, name: str, seed: int, condition: str
) -> dict[str, Any]:
    return next(
        contrast
        for contrast in result[f"{kind}_comparisons"]
        if (contrast["contrast"], contrast["seed"], contrast["condition"])
        == (name, seed, condition)
    )


def test_collect_pairs_same_seed_and_condition_and_checks_actual_delivery(
    study,
) -> None:
    root, plan = study
    for condition in ("fresh", "reordered"):
        for pairing in ("SS", "SM", "MS", "MM"):
            cell = _cell(plan, pairing, condition=condition)
            NativeFixture(
                root,
                plan,
                cell,
                wrong_report=1 if pairing == "MS" else None,
                returncode=7 if pairing == "SS" else None,
            ).write()
    # A matching condition at a different seed cannot complete a pair.
    NativeFixture(root, plan, _cell(plan, "MM", seed=2003)).write()
    result = report.collect(root)
    rows = {row["cell_id"]: row for row in result["cells"]}
    assert result["counts"] == {
        "registered": 16,
        "attempted": 9,
        "measured": 9,
        "unmeasured": 0,
        "invalid": 0,
        "not_attempted": 7,
        "by_model_actors": {
            "0": {"registered": 4, "attempted": 2, "measured": 2},
            "1": {"registered": 8, "attempted": 4, "measured": 4},
            "2": {"registered": 4, "attempted": 3, "measured": 3},
        },
    }
    assert not result["complete"]
    assert str(root) not in json.dumps(result, allow_nan=False)
    ss = rows["s2001-fresh-SS"]
    sm = rows["s2001-fresh-SM"]
    assert ss["native_returncode"] == 7
    assert ss["seats"]["dispatch"]["model_turns"] == 0
    assert ss["seats"]["dispatch"]["telemetry"]["output_tokens"] == 0
    assert sm["seats"]["dispatch"]["model_turns"] == 1
    assert sm["seats"]["dispatch"]["telemetry"]["cache_creation_input_tokens"] == 0
    assert (
        sm["seats"]["dispatch"]["accepted_view_sha256"]
        == ss["seats"]["dispatch"]["accepted_view_sha256"]
    )
    assert rows["s2001-fresh-MS"]["metrics"]["inventory_copy_fidelity"] == {
        "revision_1_correct": 11,
        "revision_2_correct": 12,
        "both_correct": 11,
        "revision_reports": 24,
    }
    listener = _comparison(result, "listener", "SS_to_SM", 2001, "fresh")
    assert listener["status"] == "compared"
    assert listener["differences"]["choices_changed"] == sum(
        item["choice"] != "A" for item in ss["metrics"]["rows"]
    )
    for metric in ("optimal", "feasible"):
        assert listener["differences"][f"{metric}_delta"] == (
            sm["metrics"][metric] - ss["metrics"][metric]
        )
    assert (
        _comparison(result, "listener", "MS_to_MM", 2001, "fresh")["status"]
        == "compared"
    )
    excluded = _comparison(result, "listener", "MS_to_MM", 2001, "reordered")
    assert excluded["status"] == "excluded_messages_changed"
    assert excluded["messages_changed_episode_ids"] == [0]
    assert excluded["differences"] is None
    speaker = _comparison(result, "speaker", "SS_to_MS", 2001, "reordered")
    assert speaker["status"] == "compared"
    assert speaker["messages_changed_episode_ids"] == [0]
    unmatched = _comparison(result, "listener", "MS_to_MM", 2003, "fresh")
    assert unmatched["status"] == "unavailable"
    assert len(result["listener_comparisons"]) == 8
    assert result["usage"]["model_turns_recorded"] == 10
    assert result["usage"]["known_cli_estimate_usd"] == pytest.approx(1.2)


@pytest.mark.parametrize(
    "defect",
    (
        "missing_receipts",
        "missing_model_answer",
        "wrong_world",
        "wrong_model",
        "wrong_turns",
        "wrong_role",
        "wrong_digest",
        "extra_native_model",
        "budget_violation",
        "script_calls_model",
        "wrong_agent",
        "config_override",
        "inconsistent_reward",
        "script_reports_wrong",
        "wrong_uuid",
    ),
)
def test_rejects_mismatched_native_proofs(study, defect: str) -> None:
    root, plan = study
    cell = _cell(plan, "SM")
    native = NativeFixture(
        root,
        plan,
        cell,
        world_seed=2003 if defect == "wrong_world" else None,
        wrong_report=1 if defect == "script_reports_wrong" else None,
    )
    dispatch = native.seat("dispatch")["agent_result"]
    metadata = dispatch["metadata"]
    if defect == "missing_receipts":
        native.world.pop("observation_receipts")
    elif defect == "missing_model_answer":
        native.seat("dispatch")["agent_result"] = None
    elif defect == "wrong_model":
        metadata["served_model"] = "unexpected-model"
    elif defect == "wrong_turns":
        metadata["model_turns"] = 2
    elif defect == "wrong_role":
        metadata["role"] = "inventory"
    elif defect == "wrong_digest":
        metadata["view_sha256"] = "0" * 64
    elif defect == "budget_violation":
        dispatch["cost_usd"] = 0.31
        dispatch["model_usage"][driver.MODEL]["cost_usd"] = 0.31
        metadata["telemetry"]["estimated_cost_usd"] = 0.31
    elif defect == "extra_native_model":
        dispatch["model_usage"]["unexpected-model"] = dispatch["model_usage"][
            driver.MODEL
        ]
    elif defect == "script_calls_model":
        native.seat("inventory")["agent_result"]["metadata"]["model_turns"] = 1
    elif defect == "wrong_agent":
        native.seat("dispatch")["agent_info"]["name"] = "seatlab-dispatch-handoff"
    elif defect == "config_override":
        native.native["config"]["participants"][0]["kwargs"]["other"] = "bad"
    elif defect == "inconsistent_reward":
        native.native["verifier_result"]["rewards"]["reward"] = -1
    elif defect == "wrong_uuid":
        native.native["id"] = "not-a-native-uuid"
    native.write()
    result = report.inspect_cell(root, plan, cell)
    assert result["status"] == "invalid"
    assert result["metrics"] is None
    assert all(
        seat["accepted_view_sha256"] is None for seat in result["seats"].values()
    )


def test_failed_and_missing_seats_do_not_count_as_zero_measurements(study) -> None:
    root, plan = study
    refused_cell = _cell(plan, "SM")
    refused = NativeFixture(root, plan, refused_cell)
    record = refused.seat("dispatch")
    record["exception_info"] = {"exception_message": str(root)}
    metadata = record["agent_result"]["metadata"]
    metadata.update(stage="refused", view_sha256=None)
    refused.native.update(reward_valid=False, verifier_result=None)
    refused.write()

    grade_cell = _cell(plan, "MS")
    no_grade = NativeFixture(root, plan, grade_cell)
    no_grade.native.update(reward_valid=False, verifier_result=None)
    no_grade.write()
    missing_cell = _cell(plan, "MM")
    _attempt(root, plan, missing_cell)

    result = report.collect(root)
    rows = {row["cell_id"]: row for row in result["cells"]}
    row = rows[refused_cell["cell_id"]]
    assert row["status"] == "invalid"
    assert row["error"] == "model_refused"
    assert row["metrics"] is None
    assert row["seats"]["dispatch"]["telemetry"]["estimated_cost_usd"] == 0.12
    assert rows[grade_cell["cell_id"]]["error"] == "native_grade_missing"
    missing = rows[missing_cell["cell_id"]]
    assert missing["status"] == "unmeasured"
    assert missing["seats"]["inventory"]["model_turns"] is None
    assert missing["seats"]["inventory"]["telemetry"]["input_tokens"] is None
    assert result["counts"]["measured"] == 0
    assert result["usage"]["known_cli_estimate_usd"] == pytest.approx(0.24)
    assert result["usage"]["attempted_model_seats_with_unknown_turn_count"] == 2
    assert str(root) not in json.dumps(result)


def test_missing_model_answer_with_partial_telemetry_is_not_measured(study) -> None:
    root, plan = study
    cell = _cell(plan, "SM")
    native = NativeFixture(root, plan, cell)
    context = native.seat("dispatch")["agent_result"]
    context.update(n_output_tokens=None, cost_usd=None, model_usage=None)
    metadata = context["metadata"]
    metadata.update(stage="telemetry_invalid", view_sha256=None)
    metadata["telemetry"].update(output_tokens=None, estimated_cost_usd=None)
    native.write()
    result = report.collect(root)
    row = next(row for row in result["cells"] if row["cell_id"] == cell["cell_id"])
    assert row["error"] == "model_telemetry_invalid"
    assert row["metrics"] is None
    assert row["seats"]["dispatch"]["telemetry"]["output_tokens"] is None
    assert result["usage"]["model_calls_with_unknown_cli_estimate"] == 1
    assert result["usage"]["known_cli_estimate_usd"] is None


def test_duplicate_and_unexpected_native_outputs_are_not_counted(study) -> None:
    root, plan = study
    first = NativeFixture(root, plan, _cell(plan, "SS"))
    second = NativeFixture(root, plan, _cell(plan, "SM"))
    second.native["id"] = first.native["id"]
    first.write()
    second.write()
    result = report.collect(root)
    assert result["counts"]["invalid"] == 2
    assert result["counts"]["measured"] == 0
    assert result["usage"]["known_cli_estimate_usd"] is None
    assert [row["error"] for row in result["cells"] if row["attempted"]] == [
        "duplicate_native_trial_id",
        "duplicate_native_trial_id",
    ]
    (first.trial.parent / "unexpected-native-trial").mkdir()
    row = report.inspect_cell(root, plan, _cell(plan, "SS"))
    assert row["error"] == "registered_cell_mismatch"
    (root / "cells" / "unexpected-cell").mkdir()
    with pytest.raises(ValueError, match="unexpected native cell"):
        report.collect(root)


def test_collect_validates_frozen_sources_and_cell_inputs(study) -> None:
    root, plan = study
    cell = _cell(plan, "SS")
    native = NativeFixture(root, plan, cell)
    native.write()
    task = root / "cells" / cell["cell_id"] / "dispatch-handoff" / "task.toml"
    task.write_text("not a registered task", encoding="utf-8")
    assert report.inspect_cell(root, plan, cell)["error"] == "registered_cell_mismatch"
    source = root / "sources" / "seatlab" / "seat_agent.py"
    source.chmod(0o600)
    source.write_text("not a registered source", encoding="utf-8")
    source.chmod(0o400)
    with pytest.raises(ValueError, match="source differs from registration"):
        report.collect(root)
