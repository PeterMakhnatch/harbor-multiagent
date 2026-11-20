import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    flotilla = importlib.import_module("visibility.flotilla")

Event = flotilla.Event
_SEATS = ("p1", "p2")


def _json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _record(kind: str, seat: str | None = None, **data: object) -> dict:
    return {"kind": kind, "seat": seat, "data": data}


def _tape(directory: Path, *records: dict, version: int = 2) -> Path:
    start = {"condition": "talk", "seats": list(_SEATS)}
    if version == 2:
        start["world_version"] = 2
    tape = directory / "artifacts/logs/world/events.jsonl"
    tape.parent.mkdir(parents=True)
    rows = (_record("start", **start), *records)
    tape.write_text(
        "".join(json.dumps(dict(row, seq=seq, t=1.0)) + "\n" for seq, row in enumerate(rows, 1)),
        encoding="utf-8",
    )
    return tape


def _snapshot(directory: Path, *, messages: list, submissions: list) -> Path:
    state = directory / "verifier/world-state.json"
    _json(state, {
        "world_version": 2,
        "condition": "talk",
        "seats": list(_SEATS),
        "frozen": True,
        "messages": messages,
        "submissions": submissions,
    })
    return state


def test_fetches_preserve_server_order_repeats_and_empty_results(tmp_path: Path) -> None:
    tape = _tape(
        tmp_path,
        _record("deliver", "p2", ids=[]),
        _record("send", "p1", id="m1", to="all"),
        _record("send", "p2", id="m2", to="p2"),
        _record("submit", "p2", n=1, answer={"day": "Mon", "start": "09:00"}),
        _record("deliver", "p2", ids=["m1"]),
        _record("whoami", "p2"),
        _record("deliver", "p2", ids=["m1"]),
        _record("late", "p2", answer={"day": "Tue", "start": "09:00"}),
        _record("send", "p2", id="m3", to="p1"),
    )
    state = _snapshot(
        tmp_path,
        messages=[
            {"id": "m1", "from": "p1", "to": "all", "text": "Monday morning is free."},
            {"id": "m2", "from": "p2", "to": "p2", "text": "My reminder."},
            {"id": "m3", "from": "p2", "to": "p1", "text": "Thanks."},
        ],
        submissions=[{"n": 1, "seat": "p2", "answer": {"day": "Mon", "start": "09:00"}}],
    )

    events, coverage, sources = flotilla._read_evidence(tmp_path, _SEATS, "talk", 2, True)
    assert (coverage, sources) == ("complete", (tape, state))
    assert events == (
        Event("fetch", "p2"),
        Event("send", "p1", (("p1", "m1"),)),
        Event("available", "p2", (("p1", "m1"),)),
        Event("send", "p2", (("p2", "m2"),)),
        Event("action", "p2"),
        Event("fetch", "p2", (("p1", "m1"),)),
        Event("fetch", "p2", (("p1", "m1"),)),
        Event("send", "p2", (("p2", "m3"),)),
        Event("available", "p1", (("p2", "m3"),)),
    )


def test_pilot_receipt_is_kept_without_a_verifier_snapshot(tmp_path: Path) -> None:
    tape = _tape(
        tmp_path,
        _record("send", "p1", id="m1", to="p2"),
        _record("deliver", "p2", ids=["m1"]),
        version=1,
    )
    events, coverage, sources = flotilla._read_evidence(tmp_path, _SEATS, "talk", 1, True)
    assert (coverage, sources) == ("partial", (tape,))
    assert events == (
        Event("send", "p1", (("p1", "m1"),)),
        Event("available", "p2", (("p1", "m1"),)),
        Event("fetch", "p2", (("p1", "m1"),)),
    )
    assert flotilla._read_evidence(tmp_path, _SEATS, "talk", 2, True)[1] == "unavailable"


@pytest.mark.parametrize("ids", [["m2"], ["m1", "m1"], []])
def test_inconsistent_fetch_does_not_get_receipt_credit(tmp_path: Path, ids: list) -> None:
    _tape(
        tmp_path,
        _record("send", "p1", id="m1", to="p2"),
        _record("deliver", "p2", ids=ids),
        _record("submit", "p2", n=1, answer={"day": "Mon", "start": 9}),
    )
    events, coverage, _ = flotilla._read_evidence(tmp_path, _SEATS, "talk", 2, True)
    assert coverage == "partial"
    assert events == (
        Event("send", "p1", (("p1", "m1"),)),
        Event("available", "p2", (("p1", "m1"),)),
    )


def test_state_tail_does_not_create_in_window_receipts_or_actions(tmp_path: Path) -> None:
    tape = _tape(tmp_path, _record("send", "p1", id="m1", to="p2"))
    state = _snapshot(
        tmp_path,
        messages=[{"id": "m1", "from": "p1", "to": "p2", "text": "Tuesday is free."}],
        submissions=[{"n": 1, "seat": "p2", "answer": {"day": "Tue", "start": 9}}],
    )
    events, coverage, sources = flotilla._read_evidence(tmp_path, _SEATS, "talk", 2, True)
    assert (coverage, sources) == ("partial", (tape, state))
    assert events == (
        Event("send", "p1", (("p1", "m1"),)),
        Event("available", "p2", (("p1", "m1"),)),
    )
    tape.unlink()
    assert flotilla._read_evidence(tmp_path, _SEATS, "talk", 2, True) == ((), "unavailable", ())


def _job(root: Path, condition: str = "solo") -> tuple[Path, dict]:
    job = root / "c1-ds-1seat"
    job_id = str(uuid4())
    _json(job / "result.json", {"id": job_id})
    trial = job / "original-attempt"
    config = {"job_id": job_id, "task": {"path": f"schedule-000-{condition}"}}
    _json(trial / "config.json", config)
    return trial, config


def test_missing_result_does_not_drop_solo_trial(tmp_path: Path) -> None:
    trial_dir, _ = _job(tmp_path)
    trial, = flotilla._load_job(tmp_path, "c1-ds-1seat", "c1", {"solo": 1})
    assert trial.key == "flotilla:c1-ds-1seat/original-attempt"
    assert trial.arm == "c1/v2/solo"
    assert trial.seats == ("p1",)
    assert trial.finished is None
    assert trial.coverage == {"p1": "unavailable"}
    assert trial.sources[-1] == trial_dir / "config.json"


def test_transferred_or_duplicate_slot_is_not_a_replacement(tmp_path: Path) -> None:
    trial_dir, config = _job(tmp_path)
    name = "c1-ds-1seat"
    _json(trial_dir / "config.json", dict(config, source_trial="synthetic-transfer"))
    with pytest.raises(ValueError, match="transferred trial"):
        list(flotilla._load_job(tmp_path, name, "c1", {"solo": 1}))
    _json(trial_dir / "config.json", config)
    _json(trial_dir.parent / "duplicate-attempt" / "config.json", config)
    with pytest.raises(ValueError, match="duplicate Flotilla task slot"):
        list(flotilla._load_job(tmp_path, name, "c1", {"solo": 1}))
