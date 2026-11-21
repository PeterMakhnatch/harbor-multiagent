import importlib
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    audit = importlib.import_module("examples.seatlab.briefing.audit")
    protocol = importlib.import_module("examples.seatlab.briefing.protocol")
    attention = importlib.import_module("examples.seatlab.attention.protocol")
    native = importlib.import_module("examples.seatlab.visibility.native_pairs")

_STAGES = ("inventory", "brief", "push", "decide")


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _config(tmp_path: Path, cell: dict[str, Any], direct: bool) -> dict[str, Any]:
    name = f"dispatch-handoff__{cell['cell_id']}"
    return {
        "task": {"path": str(tmp_path / "dispatch-handoff")},
        "environment": {"type": "docker"},
        "verifier": {"disable": False},
        "trials_dir": str(tmp_path),
        "trial_name": name,
        "participants": [
            {
                "seat": role,
                "import_path": "examples.seatlab.briefing.agent:BriefingSeat",
                "model_name": native.DECLARED_MODEL if direct else None,
                "kwargs": {
                    "cell_id": cell["cell_id"],
                    "transport": "direct" if direct else "commands",
                    "command_config": None
                    if direct
                    else str(tmp_path / "commands.json"),
                    "channel_id": name,
                },
            }
            for role in ("inventory", "dispatch")
        ],
    }


def _turn(
    cell_id: str, stage: str, prompt: str, response: dict[str, Any], direct: bool
) -> dict[str, Any]:
    text = "  " + json.dumps(response) + "\n"
    return {
        "stage": stage,
        "run": f"{cell_id}-{stage}",
        "role": "inventory" if stage in ("inventory", "push") else "dispatch",
        "status": "received",
        "prompt_sha256": _sha(prompt),
        "response_sha256": _sha(text),
        "usage": {
            "served_model": "deepseek-flash",
            "input_tokens": 12,
            "cache_tokens": 2,
            "output_tokens": 4,
        }
        if direct
        else None,
        "text": text,
    }


def _seat(
    view: dict[str, Any],
    cell: dict[str, Any],
    rows: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    opened: tuple[str, str],
    brief: str | None,
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
            "name": "seatlab-dispatch-briefing",
            "version": "1.0.0",
            "model_info": {"name": "deepseek-flash", "provider": "deepseek"}
            if direct
            else None,
        },
        "agent_result": {
            "n_input_tokens": 24 if direct else None,
            "n_cache_tokens": 4 if direct else None,
            "n_output_tokens": 8 if direct else None,
            "metadata": {
                "schema": "seatlab.native-briefing-seat/v1",
                "cell_id": cell["cell_id"],
                "role": role,
                "stage": "complete",
                "view_sha256": native.view_hash(view),
                "decision_sha256": native._decision_hash(role, rows),
                "opened": list(opened),
                "brief_seen" if role == "inventory" else "brief": brief,
                "turns": turns,
            },
        },
    }


def _simulate(
    cell: dict[str, Any], direct: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
    assert world.submit("inventory", {"rows": reports}) == {"accepted": True}
    dispatch = world.view("dispatch")
    cases = dispatch["cases"]
    aliases = attention.aliases(cases, seed, rep)
    first, second = (
        alias for alias, native_id in aliases.items() if native_id in (0, 2)
    )
    opened = (first, second)
    choices = []
    for case in world.evidence()["cases"]:
        available, utilities = case["after"], case["utilities"]
        if case["id"] == 0:
            bay = next(bay for bay in "ABCD" if bay not in available)
        elif case["id"] == 1:
            bay = min(available, key=utilities.__getitem__)
        else:
            bay = max(available, key=utilities.__getitem__)
        choices.append({"id": case["id"], "bay": bay})
    assert world.submit("dispatch", {"rows": choices}) == {"accepted": True}
    dossier = world.evidence()
    dossier["observation_receipts"] = {
        view["role"]: {"ack_sha256": native.view_hash(view)}
        for view in (inventory, dispatch)
    }
    ranks = [cases[native_id]["utilities"] for native_id in aliases.values()]
    card = min(ranks[0], key=ranks[0].__getitem__) + "".join(
        max(rank, key=rank.__getitem__) for rank in ranks[1:]
    )
    released = card if cell["arm"] == "brief" else None
    source = _turn(
        cell_id,
        "inventory",
        native._prompt("inventory", inventory["cases"]),
        {"rows": reports},
        direct,
    )
    head = _turn(
        cell_id,
        "brief",
        protocol.brief_prompt(cases, seed, rep),
        {"brief": card},
        direct,
    )
    push = _turn(
        cell_id,
        "push",
        protocol.push_prompt(reports, seed, rep, released),
        {"open": opened},
        direct,
    )
    final = _turn(
        cell_id,
        "decide",
        attention.decide_prompt(cases, seed, rep, opened),
        {"choices": {alias: choices[i]["bay"] for alias, i in aliases.items()}},
        direct,
    )
    return dossier, [
        _seat(inventory, cell, reports, [source, push], opened, released, direct),
        _seat(dispatch, cell, choices, [head, final], opened, card, direct),
    ]


def _trial(tmp_path: Path, arm: str, transport: str) -> tuple[Path, dict, dict, dict]:
    cell = next(cell for cell in protocol.grid() if cell["arm"] == arm)
    direct = transport == "direct"
    config = _config(tmp_path.resolve(), cell, direct)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    dossier, seats = _simulate(cell, direct)
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    _write(artifact, dossier)
    _write(trial / "config.json", config)
    result = {
        "id": str(uuid5(NAMESPACE_URL, trial.as_uri())),
        "trial_name": config["trial_name"],
        "trial_uri": trial.as_uri(),
        "task_id": {"path": config["task"]["path"]},
        "config": config,
        "exception_info": None,
        "finished_at": "2026-11-20T00:00:01Z",
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 4 / 6}},
        "seat_results": seats,
    }
    _write(trial / "result.json", result)
    return trial, config, cell, result


