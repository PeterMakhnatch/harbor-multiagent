"""The pre-registered cross-model analysis (docs/results/scripts/xm_analysis.py) on seeded synthetic waves: the exact
stratified test, the decision boundaries, model-arm validity, duplicates and the replication stratum."""

from __future__ import annotations

import itertools
import json
import math
import random
from fractions import Fraction

from conftest import FLOTILLA, load

XM = load(FLOTILLA / "docs" / "results" / "scripts" / "xm_analysis.py", "flotilla_test_xm_analysis")
SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_xm_split")
NEW = ("m/a", "m/b", "m/c")
REPLICATION = "m/rep"
SIMULATION_SEEDS = (1, 7, 20261123, 424242)


def row(family, instance, arm, model, success, *, valid=True, started="2026-11-24T01:00:00Z", missed=0,
        seat_rounds=12, job=None):
    measures = ({"sustained_share": 0.5, "collapsed_in": None if success else 3, "over_use": 0.1}
                if family == "commons" else
                {"efficiency": 0.9, "deal_round": 1 if success else None, "via": "claim" if success else None,
                 "strong_share": 0.2 if success else None})
    return {"job": job or f"xm1-{family}-{instance}-{arm}-{model}-{started}", "trial": "t", "started": started,
            "family": family, "instance": instance, "arm": arm, "model": model, "valid": valid,
            "reasons": [] if valid else ["no verifier score"], "cost": 0.01, "unpriced_seats": 0,
            "tokens": {"input": 10, "cache": 4, "output": 3}, "seat_rounds": seat_rounds if valid else 0,
            "missed": missed if valid else 0, "passed": 0, "success": success if valid else None,
            "measures": measures if valid else {}, "score": {}}


def outcomes(b, c, ties=0, neither=0):
    return [(True, False)] * b + [(False, True)] * c + [(True, True)] * ties + [(False, False)] * neither


def pair_rows(family, model, pairs, first="talk", second="silent", start=0, **kwargs):
    rows = []
    for offset, (one, two) in enumerate(pairs):
        rows += [row(family, start + offset, first, model, one, **kwargs),
                 row(family, start + offset, second, model, two, **kwargs)]
    return rows


def pooled(result, family="split", first="talk", second="silent"):
    found = next(c for c in result["contrasts"] if (c["family"], c["a"], c["b_arm"]) == (family, first, second))
    return found["pooled"], {line["model"]: line for line in found["models"]}


def brute_force(strata):
    total = sum(b + c for b, c in strata)
    observed = abs(2 * sum(b for b, _c in strata) - total)
    extreme = sum(abs(2 * sum(flips) - total) >= observed for flips in itertools.product((0, 1), repeat=total))
    return float(Fraction(extreme, 2**total))


def test_stratified_p_equals_brute_force_enumeration_and_mcnemar_for_one_stratum():
    assert XM.stratified_p([]) == 1.0 and XM.stratified_p([(0, 0), (0, 0)]) == 1.0
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        for _ in range(60):
            strata = [(rng.randint(0, 4), rng.randint(0, 4)) for _ in range(rng.randint(1, 4))]
            while sum(b + c for b, c in strata) > 14:
                strata.pop()
            p = XM.stratified_p(strata)
            assert p == brute_force(strata), (seed, strata)
            assert 0 < p <= 1
            mirrored = XM.stratified_p([(c, b) for b, c in strata])
            assert p == mirrored
            if len(strata) == 1:
                assert math.isclose(p, XM.WAVE.exact_mcnemar(*strata[0]), rel_tol=1e-12)


def test_decision_labels_at_the_pair_and_alpha_boundaries():
    assert XM.decide(23, 23, 0, 1e-7, 0.025, 24)[0] == "INCONCLUSIVE"
    assert XM.decide(24, 10, 1, 0.025, 0.025, 24)[0] == "PASS"
    assert XM.decide(24, 10, 1, 0.0250001, 0.025, 24)[0] == "FAIL"
    assert XM.decide(24, 1, 10, 0.02, 0.025, 24) == ("FAIL", "reversed: c > b and p <= 0.025")
    assert XM.decide(24, 4, 4, 1.0, 0.025, 24)[0] == "FAIL"
    assert XM.decide(17, 17, 0, 1e-5, 0.05, 18)[0] == "INCONCLUSIVE"
    assert XM.decide(18, 6, 0, 0.03125, 0.05, 18)[0] == "PASS"
    assert XM.TESTS == {"primary": (0.025, 24), "secondary": (0.05, 18)}


