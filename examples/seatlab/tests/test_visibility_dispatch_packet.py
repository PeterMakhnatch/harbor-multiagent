import importlib
import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from examples.seatlab.visibility import dispatch_packet, pair_report

_ROOT = Path(__file__).resolve().parents[3]
_CHOICES = {"fresh": "ABCC", "silent": "CBDC"}
_PRIVATE = "private diagnostic that must not be published"


def _write(path: Path, value: object, *, sealed: bool = False) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    if sealed:
        path.chmod(0o400)


def _write_pair(root: Path, seed: int, repetition: int) -> None:
    """Build test-only native envelopes with complete recorded content."""
    native = importlib.import_module("examples.seatlab.visibility.native_pairs")
    for cell in native.grid():
        if (cell["seed"], cell["repetition"]) != (seed, repetition):
            continue
        arm = cell["condition"]
        world = dispatch_packet._world_type()(
            {"schema": "seatlab.scenario/v1", "seed": seed, "episodes": 4, "condition": arm}
        )
        inventory = world.view("inventory")["cases"]
        if seed != 3101:
            # Report something different from truth. It must reach the new prompt.
            report = inventory[0]["reports"][1]
            report["available"] = ["A", "B"] if report["available"] != ["A", "B"] else ["C", "D"]
        world.submit("inventory", {"rows": inventory})
        decisions = [{"id": i, "bay": bay} for i, bay in enumerate(_CHOICES[arm])]
        world.submit("dispatch", {"rows": decisions})
        dossier = world.evidence()
        submissions = {"inventory": inventory, "dispatch": decisions}
        acknowledgments = {}
        records = []
        for role, choices in submissions.items():
            view = world.view(role)
            digest = native.view_hash(view)
            acknowledgments[role] = {"ack_sha256": digest}
            records.append(
                {
                    "seat": role,
                    "under_test": True,
                    "exception_info": None,
                    "agent_execution": {"started_at": "start", "finished_at": "end"},
                    "agent_info": pair_report._AGENT,
                    "agent_result": {
                        "metadata": {
                            "schema": pair_report._SEAT_SCHEMA,
                            "role": role,
                            "stage": "complete",
                            "model_turns": 1,
                            "requested_model": "deepseek-flash",
                            "served_model": "deepseek-flash",
                            "run_id": cell["cell_id"],
                            "view_sha256": digest,
                            "prompt_sha256": native._prompt_hash(native._prompt(role, view["cases"])),
                            "decision_sha256": native._decision_hash(role, choices),
                        }
                    },
                }
            )
        config = native._trial_config(root, cell)
        trial = native.team_driver._trial_dir(root, cell)
        artifact = trial / "artifacts/logs/world/handoff.json"
        artifact.parent.mkdir(parents=True)
        _write(artifact, {**dossier, "observation_receipts": acknowledgments, "note": _PRIVATE})
        score = native.evidence._score(dossier)
        _write(
            trial / "result.json",
            {
                "id": str(uuid4()),
                "task_name": "harbor/dispatch-handoff",
                "trial_name": config["trial_name"],
                "trial_uri": trial.as_uri(),
                "task_id": {"path": config["task"]["path"]},
                "finished_at": "complete",
                "exception_info": None,
                "reward_valid": True,
                "verifier_result": {"rewards": {"reward": score["optimal"] / 4}},
                "seat_results": records,
                "config": config,
                "note": _PRIVATE,
            },
        )
        directory = trial.parents[1]
        marker: dict[str, Any] = {
            **pair_report._expected(pair_report._Cell(seed, repetition, arm)),
            "trial_name": config["trial_name"],
            "task_name": None,
            "trial_id": None,
            "native_returncode": None,
            "native_score": None,
            "outcome_valid": False,
            "error": "preparation_failed",
            "fingerprint": None,
            "actions": None,
            "seats": dict.fromkeys(("inventory", "dispatch")),
        }
        _write(directory / "attempt.json", marker, sealed=True)
        _write(directory / "config.json", config, sealed=True)
        receipt = {**marker, "native_returncode": 0, "note": _PRIVATE}
        native._inspect(trial, config, cell, receipt)
        _write(directory / "receipt.json", receipt, sealed=True)


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.syspath_prepend(str(_ROOT / "examples/seatlab"))
    root = (tmp_path / "private-study").resolve()
    _write_pair(root, 3101, 1)
    return root


