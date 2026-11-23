"""The per-model mixed-motive scorecard (docs/results/scripts/scorecard.py) on seeded synthetic Harbor trial folders:
the table numbers against an independent count, an invalid model-arm, a truncated trial, speech rates, cost, and the
comma-separated job pattern."""

from __future__ import annotations

import json
import math
import random

from conftest import FLOTILLA, load

SC = load(FLOTILLA / "docs" / "results" / "scripts" / "scorecard.py", "flotilla_test_scorecard")
SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_scorecard_split")
COMMONS = load(FLOTILLA / "games" / "commons.py", "flotilla_test_scorecard_commons")
SIMULATION_SEEDS = (1, 7, 20261123)
SEAT_COST = 0.003


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class Suite:
    def __init__(self, root):
        self.jobs, self.tasks, self.entries, self.count = root / "jobs", root / "tasks", {}, 0

    def trial(self, family, instance, arm, model, params, log, *, prefix="xm1", messages=(), exception=None,
              minute=0):
        self.count += 1
        name = f"{self.count:06d}"
        task_dir = self.tasks / family / f"t-{name}"
        write(task_dir / "environment" / "task.json", {"game": params})
        self.entries[name] = {"family": family, "instance": instance, "condition": arm, "path": f"{family}/t-{name}"}
        trial = self.jobs / f"{prefix}-{name}" / f"t-{name}__x"
        config = {"task": {"path": str(task_dir)},
                  "participants": [{"model_name": model, "seat": seat} for seat in params["seats"]]}
        write(trial / "config.json", config)
        write(trial / "result.json", {
            "config": config, "started_at": f"2026-11-24T00:{minute:02d}:{self.count % 60:02d}Z",
            "exception_info": {"exception_type": exception} if exception else None,
            "seat_results": [{"seat": seat, "exception_info": None, "agent_result": {"cost_usd": SEAT_COST}}
                             for seat in params["seats"]]})
        write(trial / "verifier" / "score.json", {"error": None, "instance": instance, "condition": arm})
        write(trial / "verifier" / "world-state.json", {"game": {"log": log}, "messages": list(messages)})
        return trial

    def manifest(self):
        write(self.tasks / "manifest.json", {"format": "flotilla-manifest", "tasks": self.entries})
        return SC.XM.load_manifests([self.tasks])


def split_game(instance, success, rounds=None):
    params = SPLIT.generate(1, instance, 2, "b")
    amounts = params["outside"] if success else {seat: params["capacity"] for seat in params["seats"]}
    return params, SPLIT.play(params, {seat: SPLIT.fixed_policy(amounts[seat]) for seat in params["seats"]}, rounds)


def commons_game(instance, success):
    params = COMMONS.generate(1, instance, 3, "b")
    policy = COMMONS.fair_policy(params) if success else COMMONS.greedy_policy()
    return params, COMMONS.play(params, {seat: policy for seat in params["seats"]})


def message(seat, text):
    return {"from": seat, "id": f"m-{seat}", "t": 1.0, "text": text}


def build_wave(suite, rng, model, instances):
    """Both families, both arms, random outcomes; returns the outcomes by (family, arm, instance)."""
    outcomes = {}
    for family, game in (("split", split_game), ("commons", commons_game)):
        for instance in range(instances):
            for arm in ("silent", "talk"):
                success = rng.random() < (0.7 if arm == "talk" else 0.3)
                params, log = game(instance, success)
                talk = []
                if arm == "talk" and family == "commons":
                    talk = [message("p1", f"I will request {params['fair_share']} this round."),
                            message("p2", "I will request 0 this round.")]
                if arm == "talk" and family == "split":
                    strong = max(params["outside"], key=params["outside"].get)
                    talk = [message(strong, f"My outside option is {params['outside'][strong] + rng.choice((0, 3))}.")]
                suite.trial(family, instance, arm, model, params, log, messages=talk)
                outcomes[(family, arm, instance)] = success
    return outcomes


def mcnemar(outcomes, family, instances):
    b = sum(outcomes[(family, "talk", i)] and not outcomes[(family, "silent", i)] for i in range(instances))
    c = sum(outcomes[(family, "silent", i)] and not outcomes[(family, "talk", i)] for i in range(instances))
    return b, c, SC.XM.WAVE.exact_mcnemar(b, c)


