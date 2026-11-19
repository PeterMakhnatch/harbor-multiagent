import importlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    exporter = importlib.import_module("export_study")
    panel = importlib.import_module("run_panel")

PRIVATE = "private-text-that-must-not-escape"
MODEL = "example/free-model:free"
PROVIDER = "sample-provider"


def _plan(root: Path, policy: str, conditions: list[str]) -> Any:
    root.mkdir()
    route = {"model": MODEL, "provider": PROVIDER} if policy == "hosted" else None
    plan = panel._make_plan(
        [17],
        conditions,
        1,
        policy,
        dict.fromkeys(panel._source_names(policy), "a" * 64),
        route,
    )
    panel._create_json(root / "plan.json", plan)
    return plan


def _case(root: Path, plan: Any, condition: str, *, failure: str | None = None) -> Path:
    cell = next(cell for cell in plan["cells"] if cell["condition"] == condition)
    config = panel._trial_config(root.resolve(), plan, cell)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    directory = trial.parent.parent
    scenario = panel._scenario(plan, cell)
    scenario_path = directory / "dispatch-handoff" / panel.SCENARIO
    scenario_path.parent.mkdir(parents=True)
    panel._create_json(scenario_path, scenario)
    world = trial / exporter.WORLD
    world.parent.mkdir(parents=True)
    panel._create_json(
        trial / exporter.ARTIFACTS,
        [
            {
                "source": "/logs/world/handoff.json",
                "destination": exporter.WORLD,
                "type": "file",
                "status": "ok" if failure is None else "failed",
                "service": None,
                "private_artifact_field": PRIVATE,
            },
        ],
    )
    if failure is None:
        reports = [
            {"revision": revision, "available": ["A", "B"]} for revision in (1, 2)
        ]
        visible = {"fresh": [reports[1]], "reordered": reports[::-1]}
        panel._create_json(
            world,
            {
                "schema": "seatlab.world/v1",
                "complete": True,
                "settings": {**scenario, "host_path": str(root)},
                "private_dossier": PRIVATE,
                "cases": [
                    {
                        "id": 0,
                        "utilities": {"A": 1, "B": 4, "C": 2, "D": 3},
                        "before": ["A", "B"],
                        "after": ["A", "B"],
                        "reports": reports,
                        "visible": visible[condition],
                        "chosen": "B",
                        "private_case": {"raw_model_text": PRIVATE},
                    }
                ],
            },
        )
    timestamp = "2026-10-02T00:00:00Z"
    exception = (
        None
        if failure is None
        else {
            "exception_type": "_Unmeasured",
            "exception_message": failure,
            "exception_traceback": f"{root} {PRIVATE}",
        }
    )
    seats = []
    for participant in config["participants"]:
        role = participant["seat"]
        hosted = plan["policy"] == "hosted"
        model = {"name": MODEL, "provider": "openrouter"} if hosted else None
        context: dict[str, Any] = {"rollout_details": {"transcript": PRIVATE}}
        if hosted:
            turns = int(failure is None or role == "inventory")
            metadata = {
                "schema": "seatlab.seat/v1",
                "policy": "hosted",
                "role": role,
                "decision_source": "model",
                "model_turns": turns,
                "private_metadata": PRIVATE,
            }
            if turns:
                metadata["requested_model"] = MODEL
            if failure is None:
                metadata.update(
                    served_model=MODEL.removesuffix(":free"), provider="Sample Display"
                )
                context.update(
                    n_input_tokens=8, n_output_tokens=2, n_cache_tokens=1, cost_usd=0.0
                )
            context["metadata"] = metadata
        seats.append(
            {
                "seat": role,
                "under_test": True,
                "exception_info": exception if role == "inventory" else None,
                "agent_info": {
                    "name": participant["name"] or "seatlab-dispatch-handoff",
                    "version": "1.0.0",
                    "model_info": model,
                    "api_key": PRIVATE,
                },
                "agent_execution": {"started_at": timestamp, "finished_at": timestamp},
                "agent_result": context,
            },
        )
    panel._create_json(
        trial / "result.json",
        {
            "schema_version": 1,
            "id": str(uuid4()),
            "config": config,
            "task_name": "harbor/dispatch-handoff",
            "trial_name": trial.name,
            "trial_uri": trial.as_uri(),
            "finished_at": timestamp,
            "exception_info": exception,
            "verifier_result": {"rewards": {"reward": 1.0}}
            if failure is None
            else None,
            "reward_valid": failure is None,
            "seat_results": seats,
        },
    )
    panel._create_json(
        directory / "receipt.json", {"returncode": 0, "launch_error": None}
    )
    return trial


def _collect(root: Path, plan: Any) -> None:
    panel._write_index(
        root,
        plan,
        {
            cell["cell_id"]: panel._inspect_cell(root.resolve(), plan, cell)
            for cell in plan["cells"]
        },
    )


