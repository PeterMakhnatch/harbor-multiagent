from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from uuid import UUID

import pytest

from examples.seatlab.visibility.core import Event, measure_seats, project
from examples.seatlab.visibility.crucible import load, load_excluded, run_records

_MODEL = "fixture/model"
_LOG = Path("artifacts/logs/world/events.jsonl")
_REPORT = Path("experiments/report_final5/report.json")
_STATE = "PRIVATE_FIXTURE_TEXT"


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _event(kind, actor, *, authenticated=True, visibility="world_audit", **payload):
    return {
        "event_type": kind,
        "actor": actor,
        "authenticated": authenticated,
        "visibility": visibility,
        "payload": payload,
    }


def _run(
    root,
    index,
    family,
    condition,
    seats,
    events,
    *,
    wave="wave-listed",
    exception=None,
    collection_status="ok",
    clipped=False,
):
    result_id = str(UUID(int=index))
    name = f"native-{index}"
    relative = f"runs/{wave}"
    directory = root / relative / name
    _json(
        directory / "result.json",
        {
            "id": result_id,
            "trial_name": name,
            "finished_at": "2026-01-01T00:01:00Z",
            "verifier_result": {"rewards": {"reward": 1}},
            "config": {
                "participants": [{"seat": seat, "model_name": _MODEL} for seat in seats],
            },
            "seat_results": [
                {
                    "seat": seat,
                    "agent_info": {"model_info": {"provider": "fixture", "name": "model"}},
                    "agent_execution": {"finished_at": "2026-01-01T00:00:59Z"},
                }
                for seat in seats
            ],
            "exception_info": (
                {"exception_type": exception, "exception_message": "unrelated timeout text"}
                if exception
                else None
            ),
        },
    )
    _json(
        directory / "artifacts/manifest.json",
        [{"destination": _LOG.as_posix(), "status": collection_status}],
    )
    if events is not None:
        log = directory / _LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "".join(
                json.dumps({"seq": i, "task_family": family, "condition": condition, **event}) + "\n"
                for i, event in enumerate(events, 1)
            )
            + ('{"seq":' if clipped else ""),
            encoding="utf-8",
        )
    return relative, {
        "family": family,
        "condition": condition,
        "model": _MODEL,
        "trial_dir": f"/previous-location/{relative}/{name}",
    }


def _index(root, included, excluded=()):
    rows_by_wave: dict[str, list[dict[str, str]]] = {}
    for wave, row in (*included, *excluded):
        rows_by_wave.setdefault(wave, []).append(row)
    for wave, rows in rows_by_wave.items():
        _json(root / wave / "wave1_manifest.json", rows)
    arms = Counter((row["family"], row["condition"]) for _, row in included)
    _json(
        root / _REPORT,
        {
            "generated_by": "crucible report",
            "corpus_roots": list(rows_by_wave),
            "exclude_timeouts": True,
            "n_trials": len(included),
            "n_excluded_timeouts": len(excluded),
            "conditions": [
                {"family": family, "condition": condition, "n_trials": n}
                for (family, condition), n in arms.items()
            ],
        },
    )


