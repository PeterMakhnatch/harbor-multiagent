"""Declared profiles cover unseen task kinds, levels, arms and individual model seats."""

import json

import pytest
from test_cost_forecast import (
    ONE,
    TWO,
    Fetch,
    catalog,
    forecast,
    line,
    make_plan,
    manifest,
    row,
    solo,
    usage,
)
from test_run_wave import RUN, plan_for


def profile(**overrides):
    return {"kind": "digest", "miss": 1000, "hit": 2000, "out": 500, "source": "simulation", "uncertainty": 2,
            **overrides}


@pytest.mark.parametrize("bad", [None, {}, [], [None], [profile(miss=True)], [profile(hit=-1)],
                                  [profile(out=float("nan"))], [profile(uncertainty=0.9)],
                                  [profile(source=" ")], [profile(role=None)], [profile(n=0)],
                                  [profile(unknown=1)], [profile(), profile()], [profile(miss=0, hit=0, out=0)]])
def test_plan_refuses_malformed_profiles(tmp_path, bad):
    plan = plan_for([0], ["a"], [ONE])
    plan["token_profiles"] = bad
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(SystemExit, match="token_profiles"):
        RUN.load_plan(path)


def test_declared_profiles_price_level_and_role_and_exclude_scripted_seats(tmp_path):
    repo, jobs = tmp_path / "repo", tmp_path / "jobs"
    manifest(repo, "s1", "digest", [1, 2], ["a"], seats=["p3", "p2", "p1"], scripted=["p2"])
    path = repo / ".flotilla-local/tasks/s1/manifest.json"
    data = json.loads(path.read_text())
    for entry in data["tasks"].values():
        entry["build"]["difficulty"] = "heavy"
    path.write_text(json.dumps(data))
    plan = make_plan(assignments=[[ONE, TWO]])
    plan["token_profiles"] = [profile(), profile(level="heavy", role="p3", miss=3000, uncertainty=3)]
    prices = Fetch(catalog(*((model, "0.000001", "0.000002", "0.0000001") for model in (ONE, TWO))))
    lines = forecast(plan, repo, jobs, prices)
    assert row(lines, ONE).endswith("$0.002200/trial, total $0.0044 [declared profile (simulation)]")
    assert row(lines, TWO).endswith("$0.004200/trial, total $0.0084 [declared profile (simulation)]")
    assert line(lines, "low / expected / high") == "  low / expected / high total: $0.0092 / $0.0170 / $0.0382"


def test_history_wins_but_other_kind_does_not(tmp_path):
    repo, jobs = tmp_path / "repo", tmp_path / "jobs"
    manifest(repo, "s1", "digest", [1, 2], ["a"])
    plan = make_plan([ONE], env="docker")
    plan["token_profiles"] = [profile()]
    prices = Fetch(catalog((ONE, "0.000001", "0.000002", "0.0000001")))
    solo(jobs, "other", "probe.verify", ONE, usage(100, 0, 50, 0.5))
    assert "declared profile" in row(forecast(plan, repo, jobs, prices), ONE)
    solo(jobs, "same", "digest", TWO, usage(100, 0, 50, 0.5))
    lines = forecast(plan, repo, jobs, prices)
    assert "price-estimate digest" in row(lines, ONE)
    assert line(lines, "low / expected / high") == "  low / expected / high total: $0.0004 / $0.0004 / $0.0004"


def test_missing_catalog_is_unknown_not_zero(tmp_path):
    repo, jobs = tmp_path / "repo", tmp_path / "jobs"
    manifest(repo, "s1", "digest", [1, 2], ["a"])
    plan = make_plan([ONE, TWO])
    plan["token_profiles"] = [profile()]
    lines = forecast(plan, repo, jobs, Fetch(catalog((ONE, "0.000001", "0.000002", None))))
    assert "cost UNKNOWN" in row(lines, TWO)
    assert "UNKNOWN" in line(lines, "low / expected / high")
