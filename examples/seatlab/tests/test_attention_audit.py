import importlib
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import uuid4

import pytest

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    audit = importlib.import_module("examples.seatlab.attention.audit")
    protocol = importlib.import_module("examples.seatlab.attention.protocol")
    native = importlib.import_module("examples.seatlab.visibility.native_pairs")


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _case_config(
    tmp_path: Path, cell: dict[str, Any], transport: str
) -> dict[str, Any]:
    direct = transport == "direct"
    return {
        "task": {"path": str(tmp_path / "dispatch-handoff")},
        "environment": {"type": "docker"},
        "verifier": {"disable": False},
        "trials_dir": str(tmp_path / "trials"),
        "trial_name": cell["cell_id"],
        "participants": [
            {
                "seat": role,
                "import_path": "examples.seatlab.attention.agent:AttentionSeat",
                "model_name": "deepseek/deepseek-flash" if direct else None,
                "kwargs": {
                    "cell_id": cell["cell_id"],
                    "transport": transport,
                    "command_config": None
                    if direct
                    else str(tmp_path / "commands.json"),
                },
            }
            for role in ("inventory", "dispatch")
        ],
    }


def _turn(
    cell_id: str, stage: str, prompt: str, result: dict[str, Any], direct: bool
) -> dict[str, Any]:
    text = "  " + json.dumps(result, indent=2) + "\n"
    return {
        "stage": stage,
        "run": f"{cell_id}-{stage}",
        "role": "inventory" if stage == "inventory" else "dispatch",
        "status": "received",
        "prompt_sha256": _sha(prompt),
        "response_sha256": _sha(text),
        "text": text,
        "usage": {
            "served_model": "deepseek-flash",
            "input_tokens": 12,
            "cache_tokens": 2,
            "output_tokens": 4,
        }
        if direct
        else None,
    }


def _seat_record(
    view: dict[str, Any],
    cell: dict[str, Any],
    rows: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    opened: list[str] | None,
    direct: bool,
) -> dict[str, Any]:
    role = view["role"]
    return {
        "seat": role,
        "under_test": True,
        "exception_info": None,
        "agent_execution": {
            "started_at": "2026-11-20T00:00:00Z",
            "finished_at": "2026-11-20T00:00:01Z",
        },
        "agent_info": {
            "name": "seatlab-revision-attention",
            "version": "1.0.0",
            "model_info": {"name": "deepseek-flash", "provider": "deepseek"}
            if direct
            else None,
        },
        "agent_result": {
            "metadata": {
                "schema": "seatlab.native-attention-seat/v1",
                "cell_id": cell["cell_id"],
                "role": role,
                "stage": "complete",
                "view_sha256": native.view_hash(view),
                "decision_sha256": native._decision_hash(role, rows),
                "opened": opened,
                "turns": turns,
            }
        },
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _trial(tmp_path: Path, transport: str) -> tuple[Path, dict, dict, dict, dict]:
    cell = next(cell for cell in protocol.grid() if cell["arm"] == "named")
    cell_id, seed, rep = cell["cell_id"], cell["seed"], cell["repetition"]
    world_type = run_path(str(native.run_panel.TASK_DIR / "environment/seatworld.py"))[
        "World"
    ]
    world = world_type(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": 6,
            "condition": "reordered",
        }
    )
    inventory = world.view("inventory")
    reports = deepcopy(inventory["cases"])
    reports[0]["reports"][1]["available"] = [
        bay for bay in "DCBA" if bay not in reports[0]["reports"][0]["available"]
    ]
    reports[1]["reports"][1]["available"] = reports[1]["reports"][0]["available"]
    world.submit("inventory", {"rows": reports})
    dispatch = world.view("dispatch")
    cases = dispatch["cases"]
    aliases = protocol.aliases(cases, seed, rep)
    opened = sorted(alias for alias, actual in aliases.items() if actual in (0, 2))
    choices = []
    for case in world.evidence()["cases"]:
        after = case["after"]
        bay = max(after, key=case["utilities"].__getitem__)
        if case["id"] == 0:
            bay = next(
                unavailable for unavailable in "ABCD" if unavailable not in after
            )
        elif case["id"] == 1:
            bay = min(after, key=case["utilities"].__getitem__)
        choices.append({"id": case["id"], "bay": bay})
    world.submit("dispatch", {"rows": choices})
    dossier = world.evidence()
    dossier["observation_receipts"] = {
        view["role"]: {"ack_sha256": native.view_hash(view)}
        for view in (inventory, dispatch)
    }
    direct = transport == "direct"
    source = _turn(
        cell_id,
        "inventory",
        native._prompt("inventory", inventory["cases"]),
        {"rows": reports},
        direct,
    )
    selector = _turn(
        cell_id,
        "select",
        protocol.select_prompt(cases, seed, rep, "named"),
        {"open": opened},
        direct,
    )
    final = _turn(
        cell_id,
        "decide",
        protocol.decide_prompt(cases, seed, rep, tuple(opened)),
        {"choices": {alias: choices[i]["bay"] for alias, i in aliases.items()}},
        direct,
    )
    config = _case_config(tmp_path, cell, transport)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    _write(artifact, dossier)
    _write(trial / "config.json", config)
    result = {
        "id": str(uuid4()),
        "trial_name": config["trial_name"],
        "trial_uri": trial.as_uri(),
        "task_id": {"path": config["task"]["path"]},
        "config": config,
        "exception_info": None,
        "finished_at": "2026-11-20T00:00:01Z",
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 4 / 6}},
        "seat_results": [
            _seat_record(inventory, cell, reports, [source], None, direct),
            _seat_record(dispatch, cell, choices, [selector, final], opened, direct),
        ],
    }
    _write(trial / "result.json", result)
    return trial, config, cell, result, dispatch