@pytest.fixture
def oracle_panel(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "oracle"
    plan = _plan(root, "oracle", ["silent", "stale", "fresh", "reordered"])
    trial = _case(root, plan, "fresh")
    _case(root, plan, "reordered")
    launch = root / "cells/seed-17--stale"
    launch.mkdir()
    panel._create_json(
        launch / "receipt.json",
        {"returncode": None, "launch_error": f"{root} {PRIVATE}"},
    )
    _collect(root, plan)
    return root, trial


def test_cli_exports_only_public_fields_and_verifies_without_native_files(
    oracle_panel: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, trial = oracle_panel
    assert exporter.main(["export", "--panel", f"oracle={root}"]) == 0
    output = capsys.readouterr().out
    assert PRIVATE not in output
    assert str(root.resolve()) not in output
    bundle = json.loads(output)
    assert output == exporter._canonical(bundle).decode() + "\n"
    exported = bundle["panels"][0]
    missing, launch, fresh, _ = exported["cells"]
    assert (missing["absence"], launch["absence"]) == ("not_attempted", "launch_failed")
    assert missing["native_attempt"] is None
    assert launch["process"] == {"state": "launch_failed", "returncode": None}
    result = fresh["measurement"]
    assert result["trial_id"] == panel._read_object(trial / "result.json")["id"]
    assert result["native_sha256"] == {
        key: panel._sha256((trial / filename).read_bytes())
        for key, filename in (
            ("result", "result.json"),
            ("world_manifest", exporter.WORLD),
            ("artifact_manifest", exporter.ARTIFACTS),
        )
    }
    assert exporter.score_manifest(result["manifest"]) == result["score"]
    assert result["seats"]["dispatch"]["n_output_tokens"] is None
    public = tmp_path / "public.json"
    public.write_text(output, encoding="utf-8")
    root.rename(tmp_path / "not-at-registered-path")
    assert exporter.main(["verify", str(public)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": exporter.VERIFICATION_SCHEMA,
        "panels": 1,
        "planned": 4,
        "measured": 2,
        "unmeasured": 2,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("score", "recomputed score"),
        ("same_score_report", "manifest SHA-256"),
        ("uuid", "duplicate native trial UUID"),
        ("coordinate", "planned grid"),
        ("count", "counts do not match"),
        ("private", "outside the allowlist"),
        ("fingerprint", "fingerprint does not match"),
        ("source_path", "source SHA-256 pins"),
    ],
)
def test_portable_verification_rejects_tampering(
    oracle_panel: tuple[Path, Path],
    mutation: str,
    message: str,
) -> None:
    bundle = exporter.export_bundle([("oracle", oracle_panel[0])])
    exported = bundle["panels"][0]
    fresh, reordered = exported["cells"][-2:]
    result = fresh["measurement"]
    if mutation == "score":
        result["score"]["rows"][0]["optimal"] = 1
    elif mutation == "same_score_report":
        result["manifest"]["cases"][0]["reports"][0]["available"] = ["A", "C"]
    elif mutation == "uuid":
        reordered["measurement"]["trial_id"] = result["trial_id"]
    elif mutation == "coordinate":
        exported["cells"][0] = fresh
    elif mutation == "count":
        exported["measured"] = 3
    elif mutation == "private":
        result["manifest"]["cases"][0]["private"] = PRIVATE
    elif mutation == "fingerprint":
        result["fingerprint"] = "f" * 64
    else:
        exported["plan"]["source_sha256"]["/private/path"] = "a" * 64
    with pytest.raises(ValueError, match=message):
        exporter.verify_bundle(bundle)


def test_export_refuses_substituted_native_policy(tmp_path: Path) -> None:
    root = tmp_path / "arrival"
    plan = _plan(root, "arrival", ["fresh"])
    native = _case(root, plan, "fresh") / "result.json"
    _collect(root, plan)
    result = panel._read_object(native)
    result["config"]["participants"][0]["kwargs"]["policy"] = "hosted"
    native.write_text(panel._json(result), encoding="utf-8")
    with pytest.raises(ValueError, match="config does not match"):
        exporter.export_bundle([("arrival", root)])


def test_hosted_failure_keeps_safe_native_telemetry(tmp_path: Path) -> None:
    root = tmp_path / "hosted"
    plan = _plan(root, "hosted", ["fresh", "reordered"])
    trial = _case(root, plan, "fresh", failure="OpenRouter request failed (HTTP 404)")
    _case(
        root,
        plan,
        "reordered",
        failure=f"OpenRouter request failed (HTTP 404) {PRIVATE}",
    )
    _collect(root, plan)
    bundle = exporter.export_bundle([("hosted", root)])
    fresh, reordered = bundle["panels"][0]["cells"]
    assert fresh["absence"] == reordered["absence"] == "native_failed"
    assert fresh["process"] == {"state": "exited", "returncode": 0}
    assert "measurement" not in fresh
    attempt = fresh["native_attempt"]
    assert attempt["trial_id"] == panel._read_object(trial / "result.json")["id"]
    assert attempt["native_sha256"]["result"] == panel._sha256(
        (trial / "result.json").read_bytes()
    )
    assert attempt["native_sha256"]["world_manifest"] is None
    assert attempt["seats"] == {
        "inventory": {"model_turns": 1, "http_status": 404},
        "dispatch": {"model_turns": 0, "http_status": None},
    }
    assert reordered["native_attempt"]["seats"]["inventory"]["http_status"] is None
    assert PRIVATE not in json.dumps(bundle)
    reordered["native_attempt"]["trial_id"] = attempt["trial_id"]
    with pytest.raises(ValueError, match="duplicate native trial UUID"):
        exporter.verify_bundle(bundle)


def test_hosted_provider_display_is_not_an_endpoint_pin(tmp_path: Path) -> None:
    root = tmp_path / "hosted"
    plan = _plan(root, "hosted", ["fresh"])
    _case(root, plan, "fresh")
    _collect(root, plan)
    bundle = exporter.export_bundle([("hosted", root)])
    exported = bundle["panels"][0]
    assert exported["plan"]["hosted_route"] == {"model": MODEL, "provider": PROVIDER}
    seat = exported["cells"][0]["measurement"]["seats"]["inventory"]
    assert seat["served_route"] == {
        "model": MODEL.removesuffix(":free"),
        "provider": "Sample Display",
        "provider_slug": None,
        "provider_binding": "unknown",
    }
    seat["served_route"]["provider_slug"] = "different-provider"
    with pytest.raises(ValueError, match="slug contradicts"):
        exporter.verify_bundle(bundle)
