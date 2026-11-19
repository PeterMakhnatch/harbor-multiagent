"""Unit tests for Hanabi cross-play collector and SeatLab episode exporter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ADAPTER_DIR = Path(__file__).resolve().parent.parent
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from study import PAIRINGS, digest  # noqa: E402

_COLLECT_PATH = _ADAPTER_DIR / "collect.py"
_SPEC = importlib.util.spec_from_file_location(
    "hanabi_crossplay_collect", _COLLECT_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Failed to load {_COLLECT_PATH}")
collect_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collect_mod)
_SEATLAB_SRC = _ADAPTER_DIR.parent.parent.parent / "harbor-seatlab-author-kit" / "src"
if _SEATLAB_SRC.is_dir() and str(_SEATLAB_SRC) not in sys.path:
    sys.path.insert(0, str(_SEATLAB_SRC))


def _uuid(n: int) -> str:
    return f"11111111-2222-4333-8444-{n:012x}"


def _make_turns(
    deck_id: int, pairing: str, *, p0_action2: int = 10
) -> list[dict[str, Any]]:
    raw = [
        (
            1,
            "p0",
            {"deck_hint": deck_id, "obs": f"d{deck_id}-{pairing}-t1", "seat": "p0"},
            5,
        ),
        (
            2,
            "p1",
            {"deck_hint": deck_id, "obs": f"d{deck_id}-{pairing}-t2", "seat": "p1"},
            6 if pairing[1] == "L" else 7,
        ),
        (
            3,
            "p0",
            {"deck_hint": deck_id, "obs": f"d{deck_id}-{pairing}-t3", "seat": "p0"},
            p0_action2,
        ),
        (
            4,
            "p1",
            {"deck_hint": deck_id, "obs": f"d{deck_id}-{pairing}-t4", "seat": "p1"},
            11,
        ),
    ]
    return [
        {
            "turn": t,
            "seat": seat,
            "view": view,
            "view_sha256": digest(view),
            "action": act,
        }
        for t, seat, view, act in raw
    ]


def _history_sha(turns: list[dict[str, Any]]) -> str:
    return digest(
        [[r["turn"], r["seat"], r["view_sha256"], r["action"]] for r in turns]
    )


def _deck_sha(deck_id: int) -> str:
    return digest(list(range(deck_id, deck_id + 50)))


def _write_trial(
    trials_dir: Path,
    trial_name: str,
    *,
    deck_id: int,
    pairing: str,
    trial_id: str,
    turns: list[dict[str, Any]],
    score: int,
    reward_override: float | None = None,
    reward_txt_override: str | None = None,
    exception_info: dict[str, Any] | None = None,
    seat_exception: dict[str, Any] | None = None,
    finished_at: str | None = "2026-11-19T12:00:10Z",
    seat_finished_at: str | None = "2026-11-19T12:00:08Z",
    reward_valid: bool = True,
    write_result: bool = True,
    write_hanabi: bool = True,
    hanabi_override: dict[str, Any] | None = None,
    audit_seats: tuple[str, ...] = ("p0", "p1"),
    audit_convention_override: dict[str, str] | None = None,
) -> Path:
    tdir = trials_dir / trial_name
    tdir.mkdir(parents=True, exist_ok=True)
    reward = score / 25.0 if reward_override is None else reward_override
    if write_result:
        result = {
            "schema_version": 1,
            "id": trial_id,
            "trial_name": trial_name,
            "exception_info": exception_info,
            "finished_at": finished_at,
            "reward_valid": reward_valid,
            "seat_results": [
                {
                    "seat": s,
                    "under_test": True,
                    "agent_info": {"name": "oracle", "version": "1.0.0"},
                    "exception_info": seat_exception if s == "p1" else None,
                    "agent_execution": {
                        "started_at": "2026-11-19T12:00:00Z",
                        "finished_at": seat_finished_at,
                    },
                }
                for s in ("p0", "p1")
            ],
            "verifier_result": {"rewards": {"reward": reward}},
        }
        (tdir / "result.json").write_text(json.dumps(result), encoding="utf-8")

    vdir = tdir / "verifier"
    vdir.mkdir(parents=True, exist_ok=True)
    txt = f"{reward}\n" if reward_txt_override is None else reward_txt_override
    (vdir / "reward.txt").write_text(txt, encoding="utf-8")

    if write_hanabi:
        wdir = tdir / "artifacts" / "logs" / "world"
        wdir.mkdir(parents=True, exist_ok=True)
        payload = hanabi_override or {
            "schema": "seatlab.hanabi-native/v1",
            "deck_id": deck_id,
            "deck_sha256": _deck_sha(deck_id),
            "turns": turns,
            "scores": [score, score],
        }
        (wdir / "hanabi.json").write_text(json.dumps(payload), encoding="utf-8")

    conv_overrides = audit_convention_override or {}
    for idx, seat in enumerate(("p0", "p1")):
        if seat not in audit_seats:
            continue
        sdir = tdir / "seats" / seat / "agent"
        sdir.mkdir(parents=True, exist_ok=True)
        conv = conv_overrides.get(seat, pairing[idx])
        lines = [
            json.dumps(
                {
                    "turn": r["turn"],
                    "seat": seat,
                    "convention": conv,
                    "view_sha256": r["view_sha256"],
                    "action": r["action"],
                },
                sort_keys=True,
            )
            for r in turns
            if r["seat"] == seat
        ]
        (sdir / "policy_audit.jsonl").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
    return tdir


def _build_fixture(
    root: Path,
    deck_ids: list[int],
    scores_map: dict[tuple[int, str], int],
    turns_map: dict[tuple[int, str], list[dict[str, Any]]],
) -> tuple[Path, Path]:
    native_root = root / "native"
    (native_root / "trials").mkdir(parents=True)
    plan_cells = []
    forecast_cells = []
    for d in deck_ids:
        dsha = _deck_sha(d)
        for p in PAIRINGS:
            name = f"han-test-d{d:02}-{p.lower()}"
            turns = turns_map[(d, p)]
            score = scores_map[(d, p)]
            plan_cells.append({"deck_id": d, "pairing": p, "trial_name": name})
            forecast_cells.append(
                {
                    "deck_id": d,
                    "pairing": p,
                    "deck_sha256": dsha,
                    "history_sha256": _history_sha(turns),
                    "decisions": len(turns),
                    "scores": [score, score],
                }
            )
    (native_root / "plan.json").write_text(
        json.dumps(
            {
                "schema": "seatlab.hanabi-plan/v1",
                "deck_ids": deck_ids,
                "cells": plan_cells,
            }
        ),
        encoding="utf-8",
    )
    forecast_path = root / "forecast.json"
    forecast_path.write_text(
        json.dumps(
            {"schema": "seatlab.hanabi-predictions/v1", "cells": forecast_cells}
        ),
        encoding="utf-8",
    )
    return native_root, forecast_path


def test_statuses_bad_digest_duplicate_uuid_and_genuine_zero(tmp_path: Path) -> None:
    decks = [0, 1]
    scores_map = {
        (0, "LL"): 0,
        (0, "LR"): 5,
        (0, "RL"): 4,
        (0, "RR"): 10,
        (1, "LL"): 7,
        (1, "LR"): 3,
        (1, "RL"): 2,
        (1, "RR"): 9,
    }
    turns_map = {(d, p): _make_turns(d, p) for d in decks for p in PAIRINGS}
    native_root, forecast_path = _build_fixture(tmp_path, decks, scores_map, turns_map)
    tdir = native_root / "trials"

    # d00-ll: genuine 0-score matched trial (must not be confused with invalid/missing)
    _write_trial(
        tdir,
        "han-test-d00-ll",
        deck_id=0,
        pairing="LL",
        trial_id=_uuid(1),
        turns=turns_map[(0, "LL")],
        score=0,
    )
    # d00-lr: valid complete trial whose history/score differs from forecast
    alt_turns = _make_turns(0, "LR", p0_action2=12)
    _write_trial(
        tdir,
        "han-test-d00-lr",
        deck_id=0,
        pairing="LR",
        trial_id=_uuid(2),
        turns=alt_turns,
        score=6,
    )
    # d00-rl: unattempted (no trial directory)
    # d00-rr: incomplete (no result.json)
    (tdir / "han-test-d00-rr").mkdir()
    # d01-ll: failed (native trial exception)
    _write_trial(
        tdir,
        "han-test-d01-ll",
        deck_id=1,
        pairing="LL",
        trial_id=_uuid(3),
        turns=turns_map[(1, "LL")],
        score=7,
        exception_info={"exception_type": "AgentTimeoutError"},
    )
    # d01-lr: invalid (bad view_sha256 digest on turn 2)
    bad_turns = [dict(r) for r in turns_map[(1, "LR")]]
    bad_turns[1]["view_sha256"] = "0" * 64
    _write_trial(
        tdir,
        "han-test-d01-lr",
        deck_id=1,
        pairing="LR",
        trial_id=_uuid(4),
        turns=bad_turns,
        score=3,
    )
    # d01-rl & d01-rr: duplicate UUID — both surfaced and disqualified as invalid
    _write_trial(
        tdir,
        "han-test-d01-rl",
        deck_id=1,
        pairing="RL",
        trial_id=_uuid(99),
        turns=turns_map[(1, "RL")],
        score=2,
    )
    _write_trial(
        tdir,
        "han-test-d01-rr",
        deck_id=1,
        pairing="RR",
        trial_id=_uuid(99),
        turns=turns_map[(1, "RR")],
        score=9,
    )

    report = collect_mod.collect(native_root, forecast_path)
    assert report["counts"] == {
        "registered": 8,
        "valid": 2,
        "matched": 1,
        "valid_prediction_differs": 1,
        "unattempted": 1,
        "incomplete": 1,
        "failed": 1,
        "invalid": 3,
        "duplicate_uuids": 1,
    }
    assert report["duplicate_uuids"] == {
        _uuid(99): ["han-test-d01-rl", "han-test-d01-rr"]
    }
    assert report["rates"]["matched_of_registered"] == pytest.approx(1 / 8)
    assert report["rates"]["matched_of_valid"] == pytest.approx(1 / 2)

    by_cell = {(c["deck_id"], c["pairing"]): c for c in report["cells"]}
    assert by_cell[(0, "LL")]["status"] == "matched"
    assert by_cell[(0, "LL")]["scores"] == [0, 0]
    assert by_cell[(0, "LL")]["reward"] == 0.0
    assert by_cell[(0, "LR")]["status"] == "valid_prediction_differs"
    assert by_cell[(0, "RL")]["status"] == "unattempted"
    assert by_cell[(0, "RL")]["scores"] is None
    assert by_cell[(0, "RR")]["status"] == "incomplete"
    assert by_cell[(1, "LL")]["status"] == "failed"
    assert by_cell[(1, "LR")]["status"] == "invalid"
    assert "view_sha256" in by_cell[(1, "LR")]["reason"]
    assert by_cell[(1, "RL")]["status"] == "invalid"
    assert by_cell[(1, "RR")]["status"] == "invalid"
    assert by_cell[(1, "RL")]["scores"] is None
    # Public output must contain no private host paths.
    assert str(tmp_path) not in json.dumps(report)


def test_bad_turn_ordering_and_reward_mismatch_are_invalid(tmp_path: Path) -> None:
    turns_map = {(0, p): _make_turns(0, p) for p in PAIRINGS}
    scores_map = {(0, p): 5 for p in PAIRINGS}
    native_root, forecast_path = _build_fixture(tmp_path, [0], scores_map, turns_map)
    tdir = native_root / "trials"

    # Bad turn numbering (turn 2 labeled as turn 3)
    bad_order = [dict(r) for r in turns_map[(0, "LL")]]
    bad_order[1]["turn"] = 3
    _write_trial(
        tdir,
        "han-test-d00-ll",
        deck_id=0,
        pairing="LL",
        trial_id=_uuid(10),
        turns=bad_order,
        score=5,
    )
    # Reward file disagrees with result.json beyond 1e-12
    _write_trial(
        tdir,
        "han-test-d00-lr",
        deck_id=0,
        pairing="LR",
        trial_id=_uuid(11),
        turns=turns_map[(0, "LR")],
        score=5,
        reward_txt_override="0.25\n",
    )
    # Seat missing agent_execution.finished_at -> incomplete
    _write_trial(
        tdir,
        "han-test-d00-rl",
        deck_id=0,
        pairing="RL",
        trial_id=_uuid(12),
        turns=turns_map[(0, "RL")],
        score=5,
        seat_finished_at=None,
    )
    # Valid within float tolerance 1e-12
    _write_trial(
        tdir,
        "han-test-d00-rr",
        deck_id=0,
        pairing="RR",
        trial_id=_uuid(13),
        turns=turns_map[(0, "RR")],
        score=5,
        reward_override=0.2 + 5e-13,
        reward_txt_override=f"{0.2 + 5e-13:.17g}\n",
    )

    report = collect_mod.collect(native_root, forecast_path)
    by_cell = {(c["deck_id"], c["pairing"]): c for c in report["cells"]}
    assert by_cell[(0, "LL")]["status"] == "invalid"
    assert by_cell[(0, "LR")]["status"] == "invalid"
    assert by_cell[(0, "RL")]["status"] == "incomplete"
    assert by_cell[(0, "RR")]["status"] == "matched"


def test_episode_export_seat_local_and_seatlab_align(tmp_path: Path) -> None:
    seatlab_episodes = pytest.importorskip("seatlab.episodes")
    seatlab_alignment = pytest.importorskip("seatlab.alignment")

    decks = [0, 1]
    scores_map = {(d, p): 4 for d in decks for p in PAIRINGS}
    turns_map = {(d, p): _make_turns(d, p) for d in decks for p in PAIRINGS}
    native_root, forecast_path = _build_fixture(tmp_path, decks, scores_map, turns_map)
    tdir = native_root / "trials"

    # Deck 0: all four valid. Deck 1: LL valid, LR invalid (bad digest), RL unattempted, RR valid.
    uid = 20
    for p in PAIRINGS:
        uid += 1
        _write_trial(
            tdir,
            f"han-test-d00-{p.lower()}",
            deck_id=0,
            pairing=p,
            trial_id=_uuid(uid),
            turns=turns_map[(0, p)],
            score=4,
        )
    _write_trial(
        tdir,
        "han-test-d01-ll",
        deck_id=1,
        pairing="LL",
        trial_id=_uuid(30),
        turns=turns_map[(1, "LL")],
        score=4,
    )
    bad = [dict(r) for r in turns_map[(1, "LR")]]
    bad[0]["view_sha256"] = "f" * 64
    _write_trial(
        tdir,
        "han-test-d01-lr",
        deck_id=1,
        pairing="LR",
        trial_id=_uuid(31),
        turns=bad,
        score=4,
    )
    _write_trial(
        tdir,
        "han-test-d01-rr",
        deck_id=1,
        pairing="RR",
        trial_id=_uuid(32),
        turns=turns_map[(1, "RR")],
        score=4,
    )

    ep_dir = tmp_path / "episodes"
    report = collect_mod.collect(native_root, forecast_path, ep_dir)
    assert report["episodes"]["episode_count"] == 12  # 6 valid trials * 2 seats

    all_eps = list(seatlab_episodes.read_episodes(ep_dir / "episodes.jsonl"))
    assert len(all_eps) == 12
    for ep in all_eps:
        assert ep["closed"] is True
        assert [f["turn"] for f in ep["frames"]] == [1, 2]
        assert all(
            f["basis"] == "bound" and isinstance(f["observed"]["action"], int)
            for f in ep["frames"]
        )

    # Verify SeatLab align accepts both matchup manifests cleanly and preserves null arms.
    lr_align = seatlab_alignment.align(
        ep_dir / "ll-to-lr-source.jsonl",
        ep_dir / "ll-to-lr-target.jsonl",
        ep_dir / "ll-to-lr-pairs.json",
    )
    assert lr_align["counts"]["pairs"] == 2
    assert lr_align["counts"]["missing"] == 1  # deck 1 LR was invalid -> target null
    lr_by_id = {p["id"]: p for p in lr_align["pairs"]}
    assert lr_by_id["hanabi:ll-to-lr:d00:p0"]["role"] == "p0"
    assert lr_by_id["hanabi:ll-to-lr:d01:p0"]["source"] is not None
    assert lr_by_id["hanabi:ll-to-lr:d01:p0"]["target"] is None

    rl_align = seatlab_alignment.align(
        ep_dir / "ll-to-rl-source.jsonl",
        ep_dir / "ll-to-rl-target.jsonl",
        ep_dir / "ll-to-rl-pairs.json",
    )
    assert rl_align["counts"]["pairs"] == 2
    assert (
        rl_align["counts"]["missing"] == 1
    )  # deck 1 RL was unattempted -> target null
    rl_by_id = {p["id"]: p for p in rl_align["pairs"]}
    assert rl_by_id["hanabi:ll-to-rl:d00:p1"]["role"] == "p1"
    assert rl_by_id["hanabi:ll-to-rl:d01:p1"]["target"] is None


def test_cli_emits_json_and_refuses_existing_episodes_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    turns_map = {(0, p): _make_turns(0, p) for p in PAIRINGS}
    scores_map = {(0, p): 3 for p in PAIRINGS}
    native_root, forecast_path = _build_fixture(tmp_path, [0], scores_map, turns_map)
    _write_trial(
        native_root / "trials",
        "han-test-d00-ll",
        deck_id=0,
        pairing="LL",
        trial_id=_uuid(50),
        turns=turns_map[(0, "LL")],
        score=3,
    )

    rc = collect_mod.main([str(native_root), str(forecast_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["registered"] == 4
    assert out["counts"]["matched"] == 1
    assert out["counts"]["unattempted"] == 3
    assert "episodes" not in out

    pytest.importorskip("seatlab.author")
    existing = tmp_path / "already-exists"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        collect_mod.main(
            [str(native_root), str(forecast_path), "--episodes", str(existing)]
        )


def test_missing_seat_audit_wrong_convention_and_decisions_diff(tmp_path: Path) -> None:
    turns_map = {(0, p): _make_turns(0, p) for p in PAIRINGS}
    scores_map = {(0, p): 4 for p in PAIRINGS}
    native_root, forecast_path = _build_fixture(tmp_path, [0], scores_map, turns_map)
    tdir = native_root / "trials"

    # LL: missing p1 policy_audit.jsonl receipt -> invalid
    _write_trial(
        tdir,
        "han-test-d00-ll",
        deck_id=0,
        pairing="LL",
        trial_id=_uuid(60),
        turns=turns_map[(0, "LL")],
        score=4,
        audit_seats=("p0",),
    )
    # LR: p1 audit records wrong convention 'L' instead of assigned 'R' -> invalid
    _write_trial(
        tdir,
        "han-test-d00-lr",
        deck_id=0,
        pairing="LR",
        trial_id=_uuid(61),
        turns=turns_map[(0, "LR")],
        score=4,
        audit_convention_override={"p1": "L"},
    )
    # RL: malformed result.json -> invalid without leaking host path
    bad_dir = tdir / "han-test-d00-rl"
    bad_dir.mkdir(parents=True)
    (bad_dir / "result.json").write_text("{not-json", encoding="utf-8")
    # RR: valid complete trial with different decision count -> valid_prediction_differs mentions decisions
    shorter = turns_map[(0, "RR")][:2]
    _write_trial(
        tdir,
        "han-test-d00-rr",
        deck_id=0,
        pairing="RR",
        trial_id=_uuid(62),
        turns=shorter,
        score=4,
    )

    report = collect_mod.collect(native_root, forecast_path)
    by_cell = {(c["deck_id"], c["pairing"]): c for c in report["cells"]}
    assert by_cell[(0, "LL")]["status"] == "invalid"
    assert "policy_audit.jsonl" in by_cell[(0, "LL")]["reason"]
    assert by_cell[(0, "LR")]["status"] == "invalid"
    assert "convention" in by_cell[(0, "LR")]["reason"]
    assert by_cell[(0, "RL")]["status"] == "invalid"
    assert str(tmp_path) not in json.dumps(report)
    assert by_cell[(0, "RR")]["status"] == "valid_prediction_differs"
    assert "decisions" in by_cell[(0, "RR")]["reason"]
