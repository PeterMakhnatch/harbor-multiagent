"""Offline pilot planning with real task bundles and the runner's dry-run path."""

import argparse
import json
import math
import os
import subprocess

import pytest
from conftest import FLOTILLA, load


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    return load(FLOTILLA / "pilot_plan.py")


def power_row(policy, plus, minus, planned_n):
    return {"level": "canary", "policy": policy, "control": "talk-control", "treatment": "talk-trap",
            "pairs": 100, "excluded_pairs": 0, "plus": plus, "minus": minus,
            "success_effect": (plus - minus) / 100, "planned_n": planned_n,
            "plan_status": "planned" if planned_n is not None else "> 1000"}


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True, env={**os.environ,
        "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com"}).strip()


def test_fixture_three_gates_and_runner_forecast(tmp_path, tool, monkeypatch, capsys):
    git(tmp_path, "init", "-q")
    (tmp_path / "protocol.md").write_text(tool.PROTOCOL)
    git(tmp_path, "add", "protocol.md")
    git(tmp_path, "commit", "-qm", "preregister")
    prereg = {"commit": git(tmp_path, "rev-parse", "HEAD"), "doc": "protocol.md", "section": tool.PROTOCOL}
    tool.write_json(tmp_path / "cards/kit_fixture.card.json", {"identity": {"family": "kit_fixture"}, "passed": True,
        "power": [power_row("lapse-10", 20, 0, 38), power_row("lapse-20", 40, 0, 18)]})
    snapshot_file = tmp_path / "prices.json"
    models = ("openrouter/fixture/a", "openrouter/fixture/b")
    tool.write_json(snapshot_file, [{"id": model.removeprefix("openrouter/"), "in_per_m": price,
                                    "cache_read_per_m": price / 10, "out_per_m": price * 5,
                                    "zdr_endpoints": 1} for model, price in zip(models, (2, 4), strict=True)])
    args = argparse.Namespace(family="kit_fixture", levels=("canary",), models=models, cards=tmp_path / "cards",
        out=tmp_path / "plans", calls_per_turn=1, uncertainty=2, playtest_profile=[], cap_usd=None)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = tool.build_plan(args, scratch, tool.PriceSnapshot(snapshot_file), prereg)
    path = args.out / "kit_fixture.json"
    plan = tool.run_wave.load_plan(path)
    assert "PREVIEW ONLY" in plan["prereg"]["section"]
    assert "stages" not in plan
    assert [(row["stage"], row["level"], row["arms"], row["n"]) for row in result["sets"]] == [
        ("A", "canary", ["solo"], 10), ("B", "canary", ["talk-control", "talk-trap"], 12),
        ("C", "canary", ["solo", "team"], 38), ("C", "canary", ["hint"], 38)]
    assert [row["instances"] for row in plan["task_sets"]] == [list(range(10)), list(range(10, 22)),
                                                               list(range(22, 60)), list(range(22, 60))]
    total = tool.totals(result["rows"])
    assert total["trials"] == 2 * (10 + 2 * 12 + 3 * 38)
    assert total["model_seats"] < total["seats"]
    assert plan["budget"]["cap_usd"] == math.ceil(total["model_high"])
    assert {row["arm"] for row in plan["token_profiles"]} == {"solo", "talk-control", "talk-trap", "team", "hint"}
    report = tool.render_forecast([result])
    assert f"| **Total** | {tool.count_text(total)} |" in report
    assert "hint does not match the team roster" in report

    def forbidden(_url):
        pytest.fail("dry-run unexpectedly requested the network")

    monkeypatch.setattr(tool.run_wave, "_public_json", forbidden)
    assert tool.run_wave.main([str(path), "--dry-run", "--history-jobs", str(scratch / "empty-history")]) == 0
    text = capsys.readouterr().out
    tool.check_forecast(text, result["rows"])
    assert f"low / expected / high total: {tool.range_text(total)}" in text


def test_no_probe_pairs_reduced_to_control_and_demonstration(tool):
    arms = ["solo", "team-fullrecord", "team", "hint"]
    design, power = tool.stage_design(arms, [], "canary", {})
    assert [(stage, group, len(instances)) for stage, group, instances in design] == [
        ("A", ["solo", "team-fullrecord"], 10), ("C", ["team-fullrecord", "team", "hint"], 10)]
    assert not power


def test_card_power_uses_smallest_compatible_positive_effect_and_caps_it(tool):
    raw = [power_row("lapse-10", 5, 0, 180), power_row("lapse-20", 40, 0, 18),
           power_row("lapse-30", 70, 0, 10)]
    pair = ("talk-control", "talk-trap")
    rows = tool.power_rows({"power": raw}, "canary", [pair])
    assert tool.compatible_power(rows, {pair: (2, 0, 12)})["n"] == 100
    assert tool.compatible_power(rows, {pair: (6, 0, 12)})["n"] == 18
    assert tool.compatible_power(rows, {pair: (1, 0, 12)}) is None
    assert tool.compatible_power(rows, {pair: (2, 2, 12)}) is None
    design, _ = tool.stage_design(["solo", "team", *pair], [pair], "canary", {"power": raw})
    assert len(design[-1][2]) == 100


def test_price_snapshot_selects_real_per_token_units(tmp_path, tool):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps([{"id": "x/y", "in_per_m": 2, "cache_read_per_m": 0.1,
                                 "out_per_m": 10, "zdr_endpoints": 1}]))
    snapshot = tool.PriceSnapshot(path)
    assert tool.pricing.openrouter_cost("openrouter/x/y", {"miss": 1_000_000, "hit": 1_000_000,
                                                            "out": 1_000_000}, snapshot.prices) == 12.1