@pytest.mark.parametrize("transport", ["direct", "commands"])
def test_inspect_uses_the_accepted_sender_not_native_stock(
    tmp_path: Path, transport: str
) -> None:
    trial, config, cell, result, _ = _trial(tmp_path.resolve(), transport)
    observed = audit.inspect_trial(trial, config, cell, transport)
    assert observed["native_score"] == 4 / 6
    assert len(observed["actions"]) == 6
    assert not observed["actions"][0]["feasible"]
    assert observed["actions"][1]["feasible"] and not observed["actions"][1]["optimal"]
    assert len(observed["opened"]) == 2
    assert observed["opened_report_changes"] == 1
    assert observed["total_report_changes"] == 3
    assert observed["source_copy_errors"] == 2
    assert observed["opened_stock_changes"] == observed["opened_sensitive"] == 0
    source = result["seat_results"][0]["agent_result"]["metadata"]["turns"][0]
    assert observed["turns"][0]["response_sha256"] == _sha(source["text"])
    assert all("text" not in turn for turn in observed["turns"])


@pytest.mark.parametrize(
    "mutation", ["arm", "truth_badge", "model_rows", "standalone_config"]
)
def test_inspect_rejects_wrong_evidence_even_with_a_native_score(
    tmp_path: Path, mutation: str
) -> None:
    trial, config, cell, result, dispatch = _trial(tmp_path.resolve(), "commands")
    selection, final = result["seat_results"][1]["agent_result"]["metadata"]["turns"]
    seed, rep = cell["seed"], cell["repetition"]
    if mutation == "arm":
        selection["prompt_sha256"] = _sha(
            protocol.select_prompt(dispatch["cases"], seed, rep, "count")
        )
    elif mutation == "truth_badge":
        dossier = json.loads(
            (trial / "artifacts/logs/world/handoff.json").read_text(encoding="utf-8")
        )
        for case, latent in zip(dispatch["cases"], dossier["cases"], strict=True):
            assert case["messages"][0]["revision"] == 2
            case["messages"][0]["available"] = latent["after"]
        selection["prompt_sha256"] = _sha(
            protocol.select_prompt(dispatch["cases"], seed, rep, "named")
        )
    elif mutation == "model_rows":
        choices = json.loads(final["text"])["choices"]
        choices["Q1"] = next(bay for bay in "ABCD" if bay != choices["Q1"])
        final["text"] = json.dumps({"choices": choices})
        final["response_sha256"] = _sha(final["text"])
    else:
        wrong = deepcopy(config)
        wrong["participants"][1]["model_name"] = "deepseek/deepseek-flash"
        _write(trial / "config.json", wrong)
    _write(trial / "result.json", result)
    with pytest.raises(ValueError):
        audit.inspect_trial(trial, config, cell, "commands")