@pytest.mark.parametrize("arm", ["brief", "masked"])
@pytest.mark.parametrize("transport", ["direct", "commands"])
def test_audit_reconciles_native_commitments_without_exposing_turns(
    tmp_path: Path, arm: str, transport: str
) -> None:
    trial, config, cell, result = _trial(tmp_path, arm, transport)
    observed = audit.inspect_trial(trial, config, cell, transport)
    inv, dispatch = [
        seat["agent_result"]["metadata"] for seat in result["seat_results"]
    ]
    assert observed["native_score"] == 4 / 6
    assert len(observed["actions"]) == 6
    assert not observed["actions"][0]["feasible"]
    assert observed["actions"][1]["feasible"] and not observed["actions"][1]["optimal"]
    assert observed["opened"] == inv["opened"] == dispatch["opened"]
    assert observed["opened_report_changes"] == 1
    assert observed["total_report_changes"] == 3
    assert observed["source_copy_errors"] == 2
    assert observed["opened_stock_changes"] == observed["opened_sensitive"] == 0
    assert observed["brief_correct"] == 5
    assert observed["brief_slots"] == 6
    private_turns = [
        inv["turns"][0],
        dispatch["turns"][0],
        inv["turns"][1],
        dispatch["turns"][1],
    ]
    assert observed["turns"] == [
        {key: value for key, value in turn.items() if key != "text"}
        for turn in private_turns
    ]
    public = json.dumps(observed)
    assert dispatch["brief"] not in public
    assert "brief_seen" not in observed and "brief" not in observed


@pytest.mark.parametrize(
    ("mutation", "arm"),
    [
        ("matching false card", "brief"),
        ("missing release", "brief"),
        ("leaked release", "masked"),
        ("missing mask", "masked"),
        ("swapped prompts", "brief"),
        ("push from true stock", "brief"),
        ("matching false selection", "brief"),
        ("wrong final prompt", "masked"),
        ("different final choice", "brief"),
        ("extra turn", "masked"),
        ("wrong reward", "brief"),
        ("wrong relay", "masked"),
        ("missing acknowledgment", "masked"),
    ],
)
def test_audit_rejects_inconsistent_native_evidence(
    tmp_path: Path, mutation: str, arm: str
) -> None:
    trial, config, cell, result = _trial(tmp_path, arm, "commands")
    inv, dispatch = [
        seat["agent_result"]["metadata"] for seat in result["seat_results"]
    ]
    source, push = inv["turns"]
    seed, rep = cell["seed"], cell["repetition"]
    reports = json.loads(source["text"])["rows"]
    artifact = trial / "artifacts/logs/world/handoff.json"
    dossier = json.loads(artifact.read_text(encoding="utf-8"))
    if mutation in ("matching false card", "missing release", "leaked release"):
        card = dispatch["brief"]
        if mutation == "matching false card":
            card = next(bay for bay in "ABCD" if bay != card[0]) + card[1:]
            dispatch["brief"] = card
        elif mutation == "missing release":
            card = None
        inv["brief_seen"] = card
        push["prompt_sha256"] = _sha(protocol.push_prompt(reports, seed, rep, card))
    elif mutation == "missing mask":
        del inv["brief_seen"]
    elif mutation == "swapped prompts":
        head = dispatch["turns"][0]
        head["prompt_sha256"], push["prompt_sha256"] = (
            push["prompt_sha256"],
            head["prompt_sha256"],
        )
    elif mutation == "push from true stock":
        reports[0]["reports"][1]["available"] = dossier["cases"][0]["after"]
        push["prompt_sha256"] = _sha(
            protocol.push_prompt(reports, seed, rep, inv["brief_seen"])
        )
    elif mutation in ("matching false selection", "wrong final prompt"):
        opened = tuple(f"Q{i}" for i in range(1, 7) if f"Q{i}" not in inv["opened"])[:2]
        if mutation == "matching false selection":
            inv["opened"] = dispatch["opened"] = list(opened)
        view = audit.reconstructed_capsules(dossier)["dispatch"]["view"]
        dispatch["turns"][1]["prompt_sha256"] = _sha(
            attention.decide_prompt(view["cases"], seed, rep, opened)
        )
    elif mutation == "different final choice":
        final = dispatch["turns"][1]
        body = json.loads(final["text"])
        choices = body["choices"]
        choices["Q1"] = next(bay for bay in "ABCD" if bay != choices["Q1"])
        final["text"] = json.dumps(body)
        final["response_sha256"] = _sha(final["text"])
    elif mutation == "extra turn":
        inv["turns"].append(deepcopy(push))
    elif mutation == "wrong reward":
        result["verifier_result"]["rewards"]["reward"] = 1
    elif mutation == "wrong relay":
        for participant in config["participants"]:
            participant["kwargs"]["channel_id"] = cell["cell_id"]
        _write(trial / "config.json", config)
    else:
        del dossier["observation_receipts"]["dispatch"]
        _write(artifact, dossier)
    _write(trial / "result.json", result)
    with pytest.raises(ValueError, match="native briefing evidence"):
        audit.inspect_trial(trial, config, cell, "commands")


