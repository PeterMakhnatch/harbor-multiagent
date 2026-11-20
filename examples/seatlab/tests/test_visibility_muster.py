import importlib
import json
from pathlib import Path
from uuid import UUID

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    muster = importlib.import_module("visibility.muster")
    core = importlib.import_module("visibility.core")

_SEATS = ("p1", "p2", "p3")
_ARM = "e1/team-3"
_JOB_ID = str(UUID(int=1))
_TRIAL_ID = str(UUID(int=2))


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _write_events(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row, seq=i)) + "\n" for i, row in enumerate(rows, 1)))


def _row(kind, method, seat, response=None):
    return {"kind": kind, "method": method, "seat": seat, "response": response, "ok": True}


def _trial(tmp_path, suffix="a"):
    path = tmp_path / "team-3" / f"boxes-i01__{suffix}" / "result.json"
    roster = [{"seat": seat} for seat in _SEATS]
    _write_json(
        path,
        {
            "id": _TRIAL_ID,
            "trial_name": path.parent.name,
            "task_name": "muster/boxes-i01",
            "config": {"job_id": _JOB_ID, "participants": roster},
            "seat_results": roster,
            "finished_at": "2026-01-01T00:00:00Z",
        },
    )
    return path


def _artifact(trial_dir, rows):
    filename = "artifacts/logs/world/events.jsonl"
    _write_json(
        trial_dir / "artifacts/manifest.json",
        [
            {
                "source": "/logs/world/events.jsonl",
                "destination": filename,
                "type": "file",
                "status": "ok",
            }
        ],
    )
    _write_events(trial_dir / filename, rows)


def test_verifier_window_retains_fetches_missing_from_artifact(tmp_path):
    result = _trial(tmp_path)
    first = {"id": 1, "from": "p1", "text": "synthetic first"}
    second = {"id": 2, "from": "p2", "text": "synthetic second"}
    prefix = [_row("msg", "POST", "p1", first), _row("msg", "POST", "p2", second)]
    _artifact(result.parent, prefix)
    verifier = result.parent / "verifier/events.jsonl"
    read = _row("msgs", "GET", "p2", {"messages": [first, second]})
    _write_events(
        verifier,
        [
            *prefix,
            read,
            _row("mine", "GET", "p2"),
            _row("open", "POST", "p2"),
            read,
            _row("admin.close", "POST", None, {"closed": True, "events": 7}),
            _row("open", "POST", "p3"),
        ],
    )
    trial = muster._load_trial(result, _ARM, _JOB_ID, ())
    event = core.Event
    messages = (("p1", "1"), ("p2", "2"))
    assert trial.key == _TRIAL_ID
    assert trial.finished is True
    assert trial.seats == _SEATS
    assert trial.sources == (result, verifier)
    assert trial.coverage == dict.fromkeys(_SEATS, "complete")
    assert trial.events == (
        event("send", "p1", (messages[0],)),
        event("available", "p2", (messages[0],)),
        event("available", "p3", (messages[0],)),
        event("send", "p2", (messages[1],)),
        event("available", "p1", (messages[1],)),
        event("available", "p3", (messages[1],)),
        event("fetch", "p2", messages),
        event("action", "p2"),
        event("fetch", "p2", messages),
    )
    muted = core.project(trial, "p2")
    assert [e.messages for e in muted.events if e.kind == "fetch"] == [(messages[1],)] * 2


def test_missing_window_and_reader_do_not_prove_fetch_or_silence(tmp_path):
    result = _trial(tmp_path)
    trial = muster._load_trial(result, _ARM, _JOB_ID, ())
    assert trial.events == ()
    assert trial.coverage == dict.fromkeys(_SEATS, "unavailable")

    message = {"id": 1, "from": "p1", "text": "synthetic"}
    _artifact(
        result.parent,
        [
            _row("msg", "POST", "p1", message),
            _row("msgs", "GET", None, {"messages": [message]}),
        ],
    )
    trial = muster._load_trial(result, _ARM, _JOB_ID, ())
    assert trial.coverage == dict.fromkeys(_SEATS, "partial")
    assert [e.kind for e in trial.events] == ["send", "available", "available"]


def test_closed_solo_window_proves_no_channel_traffic(tmp_path):
    verifier = tmp_path / "verifier/events.jsonl"
    _write_events(
        verifier,
        [
            _row("mine", "GET", "p1"),
            _row("open", "POST", "p1"),
            _row("admin.close", "POST", None, {"closed": True, "events": 3}),
        ],
    )
    events, coverage = muster._project(verifier, ("p1",), channel=False)
    assert coverage == {"p1": "complete"}
    assert events == (core.Event("action", "p1"),)


def test_two_folders_with_one_native_id_do_not_create_two_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(muster, "_ARMS", (_ARM,))
    monkeypatch.setattr(muster, "_INSTANCES", ("boxes-i01",))
    monkeypatch.setattr(muster, "_ATTEMPTS", 2)
    _trial(tmp_path, "a")
    _trial(tmp_path, "b")
    _write_json(
        tmp_path / "team-3/config.json",
        {"job_name": "team-3", "n_attempts": 2, "tasks": [{"path": "boxes-i01"}]},
    )
    _write_json(tmp_path / "team-3/result.json", {"id": _JOB_ID, "n_total_trials": 2})
    trials = muster.load(tmp_path)
    assert next(trials).key == _TRIAL_ID
    with pytest.raises(ValueError, match="repeats a native trial UUID"):
        next(trials)