def test_pooled_decision_end_to_end_at_24_pairs():
    strong = [*pair_rows("split", "m/a", outcomes(3, 0, ties=5)), *pair_rows("split", "m/b", outcomes(3, 0, ties=5)),
              *pair_rows("split", "m/c", outcomes(3, 0, ties=5))]
    test, lines = pooled(XM.analyse(strong, NEW, REPLICATION))
    assert (test["pairs"], test["b"], test["c"], test["decision"]) == (24, 9, 0, "PASS")
    assert test["p"] == XM.stratified_p([(3, 0)] * 3) == brute_force([(3, 0)] * 3)
    assert all(line["label"] == "does not replicate" for line in lines.values())
    short = strong[:-2]
    test, _lines = pooled(XM.analyse(short, NEW, REPLICATION))
    assert (test["pairs"], test["decision"]) == (23, "INCONCLUSIVE")
    one_model = [*pair_rows("split", "m/a", outcomes(6, 0, ties=2)), *pair_rows("split", "m/b", outcomes(0, 0, ties=8)),
                 *pair_rows("split", "m/c", outcomes(0, 0, neither=8))]
    test, lines = pooled(XM.analyse(one_model, NEW, REPLICATION))
    assert (test["pairs"], test["b"], test["c"], test["p"]) == (24, 6, 0, 0.03125)
    assert test["decision"] == "FAIL" and lines["m/a"]["label"] == "replicates"
    reversed_wave = [*pair_rows("commons", "m/a", outcomes(0, 8)), *pair_rows("commons", "m/b", outcomes(1, 7)),
                     *pair_rows("commons", "m/c", outcomes(0, 8))]
    test, _lines = pooled(XM.analyse(reversed_wave, NEW, REPLICATION), "commons")
    assert test["decision"] == "FAIL" and test["reason"].startswith("reversed")


def test_secondary_propose_needs_18_pairs_and_alpha_005():
    rows = [r for model in NEW for r in pair_rows("split", model, outcomes(2, 0, ties=4), first="propose")]
    test, _lines = pooled(XM.analyse(rows, NEW, REPLICATION), "split", "propose")
    assert (test["pairs"], test["b"], test["c"], test["p"], test["decision"]) == (18, 6, 0, 0.03125, "PASS")
    assert pooled(XM.analyse(rows[:-2], NEW, REPLICATION), "split", "propose")[0]["decision"] == "INCONCLUSIVE"


def test_invalid_model_arm_is_excluded_and_ten_percent_is_still_valid():
    edge = pair_rows("split", "m/a", outcomes(4, 0, ties=4), missed=1, seat_rounds=10)
    over = pair_rows("split", "m/b", outcomes(4, 0, ties=4))
    for r in over:
        if r["arm"] == "talk":
            r["missed"] = 3
    broken = row("split", 0, "silent", "m/c", True, valid=False)
    clean = pair_rows("split", "m/c", outcomes(4, 0, ties=4))
    result = XM.analyse([*edge, *over, broken, *clean], NEW, REPLICATION)
    arms = {(a["model"], a["arm"]): a for a in result["arms"]}
    assert arms[("m/a", "talk")]["missed_share"] == 0.10 and arms[("m/a", "talk")]["arm_valid"]
    assert arms[("m/b", "talk")]["missed_share"] == 0.25 and not arms[("m/b", "talk")]["arm_valid"]
    assert arms[("m/c", "silent")]["trials"] == 9
    assert arms[("m/c", "silent")]["valid"] == 8 and arms[("m/c", "silent")]["arm_valid"]
    test, lines = pooled(result)
    assert lines["m/b"]["pairs"] == 0 and lines["m/b"]["label"].startswith("excluded: talk missed share 0.250")
    assert (test["pairs"], test["b"], test["c"]) == (16, 8, 0)
    assert [r["job"] for r in result["invalid"]] == [broken["job"]]


