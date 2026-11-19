"""Focused unit tests for analyze_seatlab.py against the released SeatLab API."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from seatlab.tape import TapeError, canonical, digest

TASK = Path(__file__).resolve().parents[1]
ANALYZER_PATH = TASK / "analyze_seatlab.py"
CELLS = ("A-TT", "A-WT", "A-TW", "B-TW", "B-WT", "B-TT")
COMPARISONS = (
    ("A-TT", "A-WT"),
    ("A-TT", "A-TW"),
    ("B-TT", "B-WT"),
    ("B-TT", "B-TW"),
    ("A-TT", "B-TT"),
)
ROLES = ("chef0", "chef1")
HORIZON = 38
CAPSULE = "2b7961c3e2d7cb17d3af106b54b2e31e68d038aecae015d463725ef37c15aa45"
CASE = "d7d238b6d7d45af932b5bd772479c3951d5704de196887f868eb2859931e0c58"
CHEF0_TAPE = ["north"] + ["west"] * 13 + ["stay"] * 24
CHEF1_TAPE = ["east"] + ["interact"] * 14 + ["stay"] * 23


@pytest.fixture
def analyzer():
    spec = importlib.util.spec_from_file_location("analyze_seatlab", ANALYZER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, obj: object) -> None:
    path.write_bytes(canonical(obj) + b"\n")


def _episode(
    tid: str, role: str, arm: str, *, basis: str = "bound", closed: bool = True
) -> dict[str, object]:
    actions = (
        (CHEF0_TAPE if role == "chef0" else CHEF1_TAPE)
        if arm[ROLES.index(role)] == "T"
        else ["stay"] * HORIZON
    )
    frames = []
    for tick, act in enumerate(actions):
        view = {
            "tick": tick,
            "state": {"timestep": tick, "arm": "init" if tick == 0 else arm},
        }
        frames.append(
            {
                "turn": tick + 1,
                "basis": basis,
                "view": view,
                "view_sha256": digest(view),
                "observed": {"action": act},
            }
        )
    ep: dict[str, object] = {
        "schema": "seatlab.episode/v1",
        "id": f"kitchen:{tid}:{role}",
        "case_sha256": CASE,
        "role": role,
        "native_trial_id": tid,
        "closed": closed,
        "frames": frames,
    }
    ep["episode_sha256"] = digest(ep)
    return ep


def _study(
    root: Path,
    *,
    measured: bool,
    capsule: str = CAPSULE,
    ids: dict[str, str] | None = None,
    basis: str = "bound",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    tids = {c: (ids or {}).get(c, str(uuid4())) for c in CELLS}
    cells, by_id = [], {}
    for cid in CELLS:
        if not measured:
            rec = {
                "id": cid,
                "status": "missing",
                "reason": "invalid_native_evidence",
                "native_trial_id": tids[cid],
            }
        else:
            arm = cid[2:]
            eps = [_episode(tids[cid], r, arm, basis=basis) for r in ROLES]
            (root / f"{cid}.jsonl").write_bytes(
                b"".join(canonical(ep) + b"\n" for ep in eps)
            )
            rec = {
                "id": cid,
                "status": "measured",
                "native_trial_id": tids[cid],
                "case_sha256": CASE,
                "reward": 1 if arm == "TT" else 0,
                "deliveries_by_seat": {"chef0": 0, "chef1": int(arm == "TT")},
                "episodes": 2,
                "turns_per_role": HORIZON,
                "episode_file": f"{cid}.jsonl",
                "episode_sha256": {
                    r: eps[i]["episode_sha256"] for i, r in enumerate(ROLES)
                },
            }
        cells.append(rec)
        by_id[cid] = rec
    comps = []
    for src, tgt in COMPARISONS:
        cid = f"{src}-vs-{tgt}"
        base = {
            "id": cid,
            "source": src,
            "target": tgt,
            "source_native_trial_id": tids[src],
            "target_native_trial_id": tids[tgt],
        }
        if not measured:
            comps.append({**base, "manifest": None, "reason": "no_trusted_case"})
        else:
            _write(
                root / f"{cid}.json",
                {
                    "schema": "seatlab.episode-pairs/v1",
                    "pairs": [
                        {
                            "id": f"{cid}-{r}",
                            "case_sha256": CASE,
                            "role": r,
                            "source": {
                                "native_trial_id": tids[src],
                                "episode_sha256": by_id[src]["episode_sha256"][r],
                            },
                            "target": {
                                "native_trial_id": tids[tgt],
                                "episode_sha256": by_id[tgt]["episode_sha256"][r],
                            },
                        }
                        for r in ROLES
                    ],
                },
            )
            comps.append({**base, "manifest": f"{cid}.json"})
    index = {
        "schema": "seatlab.kitchen-study/v1",
        "study_id": str(uuid4()),
        "capsule_sha256": capsule,
        "cells": cells,
        "measured_trials": 6 if measured else 0,
        "comparisons": comps,
    }
    _write(root / "index.json", index)
    return root / "index.json"


def test_analyzes_missing_original_and_measured_control_cohorts(analyzer, tmp_path):
    orig = _study(tmp_path / "private-orig", measured=False)
    ctrl = _study(tmp_path / "private-ctrl", measured=True)
    report = analyzer.analyze(orig, ctrl)
    raw = json.dumps(report)
    assert (
        str(tmp_path) not in raw
        and "private-orig" not in raw
        and "private-ctrl" not in raw
    )

    assert report["validation"] == {
        "same_frozen_capsule_sha256": True,
        "distinct_study_ids": True,
        "distinct_uuids_across_studies": True,
        "total_distinct_trial_uuids": 12,
        "original_denominator_slots": 6,
        "control_denominator_slots": 6,
        "all_measured_frames_bound": True,
    }
    assert (
        report["original_cohort"]["measured_trials"] == 0
        and report["original_cohort"]["missing_trials"] == 6
    )
    assert all(
        c["reward"] is None and c["total_deliveries"] is None
        for c in report["original_cohort"]["cells"]
    )
    assert all(
        a["reward_0"] is None and a["total_deliveries"] is None
        for a in report["original_cohort"]["team_outcomes_by_arm"].values()
    )

    ctrl_out = report["control_cohort"]
    assert ctrl_out["team_outcomes_by_arm"]["TT"]["reward_1"] == 2
    assert ctrl_out["team_outcomes_by_arm"]["WT"]["reward_0"] == 2
    assert ctrl_out["team_outcomes_by_arm"]["TW"]["reward_0"] == 2
    comps = {c["id"]: c for c in ctrl_out["comparisons"]}

    for wt in ("A-TT-vs-A-WT", "B-TT-vs-B-WT"):
        c0, c1 = comps[wt]["roles"]["chef0"], comps[wt]["roles"]["chef1"]
        assert (
            c0["role_relation"] == "changed_actor"
            and c0["first_unequal_action_at_equal_input"]["world_tick"] == 0
        )
        assert (
            c0["first_unequal_input"]["world_tick"] == 1
            and c0["direct_native_equal_actions"] == 24
        )
        assert c0["two_script_baseline"]["same_actions_over_38_ticks"] == 24
        assert (
            c1["role_relation"] == "unchanged_actor"
            and c1["first_unequal_action_at_equal_input"] is None
        )
        assert (
            c1["first_unequal_input"]["world_tick"] == 1
            and c1["direct_native_equal_actions"] == 38
        )

    for tw in ("A-TT-vs-A-TW", "B-TT-vs-B-TW"):
        c0, c1 = comps[tw]["roles"]["chef0"], comps[tw]["roles"]["chef1"]
        assert (
            c0["role_relation"] == "unchanged_actor"
            and c0["first_unequal_input"]["world_tick"] == 1
            and c0["direct_native_equal_actions"] == 38
        )
        assert (
            c1["role_relation"] == "changed_actor"
            and c1["first_unequal_action_at_equal_input"]["world_tick"] == 0
        )
        assert (
            c1["direct_native_equal_actions"] == 23
            and c1["two_script_baseline"]["same_actions_over_38_ticks"] == 23
        )

    rep = comps["A-TT-vs-B-TT"]
    assert rep["kind"] == "between_block_repeatability"
    assert all(
        rep["roles"][r]["first_boundary"]["status"] == "matched"
        and rep["roles"][r]["direct_native_equal_actions"] == 38
        for r in ROLES
    )


def test_rejects_uuid_reuse_capsule_mismatch_and_short_denominator(analyzer, tmp_path):
    shared = str(uuid4())
    with pytest.raises(ValueError):
        analyzer.analyze(
            _study(tmp_path / "o1", measured=False, ids={"A-TT": shared}),
            _study(tmp_path / "c1", measured=True, ids={"B-TT": shared}),
        )
    with pytest.raises(ValueError):
        analyzer.analyze(
            _study(tmp_path / "o2", measured=False, capsule="1" * 64),
            _study(tmp_path / "c2", measured=True, capsule="2" * 64),
        )
    o3, c3 = (
        _study(tmp_path / "o3", measured=False),
        _study(tmp_path / "c3", measured=True),
    )
    doc = json.loads(o3.read_bytes())
    doc["cells"] = doc["cells"][:5]
    _write(o3, doc)
    with pytest.raises(ValueError):
        analyzer.analyze(o3, c3)


def test_rejects_unbound_duplicate_pairing_and_tampered_digest(analyzer, tmp_path):
    o1, c1 = (
        _study(tmp_path / "o1", measured=False),
        _study(tmp_path / "c1", measured=True, basis="reconstructed"),
    )
    with pytest.raises(ValueError):
        analyzer.analyze(o1, c1)

    o2, c2 = (
        _study(tmp_path / "o2", measured=False),
        _study(tmp_path / "c2", measured=True),
    )
    m = c2.parent / "A-TT-vs-A-WT.json"
    mdoc = json.loads(m.read_bytes())
    mdoc["pairs"][1] = dict(mdoc["pairs"][0])
    _write(m, mdoc)
    with pytest.raises(TapeError):
        analyzer.analyze(o2, c2)

    o3, c3 = (
        _study(tmp_path / "o3", measured=False),
        _study(tmp_path / "c3", measured=True),
    )
    ep_file = c3.parent / "A-TT.jsonl"
    lines = ep_file.read_bytes().splitlines()
    ep0 = json.loads(lines[0])
    ep0["frames"][0]["observed"]["action"] = "stay"
    ep_file.write_bytes(canonical(ep0) + b"\n" + lines[1] + b"\n")
    with pytest.raises(TapeError):
        analyzer.analyze(o3, c3)


def test_cli_stdout_and_exclusive_out(tmp_path):
    orig, ctrl = (
        _study(tmp_path / "o", measured=False),
        _study(tmp_path / "c", measured=True),
    )
    res = subprocess.run(
        [
            sys.executable,
            str(ANALYZER_PATH),
            "--original",
            str(orig),
            "--control",
            str(ctrl),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(res.stdout)
    assert (
        payload["schema"] == "seatlab.kitchen-analysis/v1"
        and str(tmp_path) not in res.stdout
    )

    out = tmp_path / "out.json"
    subprocess.run(
        [
            sys.executable,
            str(ANALYZER_PATH),
            "--original",
            str(orig),
            "--control",
            str(ctrl),
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(out.read_text(encoding="ascii")) == payload
    assert (
        subprocess.run(
            [
                sys.executable,
                str(ANALYZER_PATH),
                "--original",
                str(orig),
                "--control",
                str(ctrl),
                "--out",
                str(out),
            ],
            check=False,
            capture_output=True,
        ).returncode
        != 0
    )