def test_seeded_waves_give_the_independently_counted_table(tmp_path):
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        suite = Suite(tmp_path / str(seed))
        instances = rng.randint(3, 6)
        outcomes = build_wave(suite, rng, "m/a", instances)
        rows, unmapped = SC.read_suite(suite.jobs, ["xm1-*"], suite.manifest())
        assert not unmapped and len(rows) == 4 * instances
        line = SC.scorecard(suite.jobs, rows, suite.manifest(), ("m/a",), None)["models"][0]
        for family in ("split", "commons"):
            for arm in ("silent", "talk"):
                wins = sum(outcomes[(family, arm, i)] for i in range(instances))
                assert line[f"{family}_{arm}"]["n"] == instances and line[f"{family}_{arm}"]["valid"]
                assert math.isclose(line[f"{family}_{arm}"]["rate"], wins / instances)
            b, c, p = mcnemar(outcomes, family, instances)
            assert (line[f"{family}_paired"]["b"], line[f"{family}_paired"]["c"]) == (b, c)
            assert math.isclose(line[f"{family}_paired"]["p"], p)
        talks = sum(outcomes[("commons", "talk", i)] for i in range(instances))
        assert line["pledges"] == 2 * instances and math.isclose(line["pledge_kept_rate"], talks / (2 * instances))
        assert line["outside_statements"] == instances and line["invalid_arms"] == []
        assert (line["trials"], line["truncated"], line["missed_share"]) == (4 * instances, 0, 0.0)
        assert math.isclose(line["cost_total"], SEAT_COST * instances * (2 * 2 + 2 * 3))
        assert math.isclose(line["cost_mean"], line["cost_total"] / (4 * instances))


def test_invalid_arm_prints_a_dash_and_hides_its_family_test(tmp_path, capsys):
    suite = Suite(tmp_path)
    build_wave(suite, random.Random(3), "m/a", 3)
    for instance in range(3):
        for arm in ("silent", "talk"):
            params, log = commons_game(instance, True)
            if arm == "silent":
                for record in log[:3]:
                    record["missed"] = list(params["seats"])
            suite.trial("commons", instance, arm, "m/b", params, log)
            suite.trial("split", instance, arm, "m/b", *split_game(instance, arm == "talk"))
    suite.manifest()
    SC.main([str(suite.jobs), "xm1-*", "--tasks", str(suite.tasks), "--new-models", "m/a,m/b",
             "--replication-model", "none", "--json", str(tmp_path / "out.json")])
    printed = capsys.readouterr().out
    saved = {line["model"]: line for line in json.loads((tmp_path / "out.json").read_text())["models"]}
    bad = saved["m/b"]
    assert bad["invalid_arms"] == ["commons silent"] and bad["commons_silent"]["rate"] is None
    assert bad["commons_paired"]["b"] is None and bad["commons_paired"]["excluded"]
    assert bad["commons_talk"]["rate"] == 1.0 and bad["split_paired"]["b"] == 3
    row = next(text for text in printed.splitlines() if text.startswith("| m/b |"))
    assert "| – (3) / 1.000 (3) |" in row and "| – / 0.000 |" in row and "commons silent |" in row
    assert "Suite cost: $" in printed and "m/a $" in printed and "m/b $" in printed


def test_truncated_split_trial_counts_as_no_deal_and_its_unplayed_rounds_as_missed(tmp_path):
    suite = Suite(tmp_path)
    for instance in range(6):
        params, log = split_game(instance, False, rounds=5 if instance == 0 else None)
        suite.trial("split", instance, "silent", "m/a", params, log)
    params, log = split_game(0, True)
    suite.trial("split", 0, "propose", "m/a", params, log)
    rows, _unmapped = SC.read_suite(suite.jobs, ["xm1-*"], suite.manifest())
    result = SC.scorecard(suite.jobs, rows, suite.manifest(), ("m/a",), None)
    line = result["models"][0]
    assert line["truncated"] == 1 and line["split_silent"]["rate"] == 0.0 and line["split_silent"]["valid"]
    assert line["missed_share"] == 2 / 72 and len(result["truncated"]) == 1
    assert result["other_arms_cost"] == {"trials": 1, "total": 2 * SEAT_COST}
    assert result["suite_cost"]["trials"] == 6 and line["split_talk"]["n"] == 0


def test_comma_separated_pattern_reads_each_glob_once_and_skips_the_smoke_jobs(tmp_path):
    suite = Suite(tmp_path)
    for prefix in ("xm1", "xm1g", "xm1s"):
        params, log = split_game(0, True)
        suite.trial("split", 0, "talk", f"m/{prefix}", params, log, prefix=prefix)
    tasks = suite.manifest()
    assert SC.patterns_of(" xm1-??????, xm1g-??????,") == ["xm1-??????", "xm1g-??????"]
    rows, _unmapped = SC.read_suite(suite.jobs, SC.patterns_of("xm1-??????,xm1g-??????,xm1-*"), tasks)
    assert sorted(r["model"] for r in rows) == ["m/xm1", "m/xm1g"]
    rows, _unmapped = SC.read_suite(suite.jobs, SC.patterns_of("xm1*"), tasks)
    assert len(rows) == 3