def test_export_and_prompt_use_only_the_original_blind_request(
    study: Path, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert dispatch_packet.main(["export", str(study)]) == 0
    exported = capsysbinary.readouterr().out
    packet = json.loads(exported)
    assert packet["system"] == "Answer only with JSON."
    assert (packet["seed"], packet["repetition"]) == (3101, 1)
    assert _PRIVATE.encode() not in exported
    assert str(study).encode() not in exported
    assert b"trial_id" not in exported
    assert b"observation_receipts" not in exported
    assert b"served_model" not in exported
    packet_file = tmp_path / "packet.json"
    packet_file.write_bytes(exported)
    assert dispatch_packet.main(["verify", str(packet_file)]) == 0
    verification = json.loads(capsysbinary.readouterr().out)
    assert verification["authenticity"] == "not independently verified"
    assert "synthetic" in verification["world_basis"]
    assert verification["external_commitment"] == "not supplied"
    assert verification["packet_sha256"] == sha256(exported).hexdigest()
    assert not exported.endswith(b"\n")
    for arm, reward in (("fresh", 1.0), ("silent", 0.5)):
        details = packet["arms"][arm]
        assert details["score"]["reward"] == reward
        assert dispatch_packet.main(["prompt", str(packet_file), arm]) == 0
        user = capsysbinary.readouterr().out
        assert user == details["request"].encode()
        assert sha256(user).hexdigest() == details["request_sha256"]
        assert not user.endswith(b"\n")
        view = json.loads(user.split(b"\n", 1)[1])
        assert view.keys() == {"role", "cases"}
        for case, original in zip(view["cases"], details["inventory"]["rows"], strict=True):
            assert case.keys() == {"id", "utilities", "messages", "pooled_available"}
            assert case["pooled_available"] is None
            assert case["messages"] == ([original["reports"][1]] if arm == "fresh" else [])


@pytest.mark.parametrize("missing_result", [False, True])
def test_export_fails_closed_without_matching_native_evidence(
    study: Path, missing_result: bool
) -> None:
    directory = study / "cells/s3101-r1-silent"
    path = directory / "receipt.json"
    receipt = json.loads(path.read_bytes())
    if missing_result:
        (directory / "trials" / receipt["trial_name"] / "result.json").unlink()
    else:
        receipt["seats"]["dispatch"]["invocation"]["prompt_sha256"] = "0" * 64
        path.chmod(0o600)
        _write(path, receipt, sealed=True)
    with pytest.raises(ValueError, match="cannot verify the exact attested request"):
        dispatch_packet.export_packet(study)


@pytest.mark.parametrize(
    "change", ["request", "system", "inventory", "choice", "world", "score", "extra"]
)
def test_verifier_rejects_a_consistent_looking_replacement(study: Path, change: str) -> None:
    packet = dispatch_packet.export_packet(study)
    silent = packet["arms"]["silent"]
    if change == "request":
        silent["request"] += " "
        silent["request_sha256"] = sha256(silent["request"].encode()).hexdigest()
    elif change == "system":
        packet["system"] += " Use bay A."
    elif change == "inventory":
        silent["inventory"]["rows"][0]["reports"][0]["available"] = ["A", "B"]
    elif change == "choice":
        # Another infeasible, nonoptimal bay gives the same aggregate and row score.
        silent["dispatch"]["rows"][0]["bay"] = "B"
    elif change == "world":
        packet["world"][0]["before"] = ["A", "B"]
        packet["world_sha256"] = dispatch_packet.view_hash(packet["world"])
    elif change == "score":
        silent["score"]["rows"][0]["optimal"] = 0  # Wrong type, even though 0 == False.
    else:
        silent["note"] = _PRIVATE
    with pytest.raises(ValueError):
        dispatch_packet.verify_packet(packet)


def test_export_selects_another_registered_pair_and_applies_recorded_reports(
    study: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    _write_pair(study, 3109, 2)
    assert dispatch_packet.main(["export", str(study), "--pair", "3109", "2"]) == 0
    packet = json.loads(capsysbinary.readouterr().out)
    assert (packet["seed"], packet["repetition"]) == (3109, 2)
    truth = packet["world"][0]["after"]
    fresh = packet["arms"]["fresh"]
    report = fresh["inventory"]["rows"][0]["reports"][1]
    case = json.loads(fresh["request"].split("\n", 1)[1])["cases"][0]
    assert report["available"] != truth
    assert case["messages"] == [report]


def test_external_digest_rejects_coordinated_replacement(
    study: Path, tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    packet = dispatch_packet.export_packet(study)
    canonical = dispatch_packet.encode_view(packet)
    expected = sha256(canonical).hexdigest()
    path = tmp_path / "packet.json"
    path.write_bytes(canonical)
    verify = ["verify", str(path)]
    assert dispatch_packet.main([*verify, "--expect-sha256", expected]) == 0
    assert json.loads(capsysbinary.readouterr().out)["external_commitment"] == "matched"
    # Simulate a packet producer replacing a decision and its internal digest.
    # A different infeasible choice preserves every original score field.
    silent = packet["arms"]["silent"]
    rows = silent["dispatch"]["rows"]
    rows[0]["bay"] = "B"
    silent["dispatch_sha256"] = dispatch_packet.view_hash(rows)
    path.write_bytes(dispatch_packet.encode_view(packet))
    assert dispatch_packet.main(verify) == 0
    capsysbinary.readouterr()
    for args in (verify, ["prompt", str(path), "silent"]):
        with pytest.raises(SystemExit) as failure:
            dispatch_packet.main([*args, "--expect-sha256", expected])
        assert failure.value.code == 2
        captured = capsysbinary.readouterr()
        assert captured.out == b""
        assert b"does not match the external digest" in captured.err


def test_receiver_rescores_model_json_in_any_row_order(
    study: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packet = dispatch_packet.export_packet(study)
    path = tmp_path / "packet.json"
    _write(path, packet)
    fresh = packet["arms"]["fresh"]
    choices = json.dumps({"rows": list(reversed(fresh["dispatch"]["rows"]))})
    assert dispatch_packet.main(["score", str(path), "silent", choices]) == 0
    assert json.loads(capsys.readouterr().out) == fresh["score"]
    for malformed in (
        choices.replace('"id": 0', '"id": true'),
        choices.replace('"id": 0', '"id": 1'),
        choices.replace('"id": 0', '"id": 0, "id": 0'),
        choices.replace('"id": 0', '"id": 0, "leak": 0'),
        choices.replace('"bay": "A"', '"bay": NaN'),
    ):
        with pytest.raises(SystemExit) as failed:
            dispatch_packet.main(["score", str(path), "silent", malformed])
        assert failed.value.code == 2
        assert capsys.readouterr().out == ""