def _outcome(cell: dict[str, Any], score: int) -> dict[str, Any]:
    return {
        "trial_id": str(uuid4()),
        "fingerprint": _sha(str(cell["seed"])),
        "native_score": score,
        "opened": ["Q1", "Q2"],
        "opened_report_changes": 1,
        "total_report_changes": 3,
        "opened_stock_changes": 0,
        "opened_sensitive": 0,
        "source_copy_errors": 2,
        "actions": [
            {"id": i, "choice": "A", "optimal": bool(score), "feasible": True}
            for i in range(6)
        ],
        "turns": [
            {"run": f"{cell['cell_id']}-{stage}", "usage": None}
            for stage in ("inventory", "select", "decide")
        ],
    }


def test_summary_weights_worlds_and_bounds_missing_pairs() -> None:
    cells = list(protocol.grid())
    rows = []
    for cell in cells:
        seed, rep, arm = cell["seed"], cell["repetition"], cell["arm"]
        outcome = None
        if seed == 6401 or rep == 0:
            outcome = _outcome(cell, int((seed == 6401) == (arm == "named")))
        elif (seed, rep, arm) == (6402, 1, "named"):
            outcome = _outcome(cell, 1)
        rows.append(
            {**cell, "state": "complete" if outcome else "missing", "outcome": outcome}
        )

    summary = audit.summarize(cells, rows[::-1])
    primary = summary["primary"]
    assert summary["observed_cells"] == 15
    assert summary["missing_cells"] == 9
    assert primary["estimate"] == pytest.approx(-2 / 3)
    assert primary["matched_pairs"] == 7
    assert primary["paired_seeds"] == list(range(6401, 6407))
    assert primary["registered_bounds"] == pytest.approx(
        {"lower": -7 / 12, "upper": 2 / 12}
    )
    assert summary["secondary"]["named"]["opened_slots"] == 16
    assert summary["secondary"]["named"]["opened_report_changes"] == 8
    assert summary["secondary"]["named"]["source_copy_errors"] == 16
    assert summary["secondary"]["named"]["optimal"] == 18
    assert summary["secondary"]["named"]["feasible"] == 48

    missing = next(
        row
        for row in rows
        if (row["seed"], row["repetition"], row["arm"]) == (6406, 0, "named")
    )
    missing.update(outcome=None, state="missing")
    primary = audit.summarize(cells, rows)["primary"]
    assert primary["estimate"] is None
    assert primary["matched_pairs"] == 6
    assert primary["paired_seeds"] == list(range(6401, 6406))
    assert primary["registered_bounds"] == pytest.approx(
        {"lower": -7 / 12, "upper": 3 / 12}
    )

    del missing["outcome"]
    with pytest.raises(ValueError):
        audit.summarize(cells, rows)


def test_summary_rejects_an_outcome_labelled_as_the_other_arm() -> None:
    cells = list(protocol.grid())
    rows = [
        {**cell, "state": "complete", "outcome": _outcome(cell, 0)} for cell in cells
    ]
    named, count = (
        next(
            row
            for row in rows
            if (row["seed"], row["repetition"], row["arm"]) == (6401, 0, arm)
        )
        for arm in ("named", "count")
    )
    named["outcome"], count["outcome"] = count["outcome"], named["outcome"]
    with pytest.raises(ValueError):
        audit.summarize(cells, rows)