def test_duplicates_count_the_first_valid_trial_by_start_time():
    cell = [row("split", 0, "talk", "m/a", True, valid=False, started="2026-11-24T00:00:00Z"),
            row("split", 0, "talk", "m/a", False, started="2026-11-24T01:00:00Z"),
            row("split", 0, "talk", "m/a", True, started="2026-11-24T02:00:00Z")]
    silent = row("split", 0, "silent", "m/a", False)
    for seed in SIMULATION_SEEDS:
        rows = [*cell, silent]
        random.Random(seed).shuffle(rows)
        result = XM.analyse(rows, NEW, REPLICATION)
        assert sorted(r["started"] for r in result["duplicates"]) == ["2026-11-24T00:00:00Z", "2026-11-24T02:00:00Z"]
        assert cell[1]["job"] + "/t" in result["counted"] and cell[2]["job"] + "/t" not in result["counted"]
        _test, lines = pooled(result)
        assert (lines["m/a"]["pairs"], lines["m/a"]["b"], lines["m/a"]["c"]) == (1, 0, 0)


def test_replication_and_other_models_stay_out_of_the_pool():
    new = [r for model in NEW for r in pair_rows("commons", model, outcomes(2, 1, ties=5))]
    alone = pooled(XM.analyse(new, NEW, REPLICATION), "commons")[0]
    extra = [*pair_rows("commons", REPLICATION, outcomes(12, 0)), *pair_rows("commons", "m/other", outcomes(9, 0))]
    test, lines = pooled(XM.analyse([*new, *extra], NEW, REPLICATION), "commons")
    assert test == alone and test["models"] == list(NEW)
    assert lines[REPLICATION]["role"] == "replication" and lines[REPLICATION]["label"] == "replicates"
    assert lines["m/other"]["role"] == "other" and lines[REPLICATION]["p"] == XM.WAVE.exact_mcnemar(12, 0)


def random_wave(rng):
    rows = []
    for family, arms in (("split", ("silent", "talk", "propose")), ("commons", ("silent", "talk"))):
        for model in (*NEW, REPLICATION):
            for instance, arm in itertools.product(range(12), arms):
                for copy in range(rng.choice((0, 1, 1, 1, 2))):
                    rows.append(row(family, instance, arm, model, rng.random() < 0.5, valid=rng.random() < 0.9,
                                    started=f"2026-11-24T0{copy}:{rng.randint(10, 59)}:00Z",
                                    missed=rng.choice((0, 0, 0, 1, 4)), job=f"xm1-{len(rows):06x}"))
    return rows


def test_seeded_waves_keep_every_invariant():
    for seed in SIMULATION_SEEDS:
        rows = random_wave(random.Random(seed))
        result = XM.analyse(rows, NEW, REPLICATION)
        counted = [r for r in rows if f"{r['job']}/t" in result["counted"]]
        assert all(r["valid"] for r in counted)
        assert len({XM.cell(r) for r in counted}) == len(counted)
        lone_invalid = [r for r in rows if not r["valid"] and sum(XM.cell(o) == XM.cell(r) for o in rows) == 1]
        assert len(counted) + len(result["duplicates"]) + len(lone_invalid) == len(rows)
        arms = {(a["family"], a["model"], a["arm"]): a for a in result["arms"]}
        for found in result["contrasts"]:
            for line in found["models"]:
                if line["excluded"]:
                    assert line["pairs"] == 0
                if found["kind"] != "reported":
                    assert line["b"] + line["c"] <= line["pairs"]
            if found["kind"] == "reported":
                continue
            test = found["pooled"]
            pool = [line for line in found["models"] if line["model"] in NEW]
            assert test["pairs"] == sum(line["pairs"] for line in pool) and REPLICATION not in test["models"]
            assert test["p"] == XM.stratified_p([(line["b"], line["c"]) for line in pool])
            for line in pool:
                valid = all(arms.get((found["family"], line["model"], arm), {}).get("arm_valid", False)
                            for arm in (found["a"], found["b_arm"]))
                assert valid == (not line["excluded"])
        assert math.isclose(result["total_cost"], 0.01 * len(rows))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def harbor_trial(jobs, tasks, name, arm, model, log, *, exception=None):
    task_dir = tasks / "split" / f"t-{name}"
    trial = jobs / f"xm1-{name}" / f"t-{name}__x"
    config = {"task": {"path": str(task_dir)},
              "participants": [{"model_name": model, "seat": seat} for seat in ("p1", "p2")]}
    write(trial / "config.json", config)
    write(trial / "result.json", {
        "config": config, "started_at": f"2026-11-24T00:00:0{len(name) % 10}Z",
        "exception_info": {"exception_type": exception} if exception else None,
        "seat_results": [{"seat": seat, "exception_info": None, "agent_result": {
            "n_input_tokens": 100, "n_cache_tokens": 40, "n_output_tokens": 10, "cost_usd": 0.002}}
            for seat in ("p1", "p2")]})
    write(trial / "verifier" / "score.json", {"error": None, "instance": 0, "condition": arm})
    write(trial / "verifier" / "world-state.json", {"game": {"log": log}})


