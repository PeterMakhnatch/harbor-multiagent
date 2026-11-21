import importlib
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    native_panel = importlib.import_module("examples.seatlab.native_panel")
    briefing = importlib.import_module("examples.seatlab.briefing.__main__")
    briefing_audit = importlib.import_module("examples.seatlab.briefing.audit")
    fixtures = importlib.import_module("examples.seatlab.tests.test_briefing_audit")
    packet = importlib.import_module("examples.seatlab.turn_fork.packet")
    extract = importlib.import_module("examples.seatlab.turn_fork.extract")

_SECRET = "private-synthetic-metadata"


def _write(path: Path, value: object) -> bytes:
    body = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(body)
    return body


@dataclass(frozen=True)
class Study:
    root: Path
    trial: Path
    report: Path
    report_data: dict[str, Any]
    outcome: dict[str, Any]
    native_result: dict[str, Any]
    world: dict[str, Any]


@pytest.fixture
def study(tmp_path: Path) -> Study:
    panel, teams = native_panel.panel, native_panel.teams
    root = tmp_path.resolve() / "native"
    root.mkdir(mode=0o700)
    (root / "cells").mkdir(mode=0o700)
    spec = briefing.experiment()
    cells = list(spec.grid())
    cell = cells[0]
    assert cell["arm"] == "brief"
    plan = native_panel._plan(spec, cells, "direct", None)
    teams._seal(
        root / "register.json",
        teams._bytes({"plan": plan, "spend_directory": str(root / "absent-ledger")}),
    )
    directory = native_panel._cell_dir(root, cell)
    directory.mkdir(mode=0o700)
    (directory / "trials").mkdir(mode=0o700)
    config = native_panel._config(spec, root, cell, "direct")
    teams._seal(directory / "config.json", teams._bytes(config))
    teams._seal(directory / "attempt.json", teams._bytes(cell))
    panel._prepare_cell(directory, plan, cell, panel._snapshot("arrival"))
    teams._seal(
        directory / "outcome.json",
        teams._bytes({"returncode": 0, "launch_error": None}),
    )

    trial = Path(config["trials_dir"]) / config["trial_name"]
    world, seats = fixtures._simulate(cell, True)
    world["private_note"] = _SECRET
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    _write(artifact, world)
    _write(trial / "config.json", config)
    native_result = {
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
    _write(trial / "result.json", native_result)
    outcome = briefing_audit.inspect_trial(trial, config, cell, "direct")
    rows = [dict(item, state="unclaimed", outcome=None) for item in cells]
    rows[0].update(state="complete", outcome=outcome)
    report = tmp_path / "report.json"
    report_data = {
        "schema": spec.schema("report"),
        "plan": plan,
        "rows": rows,
        "summary": spec.summarize(cells, rows),
    }
    _write(report, report_data)
    return Study(root, trial, report, report_data, outcome, native_result, world)


def test_export_uses_the_accepted_inputs_without_publishing_private_fields(
    tmp_path: Path, study: Study
) -> None:
    masked = study.root / "cells" / "brf-s6501-r0-masked" / "trials"
    masked.mkdir(parents=True)
    (masked / "irrelevant").write_text(_SECRET, encoding="utf-8")
    built = extract.build(study.root, study.report)
    inv, dispatch = [
        seat["agent_result"]["metadata"] for seat in study.native_result["seat_results"]
    ]
    assert study.outcome["brief_correct"] == 5
    assert study.outcome["source_copy_errors"] == 2
    assert study.outcome["native_score"] == 4 / 6
    source = {
        "source_id": "s6501-r0",
        "seed": 6501,
        "repetition": 0,
        "trial_id": study.native_result["id"],
        "world_sha256": study.outcome["fingerprint"],
        "upstream": {
            stage: {key: turn[key] for key in ("prompt_sha256", "response_sha256")}
            for stage, turn in (
                ("inventory", inv["turns"][0]),
                ("brief", dispatch["turns"][0]),
            )
        },
        "card": dispatch["brief"],
        "cases": [
            {key: row[key] for key in ("id", "utilities", "reports", "after")}
            for row in study.world["cases"]
        ],
    }
    assert built == {
        "schema": "seatlab.turn-fork-packet/v1",
        "source_report_sha256": sha256(study.report.read_bytes()).hexdigest(),
        "sources": [source],
    }
    target = tmp_path / "packet.json"
    body = _write(target, built)
    assert _SECRET.encode("utf-8") not in body
    assert packet.load(target, sha256(body).hexdigest()) == built


@pytest.mark.parametrize(
    "fault",
    [
        "report-outcome",
        "missing-complete-row",
        "hidden-completion",
        "duplicate-report-row",
        "report-cell-identity",
        "duplicate-trial-folder",
        "native-trial-identity",
        "native-card",
        "native-reports",
    ],
)
def test_export_rejects_report_or_native_evidence_drift(
    study: Study, fault: str
) -> None:
    report = study.report_data
    row = report["rows"][0]
    if fault == "report-outcome":
        row["outcome"]["turns"][0]["prompt_sha256"] = "e" * 64
    elif fault == "missing-complete-row":
        report["rows"].pop(0)
    elif fault == "hidden-completion":
        row.update(state="unverified", outcome=None)
    elif fault == "duplicate-report-row":
        report["rows"][1] = row.copy()
    elif fault == "report-cell-identity":
        row["repetition"] = False
    elif fault == "duplicate-trial-folder":
        (study.trial.parent / "second-native-trial").mkdir()
    elif fault == "native-trial-identity":
        study.native_result["trial_uri"] += "/other"
        _write(study.trial / "result.json", study.native_result)
    elif fault == "native-card":
        seat = study.native_result["seat_results"][1]["agent_result"]["metadata"]
        original = seat["brief"]
        seat["brief"] = ("B" if original[0] == "A" else "A") + original[1:]
        _write(study.trial / "result.json", study.native_result)
    elif fault == "native-reports":
        study.world["cases"][0]["reports"].reverse()
        _write(study.trial / "artifacts/logs/world/handoff.json", study.world)
    else:
        pytest.fail("unknown fixture fault")
    _write(study.report, report)
    with pytest.raises(ValueError, match="inconsistent native briefing evidence"):
        extract.build(study.root, study.report)


@pytest.fixture
def stored(study: Study) -> dict[str, Any]:
    return extract.build(study.root, study.report)


@pytest.mark.parametrize(
    "fault",
    [
        "extra-upstream-field",
        "extra-case-field",
        "wrong-slot",
        "duplicate-trial",
        "bad-uuid",
        "report-order",
        "bay-order",
        "duplicate-case",
        "bad-utility",
    ],
)
def test_load_rejects_authenticated_packet_with_invalid_data(
    tmp_path: Path, stored: dict[str, Any], fault: str
) -> None:
    source = stored["sources"][0]
    cases = source["cases"]
    if fault == "extra-upstream-field":
        source["upstream"]["brief"]["text"] = _SECRET
    elif fault == "extra-case-field":
        cases[0]["visible"] = _SECRET
    elif fault == "wrong-slot":
        source["source_id"] = "s6501-r1"
    elif fault == "duplicate-trial":
        stored["sources"].append(dict(source, source_id="s6501-r1", repetition=1))
    elif fault == "bad-uuid":
        source["trial_id"] = "E70C35EC-3D66-4C10-AACD-56503B351F95"
    elif fault == "report-order":
        cases[0]["reports"].reverse()
    elif fault == "bay-order":
        cases[0]["reports"][0]["available"].reverse()
    elif fault == "duplicate-case":
        cases[1] = cases[0]
    elif fault == "bad-utility":
        cases[0]["utilities"]["A"] = True
    else:
        pytest.fail("unknown fixture fault")
    path = tmp_path / "packet.json"
    body = _write(path, stored)
    with pytest.raises(ValueError) as caught:
        packet.load(path, sha256(body).hexdigest())
    assert _SECRET not in str(caught.value)


def test_load_authenticates_exact_bytes_and_rejects_symlink(
    tmp_path: Path, stored: dict[str, Any]
) -> None:
    path = tmp_path / "packet.json"
    body = _write(path, stored)
    digest = sha256(body).hexdigest()
    path.write_bytes(body + b"\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        packet.load(path, digest)
    path.write_bytes(body)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="cannot read turn-fork packet"):
        packet.load(alias, digest)


@pytest.mark.parametrize(
    "token", [b"NaN", b"Infinity", b"1e9999", b'6501, "seed": 6501']
)
def test_load_rejects_ambiguous_json(
    tmp_path: Path, stored: dict[str, Any], token: bytes
) -> None:
    path = tmp_path / "packet.json"
    body = _write(path, stored).replace(b'"seed": 6501', b'"seed": ' + token)
    path.write_bytes(body)
    with pytest.raises(ValueError, match="invalid evidence JSON"):
        packet.load(path, sha256(body).hexdigest())


def test_load_rejects_oversized_authenticated_packet(tmp_path: Path) -> None:
    body = b" " * (64 * 1024 + 1)
    path = tmp_path / "packet.json"
    path.write_bytes(body)
    with pytest.raises(ValueError, match="file type or size"):
        packet.load(path, sha256(body).hexdigest())
