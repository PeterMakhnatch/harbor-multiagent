import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
# Run from the experiment directory: study.py resolves its siblings from there.
_spec = importlib.util.spec_from_file_location("cohort_study", HERE / "study.py")
study = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(study)


def test_registration():
    assert study.SEEDS == (5, 19, 73) and len(study.CELLS) == 15
    assert study.HELD_OUT_SEEDS == tuple(range(101, 113))
    assert len(study.HELD_OUT_CELLS) == 60
    assert not set(study.SEEDS) & set(study.HELD_OUT_SEEDS)
    assert {r[1:] for r in study.HELD_OUT_CELLS if r[1] == 101} == {
        (101, *t) for t in study.TEAMS
    }


def test_world_spec_seed_gate():
    assert study.world_spec(5)["seed"] == 5
    with pytest.raises(SystemExit):
        study.world_spec(101)
    assert study.world_spec(101, study.HELD_OUT_SEEDS)["seed"] == 101
    with pytest.raises(SystemExit):
        study.world_spec(113, study.HELD_OUT_SEEDS)


def _plan(cohort, cell_id):
    seeds, rows = study.COHORTS[cohort]
    row = next(r for r in rows if r[0] == cell_id)
    cell = dict(
        id=row[0],
        game=study.GAME,
        seed=row[1],
        team=row[2],
        speaker=row[3],
        listener=row[4],
        spec=f"seed-{row[1]}.json",
        trial_name=f"{row[0]}-abcdef01",
    )
    plan = {"schema": study.SCHEMA, "seeds": list(seeds), "cells": [cell]}
    if cohort != "initial":
        plan["cohort"] = cohort
    return plan, cell


def test_registered_accepts_both_cohorts():
    for cohort, cid in (
        ("initial", "s5-plain-plain"),
        ("held-out", "s112-plain-blind"),
    ):
        plan, cell = _plan(cohort, cid)
        assert study._registered(plan, cid) == cell


def test_wrong_cohort_refused():
    plan, _ = _plan("held-out", "s101-plain-plain")
    plan["cohort"] = "initial"
    with pytest.raises(SystemExit):
        study._registered(plan, "s101-plain-plain")
    plan, _ = _plan("initial", "s5-plain-plain")
    plan["cohort"] = "held-out"
    with pytest.raises(SystemExit):
        study._registered(plan, "s5-plain-plain")
    plan, _ = _plan("held-out", "s101-plain-plain")
    plan["cohort"] = "other"
    with pytest.raises(SystemExit):
        study._registered(plan, "s101-plain-plain")


def test_tampered_tuple_refused():
    for key, value in (("seed", 5), ("listener", "plain"), ("team", "x")):
        plan, cell = _plan("held-out", "s101-plain-blind")
        cell[key] = value
        with pytest.raises(SystemExit):
            study._registered(plan, "s101-plain-blind")


def test_run_refuses_before_claim(tmp_path):
    plan, _ = _plan("held-out", "s101-plain-plain")
    plan["cohort"] = "initial"
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(SystemExit):
        study.run(tmp_path, "s101-plain-plain", Path(sys.executable))
    assert not (tmp_path / "attempts").exists()
