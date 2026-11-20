import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from shutil import copyfile

import pytest

from examples.seatlab.visibility import clarify_run as runner
from examples.seatlab.visibility import clarify_schedule as schedule
from examples.seatlab.visibility import clarify_views, deepseek, spend

_START = "2026-11-22T11:00:00Z"
_NOW = datetime(2026, 11, 22, 11, tzinfo=UTC)
_CELLS = tuple(cell for cell in schedule.grid() if cell.window == 0)
_ACTIONS = {"rows": [{"id": i, "bay": bay} for i, bay in enumerate("ABCC")]}
_PRIVATE = "model text must not appear in a stored receipt"


@pytest.fixture(autouse=True)
def prevent_live_turns(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("test did not register a simulated turn")

    monkeypatch.setattr(deepseek, "turn", unexpected)
    monkeypatch.setattr(runner, "_now", lambda: _NOW)


@pytest.fixture
def packets(tmp_path):
    directory = tmp_path.resolve() / "packets"
    directory.mkdir()
    for seed in schedule.SEEDS:
        copyfile(
            schedule.packet_path(schedule.PACKETS, seed),
            schedule.packet_path(directory, seed),
        )
    return directory


@pytest.fixture
def study(tmp_path, packets):
    directory = tmp_path.resolve() / "study"
    runner.seal(packets, directory, start_utc=_START)
    return directory


def _receipt_path(study: Path, cell: schedule.Cell = _CELLS[0]) -> Path:
    return study / "cells" / f"{cell.cell_id}.json"


def _receipt(study: Path, cell=schedule.grid()[0]) -> dict:
    return json.loads(_receipt_path(study, cell).read_bytes())


def test_offline_cli_seals_exact_grid_packets_model_and_utc_bounds(
    packets, tmp_path, capsys
):
    study = tmp_path.resolve() / "new-study"
    assert runner.main(["seal", str(packets), str(study), "--start-utc", _START]) == 0
    sealed = json.loads(capsys.readouterr().out)
    assert sealed["schema"] == "seatlab-clarification-plan-v1"
    assert sealed["requested_model"] == deepseek.MODEL
    assert sealed["model_revision"] == deepseek.REVISION
    assert sealed["start_utc"] == _START
    assert sealed["windows"] == [
        {"window": 0, "start_utc": _START, "end_utc": "2026-11-22T11:30:00Z"},
        {
            "window": 1,
            "start_utc": "2026-11-22T14:00:00Z",
            "end_utc": "2026-11-22T14:30:00Z",
        },
        {
            "window": 2,
            "start_utc": "2026-11-22T16:30:00Z",
            "end_utc": "2026-11-22T17:00:00Z",
        },
    ]
    assert sealed["packet_sha256"] == {
        packet.name: sha256(packet.read_bytes()).hexdigest()
        for packet in packets.iterdir()
    }
    assert [row["cell_id"] for row in sealed["cells"]] == [
        cell.cell_id for cell in schedule.grid()
    ]
    assert len(sealed["cells"]) == 144
    assert [(row["hidden"], row["queued_case"]) for row in sealed["cells"]] == [
        (list(cell.hidden), cell.queued_case) for cell in schedule.grid()
    ]
    assert runner.main(["plan", str(study)]) == 0
    assert json.loads(capsys.readouterr().out) == sealed
    with pytest.raises(FileExistsError):
        runner.seal(packets, study, start_utc=_START)
    assert runner.load_plan(study) == sealed


def test_seal_rejects_unsafe_paths_and_invalid_inputs(tmp_path, packets, study):
    parent_link = tmp_path.resolve() / "link"
    parent_link.symlink_to(study.parent, target_is_directory=True)
    with pytest.raises(OSError):
        runner.seal(packets, parent_link / "new", start_utc=_START)
    with pytest.raises(OSError):
        runner.load_plan(parent_link / study.name)
    with pytest.raises(
        runner.StudyError, match="private_study_outside_checkout_required"
    ):
        runner.seal(packets, schedule.PACKETS / "study", start_utc=_START)
    with pytest.raises(runner.StudyError, match="start_utc_required"):
        runner.seal(
            packets, study.parent / "new", start_utc="2026-11-22T11:00:00-05:00"
        )
    path = schedule.packet_path(packets, schedule.SEEDS[0])
    path.write_bytes(schedule.packet_path(packets, schedule.SEEDS[1]).read_bytes())
    with pytest.raises(runner.StudyError, match="evidence_invalid"):
        runner.seal(packets, study.parent / "new", start_utc=_START)
    assert not (study.parent / "new").exists()


def test_attempt_precedes_first_turn_and_complete_receipt_has_only_graded_fields(
    packets, study, monkeypatch, capsys
):
    cell, following = _CELLS[:2]
    calls = []

    def turn(prompt, *, run_id, role):
        calls.append(run_id)
        assert role == "dispatch"
        if run_id == f"{cell.cell_id}-select":
            marker = _receipt(study)
            assert marker["status"] == "started"
            assert marker["turns"] == {}
            assert marker["selected_case"] is None
            with pytest.raises(runner.StudyError, match="study_already_running"):
                runner.run_window(packets, study, 0)
            return deepseek.SeatTurn(
                '{"case":3}',
                deepseek.MODEL,
                input_tokens=100,
                cache_tokens=0,
                output_tokens=5,
            )
        if run_id == f"{cell.cell_id}-act":
            marker = _receipt(study)
            assert marker["status"] == "started"
            assert marker["selected_case"] == marker["delivered_case"] == 3
            assert set(marker["turns"]) == {"select"}
            return deepseek.SeatTurn(
                json.dumps(_ACTIONS),
                deepseek.REVISION,
                input_tokens=110,
                cache_tokens=64,
                output_tokens=42,
            )
        assert run_id == f"{following.cell_id}-select"
        raise deepseek.SeatUnavailable(_PRIVATE)

    monkeypatch.setattr(deepseek, "turn", turn)
    assert runner.main(["run-window", str(packets), str(study), "0"]) == 1
    summary = json.loads(capsys.readouterr().out)
    assert calls == [
        f"{cell.cell_id}-select",
        f"{cell.cell_id}-act",
        f"{following.cell_id}-select",
    ]
    assert summary["complete"] == 1
    assert summary["incomplete"] == {following.cell_id: "transport_failed"}
    assert summary["unattempted"] == [pending.cell_id for pending in _CELLS[2:]]
    packet_path = schedule.packet_path(packets, cell.seed)
    raw = packet_path.read_bytes()
    packet = json.loads(raw)
    assert _receipt(study) == {
        "schema": "seatlab-clarification-turns-v1",
        "cell_id": cell.cell_id,
        "window": cell.window,
        "seed": cell.seed,
        "mask": cell.mask,
        "arm": cell.arm,
        "packet_sha256": sha256(raw).hexdigest(),
        "status": "complete",
        "selected_case": 3,
        "delivered_case": 3,
        "actions": _ACTIONS,
        "grade": clarify_views.grade_actions(packet, cell, _ACTIONS),
        "turns": {
            "select": {
                "prompt_sha256": sha256(
                    clarify_views.build_initial(packet, cell).encode()
                ).hexdigest(),
                "served_model": deepseek.MODEL,
            },
            "act": {
                "prompt_sha256": sha256(
                    clarify_views.build_final(packet, cell, 3).encode()
                ).hexdigest(),
                "served_model": deepseek.REVISION,
            },
        },
    }
    assert _PRIVATE not in _receipt_path(study, following).read_text()


@pytest.mark.parametrize(
    "failure", ["budget", "malformed", "backup", "spoofed_alias", "interrupted"]
)
def test_deliberate_invocation_skips_attempted_slot(
    failure, packets, study, monkeypatch
):
    first, second = _CELLS[:2]
    calls = []

    def turn(prompt, *, run_id, role):
        calls.append(run_id)
        if run_id == f"{first.cell_id}-select":
            return deepseek.SeatTurn('{"case":0}', deepseek.MODEL, 100, 0, 5)
        if run_id == f"{first.cell_id}-act":
            if failure == "budget":
                raise spend.SpendRefused(_PRIVATE)
            if failure == "malformed":
                return deepseek.SeatTurn(_PRIVATE, deepseek.MODEL, 110, 64, 8)
            if failure in {"backup", "spoofed_alias"}:
                served = "deepseek-pro" if failure == "backup" else "DeepSeek-Flash"
                return deepseek.SeatTurn(json.dumps(_ACTIONS), served, 110, 64, 42)
            raise KeyboardInterrupt
        assert run_id == f"{second.cell_id}-select"
        raise deepseek.SeatUnavailable("provider_timeout")

    monkeypatch.setattr(deepseek, "turn", turn)
    expected = {
        "budget": "budget_unresolved",
        "malformed": "evidence_invalid",
        "backup": "served_model_invalid",
        "spoofed_alias": "served_model_invalid",
        "interrupted": "started",
    }[failure]
    if failure == "interrupted":
        with pytest.raises(KeyboardInterrupt):
            runner.run_window(packets, study, 0)
    else:
        result = runner.run_window(packets, study, 0)
        assert result["stopped"] == {"cell_id": first.cell_id, "status": expected}
    raw = _receipt_path(study).read_bytes()
    receipt = json.loads(raw)
    assert receipt["status"] == expected
    assert receipt["selected_case"] == receipt["delivered_case"] == 0
    assert receipt["grade"] is None and receipt["actions"] is None
    assert set(receipt["turns"]) == (
        {"select", "act"} if failure == "malformed" else {"select"}
    )
    assert _PRIVATE.encode() not in raw
    result = runner.run_window(packets, study, 0)
    assert result["incomplete"] == {
        first.cell_id: expected,
        second.cell_id: "transport_failed",
    }
    assert _receipt_path(study).read_bytes() == raw
    assert calls == [
        f"{first.cell_id}-select",
        f"{first.cell_id}-act",
        f"{second.cell_id}-select",
    ]


@pytest.mark.parametrize("linked", [False, True])
def test_existing_invalid_receipt_is_not_replaced(linked, packets, study, monkeypatch):
    first, second = _CELLS[:2]
    path = _receipt_path(study)
    plan = study / "plan.json"
    sealed_plan = plan.read_bytes()
    if linked:
        path.symlink_to(plan)
    else:
        path.write_text(_PRIVATE)
        path.chmod(0o600)

    def turn(prompt, *, run_id, role):
        assert run_id == f"{second.cell_id}-select"
        raise deepseek.SeatUnavailable("provider_timeout")

    monkeypatch.setattr(deepseek, "turn", turn)
    result = runner.run_window(packets, study, 0)
    assert result["incomplete"] == {
        first.cell_id: "receipt_invalid",
        second.cell_id: "transport_failed",
    }
    assert plan.read_bytes() == sealed_plan
    if linked:
        assert path.readlink() == plan
    else:
        assert path.read_text() == _PRIVATE


@pytest.mark.parametrize("changed", ["early", "late", "packet"])
def test_second_turn_rechecks_current_time_and_every_source_packet(
    changed, packets, study, monkeypatch
):
    cell = _CELLS[0]
    path = schedule.packet_path(packets, schedule.SEEDS[-1])
    expected = "packet_changed" if changed == "packet" else "outside_window"

    def select_only(prompt, *, run_id, role):
        assert run_id == f"{cell.cell_id}-select"
        if changed == "packet":
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            delta = (
                timedelta(seconds=-1) if changed == "early" else timedelta(minutes=31)
            )
            monkeypatch.setattr(runner, "_now", lambda: _NOW + delta)
        return deepseek.SeatTurn('{"case":1}', deepseek.MODEL, 100, 0, 5)

    monkeypatch.setattr(deepseek, "turn", select_only)
    result = runner.run_window(packets, study, 0)
    assert result["stopped"] == {"cell_id": cell.cell_id, "status": expected}
    receipt = _receipt(study)
    assert receipt["status"] == expected
    assert set(receipt["turns"]) == {"select"}
    assert len(list((study / "cells").iterdir())) == 1


def test_later_windows_refuse_early_start_without_consuming_a_cell(packets, study):
    with pytest.raises(runner.StudyError, match="outside_window"):
        runner.run_window(packets, study, 1)
    assert not list((study / "cells").iterdir())