def _outcome(cell: dict[str, Any], score: int) -> dict[str, Any]:
    cell_id = cell["cell_id"]
    return {
        "trial_id": str(uuid5(NAMESPACE_URL, cell_id)),
        "fingerprint": _sha(str(cell["seed"])),
        "native_score": score,
        "opened": ["Q1", "Q2"],
        "opened_report_changes": 1,
        "total_report_changes": 3,
        "opened_stock_changes": 0,
        "opened_sensitive": 0,
        "source_copy_errors": 2,
        "brief_correct": 5,
        "brief_slots": 6,
        "actions": [
            {"id": i, "choice": "A", "optimal": bool(score), "feasible": True}
            for i in range(6)
        ],
        "turns": [{"run": f"{cell_id}-{stage}", "usage": None} for stage in _STAGES],
    }


def test_summary_weights_seeds_and_keeps_unpaired_native_scores() -> None:
    cells = list(protocol.grid())
    rows = []
    for cell in cells:
        seed, rep, arm = cell["seed"], cell["repetition"], cell["arm"]
        outcome = None
        if seed == 6501 or rep == 0:
            outcome = _outcome(cell, int((seed == 6501) == (arm == "brief")))
        elif (seed, rep, arm) == (6502, 1, "brief"):
            outcome = _outcome(cell, 1)
        rows.append(
            {**cell, "state": "complete" if outcome else "missing", "outcome": outcome}
        )
    summary = audit.summarize(cells, rows[::-1])
    primary = summary["primary"]
    assert summary["observed_cells"] == 15
    assert summary["missing_cells"] == 9
    assert primary["contrast"] == "brief_minus_masked_native_score"
    assert primary["estimate"] == pytest.approx(-2 / 3)
    assert primary["matched_pairs"] == 7
    assert primary["paired_seeds"] == list(range(6501, 6507))
    assert primary["registered_bounds"] == pytest.approx(
        {"lower": -7 / 12, "upper": 2 / 12}
    )
    assert summary["secondary"]["brief"]["brief_correct"] == 40
    assert summary["secondary"]["brief"]["brief_slots"] == 48
    assert summary["secondary"]["brief"]["brief_correct_rate"] == 5 / 6
    assert summary["secondary"]["masked"]["brief_correct"] == 35
    assert summary["secondary"]["masked"]["brief_slots"] == 42


def test_summary_reports_bounds_when_no_pair_completes() -> None:
    cells = list(protocol.grid())
    observed = {(6501, 0, "brief"): 0, (6502, 0, "masked"): 1}
    rows = []
    for cell in cells:
        score = observed.get((cell["seed"], cell["repetition"], cell["arm"]))
        outcome = None if score is None else _outcome(cell, score)
        rows.append(
            {
                **cell,
                "state": "missing" if score is None else "complete",
                "outcome": outcome,
            }
        )
    summary = audit.summarize(cells, rows)
    assert summary["observed_cells"] == 2
    assert summary["missing_cells"] == 22
    assert summary["primary"]["estimate"] is None
    assert summary["primary"]["matched_pairs"] == 0
    assert summary["primary"]["registered_pairs"] == 12
    assert summary["primary"]["registered_bounds"] == pytest.approx(
        {"lower": -1, "upper": 10 / 12}
    )


@pytest.mark.parametrize(
    "mutation", ["wrong roster", "extra turn", "swapped outcome", "card slots"]
)
def test_summary_rejects_unregistered_evidence(mutation: str) -> None:
    cells = list(protocol.grid())
    rows = [
        {**cell, "state": "complete", "outcome": _outcome(cell, 1)} for cell in cells
    ]
    if mutation == "wrong roster":
        cells[-1] = cells[0]
    elif mutation == "extra turn":
        turns = rows[0]["outcome"]["turns"]
        turns.append(turns[-1])
    elif mutation == "swapped outcome":
        rows[0]["outcome"], rows[1]["outcome"] = rows[1]["outcome"], rows[0]["outcome"]
    else:
        rows[0]["outcome"]["brief_slots"] = 5
    with pytest.raises(ValueError):
        audit.summarize(cells, rows)