def test_census_uses_report_and_wave_rows_and_keeps_timeout_separate(tmp_path):
    family = "migration-turf-war"
    actor = "migrator_c"
    seats = (actor, "migrator_ruby", "migrator_ts")
    events = [_event("repo_commit", "world", authenticated=False), _event("repo_commit", actor)]
    _index(
        tmp_path,
        [
            _run(tmp_path, 1, family, "aligned_control", seats, events),
            _run(tmp_path, 2, family, "aligned_control", seats, events, exception="AgentError"),
        ],
        [
            _run(
                tmp_path, 3, family, "aligned_control", seats, events,
                wave="wave-timeout", exception="AgentTimeoutError",
            )
        ],
    )
    _run(tmp_path, 4, family, "aligned_control", seats, events)
    _run(tmp_path, 5, family, "aligned_control", seats, events, wave="wave-listed-oracle-pilot")
    primary, non_timeout_error = load(tmp_path)
    (timeout,) = load_excluded(tmp_path)
    assert [trial.key for trial in (primary, non_timeout_error, timeout)] == [
        str(UUID(int=i)) for i in (1, 2, 3)
    ]
    assert [trial.finished for trial in (primary, non_timeout_error, timeout)] == [True, False, False]
    assert primary.seats == seats
    assert primary.coverage == dict.fromkeys(seats, "complete")
    assert timeout.coverage == dict.fromkeys(seats, "partial")
    assert primary.events == (Event("action", actor),)
    census = {
        _REPORT.as_posix(),
        "runs/wave-listed/wave1_manifest.json",
        "runs/wave-listed/native-1/result.json",
        "runs/wave-listed/native-2/result.json",
        "runs/wave-timeout/wave1_manifest.json",
        "runs/wave-timeout/native-3/result.json",
    }
    assert {path.relative_to(tmp_path).as_posix() for path in primary.sources} == census | {
        "runs/wave-listed/native-1/artifacts/manifest.json",
        f"runs/wave-listed/native-1/{_LOG.as_posix()}",
    }
    assert {path.relative_to(tmp_path).as_posix() for path in timeout.sources} == census | {
        "runs/wave-timeout/native-3/artifacts/manifest.json",
        f"runs/wave-timeout/native-3/{_LOG.as_posix()}",
    }


def test_census_rejects_duplicate_across_cohorts_and_unknown_observed_model(tmp_path):
    family = "epistemic-crucible"
    first = _run(tmp_path, 1, family, "hidden_profile_solo_ceiling", ("solo_analyst",), None)
    second = _run(
        tmp_path, 2, family, "hidden_profile_solo_ceiling", ("solo_analyst",), None,
        exception="AgentTimeoutError",
    )
    _index(tmp_path, [first], [second])
    file = tmp_path / "runs/wave-listed/native-2/result.json"
    result = json.loads(file.read_text())
    result["id"] = str(UUID(int=1))
    _json(file, result)
    with pytest.raises(ValueError, match="duplicate native trial UUID"):
        list(run_records(tmp_path))
    result["id"] = str(UUID(int=2))
    result["seat_results"][0]["agent_info"]["model_info"]["name"] = "unknown"
    _json(file, result)
    with pytest.raises(ValueError, match="recorded model is unknown"):
        list(run_records(tmp_path, excluded=True))


def test_supply_preserves_repeated_fetches_nonpeer_entries_and_order(tmp_path):
    family = "supply-chain-sabotage"
    first, second, silent = "scout_a", "scout_b", "builder"
    events = [
        _event("chat_public", first, text=_STATE),
        _event("chat_public", second, text=_STATE),
        _event("chat_dm", first, to=second, text=_STATE),
        _event("chat_dm", "world", authenticated=False, visibility="world_private", to=second, text=_STATE),
        _event("message_read", second, channel="public", msg_seqs=[]),
        _event("message_read", second, channel="public", msg_seqs=[2]),
        _event("message_read", second, channel="public", msg_seqs=[1, 2]),
        _event("message_read", second, channel="public", msg_seqs=[1, 1, 2]),
        _event("message_read", second, channel="dm", msg_seqs=[3, 4]),
        _event("scan", second),
        _event("message_read", first, channel="public", msg_seqs=[2]),
        _event("done", first),
        _event("fault_injected", first, authenticated=False),
    ]
    run = _run(tmp_path, 1, family, "honest", (first, second, silent), events)
    _index(tmp_path, [run])
    (trial,) = load(tmp_path)
    assert _STATE not in repr(trial.events)
    assert [(e.kind, e.messages) for e in trial.events if e.seat == second] == [
        ("send", ((second, "2"),)),
        ("fetch", ()),
        ("fetch", ((second, "2"),)),
        ("fetch", ((first, "1"), (second, "2"))),
        ("fetch", ((first, "1"), (first, "1"), (second, "2"))),
        ("fetch", ((first, "3"), ("world", "4"))),
        ("action", ()),
    ]
    muted = project(trial, second)
    assert [e.messages for e in muted.events if e.seat == second and e.kind == "fetch"] == [
        (), ((second, "2"),), ((second, "2"),), ((second, "2"),), (("world", "4"),),
    ]
    assert tuple(e for e in muted.events if e.seat != second) == tuple(
        e for e in trial.events if e.seat != second
    )
    seats = {seat.seat: seat for seat in measure_seats(trial)}
    changed = seats[second].receipts["fetch"]
    assert (changed.events, changed.message_occurrences, changed.unique_messages) == (3, 4, 2)
    assert changed.before_action == 3
    assert seats[first].receipts["fetch"].no_known_action == 1
    assert seats[silent].coverage == "complete"
    assert seats[silent].receipts["fetch"].events == 0


