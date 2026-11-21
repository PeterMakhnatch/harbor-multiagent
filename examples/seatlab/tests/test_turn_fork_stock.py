import importlib
import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.tests import test_turn_fork_packet as fixtures
from examples.seatlab.turn_fork import protocol
from examples.seatlab.turn_fork.__main__ import _envelope, _grid

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    stock = importlib.import_module("examples.seatlab.turn_fork.stock")

study = fixtures.study
stored = fixtures.stored
_DIGEST = "d" * 64


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _turn(
    row: dict[str, Any], stage: str, role: str, prompt: str, text: str
) -> dict[str, Any]:
    return {
        "stage": stage,
        "role": role,
        "run": protocol.run_id(_DIGEST, row["source_id"], row["arm"], stage),
        "prompt_sha256": _hash(prompt),
        "response_sha256": _hash(text),
        "usage": None,
        "status": "received",
    }


def _report(packet: dict[str, Any]) -> dict[str, Any]:
    source = packet["sources"][0]
    rows = []
    for cell in _grid():
        enrolled = cell["source_id"] == source["source_id"]
        row = dict(
            cell,
            state="unclaimed" if enrolled else "no_source",
            outcome=None,
            turns=[],
        )
        if enrolled and cell["arm"] in ("aligned", "shifted"):
            opened = ("Q1", "Q2")
            row["turns"] = [
                _turn(
                    row,
                    "push",
                    "inventory",
                    protocol.push_prompt(source, cell["arm"]),
                    json.dumps({"open": opened}),
                )
            ]
            if cell["arm"] == "shifted":
                row["state"] = "incomplete"
            else:
                choices = {
                    alias: max(case["after"], key=case["utilities"].__getitem__)
                    for alias, case_id in aliases(
                        source["cases"], source["seed"], source["repetition"]
                    ).items()
                    for case in (source["cases"][case_id],)
                }
                text = json.dumps({"choices": choices})
                row["state"] = "complete"
                row["outcome"] = protocol.grade(source, opened, text)
                row["turns"].append(
                    _turn(
                        row,
                        "decide",
                        "dispatch",
                        protocol.decide_prompt(source, opened),
                        text,
                    )
                )
        rows.append(row)
    return _envelope(packet, _DIGEST, "commands", rows)


def test_stock_rescores_fork_and_original_without_changing_native_evidence(
    study: fixtures.Study, stored: dict[str, Any]
) -> None:
    artifact = study.trial / "artifacts/logs/world/handoff.json"
    original_bytes = artifact.read_bytes()
    result = stock.compare(stored, _report(stored), study.root)
    assert result == {
        "schema": "seatlab.turn-fork-stock/v1",
        "measurement": "offline_rescore_not_new_native_rewards",
        "source_turns_bound": 2,
        "original_prompts_bound": 2,
        "original_actions_bound": 6,
        "original_optimal": 4,
        "original_feasible": 5,
        "arms": {
            "aligned": {
                "complete": 1,
                "incomplete": 0,
                "unclaimed": 0,
                "no_source": 11,
                "actions": 6,
                "optimal": 6,
                "feasible": 6,
            },
            "shifted": {
                "complete": 0,
                "incomplete": 1,
                "unclaimed": 0,
                "no_source": 11,
                "actions": 0,
                "optimal": 0,
                "feasible": 0,
            },
            "repeat": {
                "complete": 0,
                "incomplete": 0,
                "unclaimed": 1,
                "no_source": 11,
                "actions": 0,
                "optimal": 0,
                "feasible": 0,
            },
        },
        "mismatches": 0,
    }
    assert artifact.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "field",
    [
        "trial_id",
        "world_sha256",
        "inventory.prompt_sha256",
        "inventory.response_sha256",
        "brief.prompt_sha256",
        "brief.response_sha256",
    ],
)
def test_stock_rejects_a_packet_bound_to_different_native_evidence(
    study: fixtures.Study, stored: dict[str, Any], field: str
) -> None:
    report = _report(stored)
    source = stored["sources"][0]
    if field == "trial_id":
        source[field] = str(UUID(int=1))
    elif field == "world_sha256":
        source[field] = "0" * 64
    else:
        stage, key = field.split(".")
        source["upstream"][stage][key] = "0" * 64
    with pytest.raises(
        ValueError, match="differs from the packet|differ from the packet"
    ):
        stock.compare(stored, report, study.root)


@pytest.mark.parametrize("field", ["choice", "feasible", "optimal", "opened_sensitive"])
def test_stock_rejects_counterfeit_fork_grades(
    study: fixtures.Study, stored: dict[str, Any], field: str
) -> None:
    report = _report(stored)
    outcome = next(
        row["outcome"] for row in report["rows"] if row["state"] == "complete"
    )
    if field == "choice":
        case = stored["sources"][0]["cases"][0]
        outcome["actions"][0][field] = next(
            bay for bay in "ABCD" if bay not in case["after"]
        )
    elif field == "feasible":
        outcome["actions"][0][field] = False
    elif field == "optimal":
        outcome[field] -= 1
    else:
        outcome[field] = (outcome[field] + 1) % 3
    with pytest.raises(ValueError, match="differ from the native stock score"):
        stock.compare(stored, report, study.root)


@pytest.mark.parametrize(
    "fault", ["root-symlink", "trial-symlink", "artifact-symlink", "oversized-result"]
)
def test_stock_requires_physical_bounded_native_inputs(
    tmp_path: Path, study: fixtures.Study, stored: dict[str, Any], fault: str
) -> None:
    report = _report(stored)
    root = study.root
    if fault == "root-symlink":
        root = tmp_path / "native-link"
        root.symlink_to(study.root, target_is_directory=True)
    elif fault == "trial-symlink":
        target = tmp_path / "other-trial"
        study.trial.rename(target)
        study.trial.symlink_to(target, target_is_directory=True)
    elif fault == "artifact-symlink":
        artifact = study.trial / "artifacts/logs/world/handoff.json"
        target = tmp_path / "other-world.json"
        artifact.rename(target)
        artifact.symlink_to(target)
    else:
        (study.trial / "result.json").write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        stock.compare(stored, report, root)


@pytest.mark.parametrize("seat_index", [0, 1], ids=["push", "decide"])
def test_stock_rejects_drift_in_an_original_continuation_prompt(
    study: fixtures.Study, stored: dict[str, Any], seat_index: int
) -> None:
    report = _report(stored)
    native = study.native_result
    seat = native["seat_results"][seat_index]["agent_result"]["metadata"]
    seat["turns"][1]["prompt_sha256"] = "0" * 64
    (study.trial / "result.json").write_text(json.dumps(native), encoding="utf-8")
    with pytest.raises(ValueError, match="native briefing evidence"):
        stock.compare(stored, report, study.root)