def test_reads_harbor_trial_folders_by_manifest_and_participant_model(tmp_path, capsys):
    params = SPLIT.generate(1, 0, 2, "b")
    deal = SPLIT.play(params, {seat: SPLIT.fixed_policy(params["outside"][seat]) for seat in params["seats"]})
    stuck = SPLIT.play(params, {seat: SPLIT.fixed_policy(params["capacity"]) for seat in params["seats"]})
    stuck[0]["missed"] = ["p1"]
    tasks, jobs = tmp_path / "tasks", tmp_path / "jobs"
    entries = {}
    for name, arm in (("aaa", "talk"), ("bbb", "silent"), ("ccc", "talk")):
        write(tasks / "split" / f"t-{name}" / "environment" / "task.json", {"game": params})
        entries[f"flotilla/t-{name}"] = {"family": "split", "instance": 0, "condition": arm, "path": f"split/t-{name}"}
    write(tasks / "manifest.json", {"format": "flotilla-manifest", "tasks": entries})
    harbor_trial(jobs, tasks, "aaa", "talk", "openrouter/x/y", deal)
    harbor_trial(jobs, tasks, "bbb", "silent", "openrouter/x/y", stuck)
    harbor_trial(jobs, tasks, "ccc", "talk", "openrouter/x/y", deal, exception="AgentTimeoutError")
    harbor_trial(jobs, tmp_path / "elsewhere", "ddd", "talk", "openrouter/x/y", deal)
    rows, unmapped = XM.read_trials(jobs, "xm1-*", XM.load_manifests([tasks]))
    by = {r["job"]: r for r in rows}
    assert unmapped == ["xm1-ddd/t-ddd__x"] and set(by) == {"xm1-aaa", "xm1-bbb", "xm1-ccc"}
    assert by["xm1-aaa"]["success"] is True and by["xm1-aaa"]["model"] == "openrouter/x/y"
    assert by["xm1-bbb"]["success"] is False and by["xm1-bbb"]["seat_rounds"] == 12 and by["xm1-bbb"]["missed"] == 1
    assert not by["xm1-ccc"]["valid"] and by["xm1-ccc"]["reasons"] == ["harbor exception AgentTimeoutError"]
    assert by["xm1-aaa"]["cost"] == 0.004 and by["xm1-aaa"]["tokens"] == {"input": 200, "cache": 80, "output": 20}
    out = tmp_path / "out.json"
    XM.main([str(jobs), "xm1-*", "--tasks", str(tasks), "--new-models", "openrouter/x/y", "--json", str(out)])
    printed = capsys.readouterr().out
    assert "invalid 1" in printed and "| openrouter/x/y | new | 1 | 1 | 0 | 1 | does not replicate |" in printed
    saved = json.loads(out.read_text())
    assert saved["unmapped"] == unmapped and len(saved["duplicates"]) == 1