def test_reward_uses_audit_not_endpoint_cursor_and_explicit_actions(tmp_path):
    family = "reward-hack-contagion"
    seats = ("planner", "coder", "reviewer")
    events = [
        _event("plan_write", "planner"),
        _event("chat_post", "planner", message_seq=1, channel="public", text=_STATE),
        _event("message_read", "coder", channel="public", msg_seqs=[2]),
        _event("plan_read", "coder", version=1),
        _event("workspace_commit", "coder", message="workspace write"),
        _event("workspace_commit", "coder", message="submission snapshot"),
        _event("submit", "coder"),
        _event("submission_read", "reviewer"),
        _event("mechanism_detection", "reviewer", authenticated=False),
        _event("review_approve", "reviewer"),
    ]
    _index(tmp_path, [_run(tmp_path, 1, family, "clean", seats, events)])
    (trial,) = load(tmp_path)
    assert trial.events == (
        Event("action", "planner"),
        Event("send", "planner", (("planner", "2"),)),
        Event("fetch", "coder", (("planner", "2"),)),
        Event("action", "coder"),
        Event("action", "coder"),
        Event("action", "reviewer"),
    )


def test_round_scope_dangling_reference_and_solo_without_audit(tmp_path):
    family = "epistemic-crucible"
    scout, decision_maker, solo = "scout_1", "decision_maker", "solo_analyst"
    events = [
        _event("scout_report", scout, round=1),
        _event("scout_report", scout, round=2),
        _event("chat_message", scout, text=_STATE),
        _event("message_read", decision_maker, channel="round_reports", round=2, msg_seqs=[1, 2, 3]),
        _event("observation_read", scout, round=2),
        _event("decision", decision_maker, round=2, choice="route_alpha"),
    ]
    _index(
        tmp_path,
        [
            _run(tmp_path, 1, family, "gullibility_p00", (scout, decision_maker), events),
            _run(tmp_path, 2, family, "hidden_profile_solo_ceiling", (solo,), None),
            _run(
                tmp_path, 3, family, "gullibility_p00", (scout, decision_maker), events,
                clipped=True,
            ),
        ],
    )
    trial, missing, clipped = load(tmp_path)
    assert trial.events[-2:] == (
        Event("fetch", decision_maker, ((scout, "2"),)),
        Event("action", decision_maker),
    )
    assert trial.coverage == {scout: "complete", decision_maker: "partial"}
    assert missing.seats == (solo,)
    assert missing.coverage == {solo: "unavailable"}
    assert missing.events == ()
    assert clipped.events == trial.events
    assert clipped.coverage == {scout: "partial", decision_maker: "partial"}


def test_migration_board_and_failed_artifact_status(tmp_path):
    family = "migration-turf-war"
    first, second = "migrator_c", "migrator_ts"
    events = [
        _event("board_post", first, text=_STATE),
        _event("message_read", second, channel="board", msg_seqs=[1]),
        _event("service_deploy_requested", second),
        _event("service_deploy", second),
        _event("script_scan", second),
        _event("truce_signature", first),
        _event("seat_done", first),
    ]
    _index(
        tmp_path,
        [_run(
            tmp_path, 1, family, "aligned_control", (first, second), events,
            collection_status="failed",
        )],
    )
    (trial,) = load(tmp_path)
    assert trial.events == (
        Event("send", first, ((first, "1"),)),
        Event("fetch", second, ((first, "1"),)),
        Event("action", second),
        Event("action", first),
    )
    assert trial.coverage == {first: "partial", second: "partial"}
