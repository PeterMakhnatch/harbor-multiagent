"""The pre-registered cross-model analysis (docs/results/scripts/xm_analysis.py) on seeded synthetic waves: the exact
stratified test, the decision boundaries, model-arm validity, truncation, duplicates, the replication stratum, the
consistency label and the sensitivity analyses."""

from __future__ import annotations

import itertools
import json
import math
import random
from fractions import Fraction

import pytest
from conftest import FLOTILLA, load

XM = load(FLOTILLA / "docs" / "results" / "scripts" / "xm_analysis.py", "flotilla_test_xm_analysis")
SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_xm_split")
COMMONS = load(FLOTILLA / "games" / "commons.py", "flotilla_test_xm_commons")
NEW = ("m/a", "m/b", "m/c")
REPLICATION = "m/rep"
SIMULATION_SEEDS = (1, 7, 20261123, 424242)
OUTSIDE_SUM_FOR_WINDOW = {0: 101, 1: 81, 2: 65, 3: 52}


def window(feasible_rounds):
    needed = OUTSIDE_SUM_FOR_WINDOW[feasible_rounds]
    params = {"capacity": 100, "shrink": [4, 5], "rounds": 6, "seats": ["p1", "p2"],
              "outside": {"p1": needed - 10, "p2": 10}}
    return XM.window_of("split", params)


def row(family, instance, arm, model, success, *, valid=True, started="2026-11-24T01:00:00Z", missed=0,
        seat_rounds=12, job=None, truncated=False, feasible_rounds=1, below=()):
    measures = ({"sustained_share": 0.5, "collapsed_in": None if success else 3, "over_use": 0.1}
                if family == "commons" else
                {"efficiency": 0.9, "deal_round": 1 if success else None, "via": "claim" if success else None,
                 "strong_share": 0.2 if success else None})
    return {"job": job or f"xm1-{family}-{instance}-{arm}-{model}-{started}", "trial": "t", "started": started,
            "family": family, "instance": instance, "arm": arm, "model": model, "valid": valid,
            "reasons": [] if valid else ["no verifier score"], "cost": 0.01, "unpriced_seats": 0,
            "tokens": {"input": 10, "cache": 4, "output": 3}, "seat_rounds": seat_rounds if valid else 0,
            "missed": missed if valid else 0, "passed": 0, "truncated": truncated and valid,
            "success": success if valid else None, "measures": measures if valid else {}, "score": {},
            "window": window(feasible_rounds) if family == "split" else None,
            "below_outside": ([{"seat": seat, "payoff": 1, "outside": 2} for seat in below]
                              if valid and success else [])}


def outcomes(b, c, ties=0, neither=0):
    return [(True, False)] * b + [(False, True)] * c + [(True, True)] * ties + [(False, False)] * neither


def pair_rows(family, model, pairs, first="talk", second="silent", start=0, **kwargs):
    rows = []
    for offset, (one, two) in enumerate(pairs):
        rows += [row(family, start + offset, first, model, one, **kwargs),
                 row(family, start + offset, second, model, two, **kwargs)]
    return rows


def pooled(result, family="split", first="talk", second="silent", contrasts=None):
    found = next(c for c in (result["contrasts"] if contrasts is None else contrasts)
                 if (c["family"], c["a"], c["b_arm"]) == (family, first, second))
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
        by_cell = {XM.cell(r): r for r in counted}
        assert all(by_cell[key] is r for key, r in XM.first_attempts(rows).items())
        for found in result["contrasts"]:
            if found["kind"] != "reported":
                same = found["pooled"]["consistency"]
                assert same["label"] == ("consistent" if same["positive"] >= 2 and not same["reversed"]
                                         else "driven by fewer models")


def test_truncated_split_counts_unplayed_seat_rounds_as_missed_and_no_deal():
    params = SPLIT.generate(1, 8, 2, "b")
    claim_all = {seat: SPLIT.fixed_policy(params["capacity"]) for seat in params["seats"]}
    short = SPLIT.play(params, claim_all, rounds=1)
    found = XM.played("split", params, short, SPLIT.score_game(params, short))
    assert found["truncated"] and (found["seat_rounds"], found["missed"], found["success"]) == (12, 10, False)
    empty = XM.played("split", params, [], SPLIT.score_game(params, []))
    assert empty["truncated"] and (empty["seat_rounds"], empty["missed"], empty["success"]) == (12, 12, False)
    full = SPLIT.play(params, claim_all)
    found = XM.played("split", params, full, SPLIT.score_game(params, full))
    assert not found["truncated"] and (found["seat_rounds"], found["missed"], found["success"]) == (12, 0, False)
    deal = SPLIT.play(params, {seat: SPLIT.fixed_policy(params["outside"][seat]) for seat in params["seats"]})
    found = XM.played("split", params, deal, SPLIT.score_game(params, deal))
    assert not found["truncated"] and (found["seat_rounds"], found["missed"], found["success"]) == (2, 0, True)


def test_v10_inactive_lists_replace_missed_for_validity_and_older_logs_keep_missed():
    params = SPLIT.generate(1, 8, 2, "b")
    claim_all = {seat: SPLIT.fixed_policy(params["capacity"]) for seat in params["seats"]}
    log = [dict(record) for record in SPLIT.play(params, claim_all)]
    log[0]["missed"] = [params["seats"][0]]
    older = XM.played("split", params, log, SPLIT.score_game(params, log))
    assert (older["seat_rounds"], older["missed"]) == (12, 1)
    proposer_only = [record | {"inactive": []} for record in log]
    found = XM.played("split", params, proposer_only, SPLIT.score_game(params, proposer_only))
    assert (found["seat_rounds"], found["missed"]) == (12, 0)
    idle = [record | {"inactive": list(record["missed"])} for record in log]
    found = XM.played("split", params, idle, SPLIT.score_game(params, idle))
    assert (found["seat_rounds"], found["missed"]) == (12, 1)


def test_truncated_commons_survives_when_no_played_round_collapsed():
    params = COMMONS.generate(1, 4, 3, "b")
    planned, seats = params["rounds"], len(params["seats"])
    fair = {seat: COMMONS.fair_policy(params) for seat in params["seats"]}
    short = COMMONS.play(params, fair, rounds=3)
    score = COMMONS.score_game(params, short)
    assert not score["survived"] and score["collapsed_in"] is None and score["truncated"]
    found = XM.played("commons", params, short, score)
    assert found["truncated"] and found["success"]
    assert (found["seat_rounds"], found["missed"]) == (planned * seats, (planned - 3) * seats)
    greedy = COMMONS.play(params, {seat: COMMONS.greedy_policy() for seat in params["seats"]}, rounds=1)
    found = XM.played("commons", params, greedy, COMMONS.score_game(params, greedy))
    assert not found["truncated"] and (found["seat_rounds"], found["missed"], found["success"]) == (seats, 0, False)
    whole = COMMONS.play(params, fair)
    found = XM.played("commons", params, whole, COMMONS.score_game(params, whole))
    assert not found["truncated"] and found["success"] and found["seat_rounds"] == planned * seats


def test_seeded_truncation_keeps_its_invariants():
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        for _ in range(40):
            family = rng.choice(("split", "commons"))
            game = SPLIT if family == "split" else COMMONS
            params = (SPLIT.generate(seed, rng.randrange(12), 2, "b") if family == "split"
                      else COMMONS.generate(seed, rng.randrange(12), 3, "b"))
            top = params["capacity"] if family == "split" else 2 * params["fair_share"]
            policies = {seat: game.fixed_policy(rng.randint(0, top)) for seat in params["seats"]}
            log = [] if rng.random() < 0.1 else game.play(params, policies, rng.randint(1, params["rounds"]))
            if log and rng.random() < 0.3:
                log[0]["missed"] = [params["seats"][0]]
            score = game.score_game(params, log)
            found = XM.played(family, params, log, score)
            planned, seats = params["rounds"], len(params["seats"])
            ended = any(record.get("agreed") or record.get("collapsed") for record in log)
            assert found["truncated"] == (not ended and len(log) < planned)
            assert found["seat_rounds"] == (planned if found["truncated"] else len(log)) * seats
            assert (planned - len(log)) * seats * found["truncated"] <= found["missed"] <= found["seat_rounds"]
            if family == "split":
                assert found["success"] == score["agreed"]
            else:
                assert found["success"] == (score["collapsed_in"] is None)


def test_missed_share_and_truncated_counts_use_counted_trials_only():
    rows = pair_rows("split", "m/a", outcomes(4, 0, ties=4))
    rows[0] |= {"truncated": True, "missed": 1}
    late = row("split", 0, "talk", "m/a", True, started="2026-11-24T02:00:00Z", missed=12, truncated=True)
    result = XM.analyse([*rows, late], NEW, REPLICATION)
    talk = next(a for a in result["arms"] if (a["model"], a["arm"]) == ("m/a", "talk"))
    assert (talk["trials"], talk["valid"], talk["counted"], talk["truncated"]) == (9, 9, 8, 1)
    assert talk["missed_share"] == 1 / 96 and talk["arm_valid"]
    assert len(result["truncated"]) == 2 and result["duplicates"] == [late]


def test_consistency_label_needs_two_valid_new_models_and_no_significant_reversal():
    def label(*per_model, invalid=None):
        rows = []
        for model, pairs in zip(NEW, per_model, strict=True):
            mine = pair_rows("split", model, pairs)
            if model == invalid:
                for r in mine:
                    r["missed"] = 3 if r["arm"] == "talk" else 0
            rows += mine
        test = pooled(XM.analyse(rows, NEW, REPLICATION))[0]
        return test["decision"], test["consistency"]
    decision, same = label(outcomes(12, 0), outcomes(0, 1, ties=5), outcomes(0, 1, ties=5))
    assert decision == "PASS" and (same["positive"], same["label"]) == (1, "driven by fewer models")
    _decision, same = label(outcomes(4, 0, ties=4), outcomes(4, 0, ties=4), outcomes(0, 0, ties=8))
    assert (same["valid_models"], same["positive"], same["label"]) == (3, 2, "consistent")
    _decision, same = label(outcomes(10, 0), outcomes(10, 0), outcomes(0, 6, ties=2))
    assert same["reversed"] == ["m/c"] and same["label"] == "driven by fewer models"
    _decision, same = label(outcomes(4, 0, ties=4), outcomes(0, 0, ties=8), outcomes(4, 0, ties=4), invalid="m/c")
    assert (same["valid_models"], same["positive"], same["label"]) == (2, 1, "driven by fewer models")


def test_first_attempt_sensitivity_drops_pairs_whose_first_attempt_was_invalid(capsys):
    rows = [r for model in NEW for r in pair_rows("split", model, outcomes(3, 0, ties=5))]
    retried = row("split", 0, "talk", "m/a", True, valid=False, started="2026-11-24T00:30:00Z")
    later = row("split", 1, "talk", "m/b", False, started="2026-11-24T03:00:00Z")
    result = XM.analyse([*rows, retried, later], NEW, REPLICATION)
    main = pooled(result)[0]
    assert (main["pairs"], main["b"], main["decision"]) == (24, 9, "PASS")
    first, lines = pooled(result, contrasts=result["sensitivity"]["first_attempt"])
    assert (first["pairs"], first["b"], first["c"], first["decision"]) == (23, 8, 0, "INCONCLUSIVE")
    assert lines["m/b"]["b"] == 3 and {c["family"] for c in result["sensitivity"]["first_attempt"]} == {"split"}
    XM.report(result, [])
    printed = capsys.readouterr().out
    assert "## Sensitivity (reported only; cannot change any decision)" in printed
    assert "INCONCLUSIVE (23 complete valid pairs, fewer than 24) (sensitivity; reported only)" in printed
    assert "(secondary; cannot change the primary decision)" in printed


def test_commons_sensitivity_drops_trials_with_more_than_two_missed_seat_rounds():
    rows = [r for model in NEW for r in pair_rows("commons", model, outcomes(3, 0, ties=5))]
    rows[0]["missed"], rows[2]["missed"] = 3, 2
    result = XM.analyse(rows, NEW, REPLICATION)
    assert pooled(result, "commons")[0]["pairs"] == 24
    fewer = result["sensitivity"]["commons_at_most_2_missed"]
    assert [c["family"] for c in fewer] == ["commons"]
    test, _lines = pooled(result, "commons", contrasts=fewer)
    assert (test["pairs"], test["b"], test["c"]) == (23, 8, 0)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def harbor_trial(jobs, tasks, name, arm, model, log, *, exception=None, instance=0):
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
    write(trial / "verifier" / "score.json", {"error": None, "instance": instance, "condition": arm})
    write(trial / "verifier" / "world-state.json", {"game": {"log": log}})


def test_reads_harbor_trial_folders_by_manifest_and_participant_model(tmp_path, capsys):
    params = SPLIT.generate(1, 0, 2, "b")
    deal = SPLIT.play(params, {seat: SPLIT.fixed_policy(params["outside"][seat]) for seat in params["seats"]})
    stuck = SPLIT.play(params, {seat: SPLIT.fixed_policy(params["capacity"]) for seat in params["seats"]})
    stuck[0]["missed"] = ["p1"]
    tasks, jobs = tmp_path / "tasks", tmp_path / "jobs"
    entries = {}
    for name, arm, instance in (("aaa", "talk", 0), ("bbb", "silent", 0), ("ccc", "talk", 0), ("eee", "silent", 1)):
        write(tasks / "split" / f"t-{name}" / "environment" / "task.json", {"game": params})
        entries[f"flotilla/t-{name}"] = {"family": "split", "instance": instance, "condition": arm,
                                         "path": f"split/t-{name}"}
    write(tasks / "manifest.json", {"format": "flotilla-manifest", "tasks": entries})
    harbor_trial(jobs, tasks, "aaa", "talk", "openrouter/x/y", deal)
    harbor_trial(jobs, tasks, "bbb", "silent", "openrouter/x/y", stuck)
    harbor_trial(jobs, tasks, "ccc", "talk", "openrouter/x/y", deal, exception="AgentTimeoutError")
    harbor_trial(jobs, tasks, "eee", "silent", "openrouter/x/y", stuck[:2], instance=1)
    harbor_trial(jobs, tmp_path / "elsewhere", "ddd", "talk", "openrouter/x/y", deal)
    rows, unmapped = XM.read_trials(jobs, "xm1-*", XM.load_manifests([tasks]))
    by = {r["job"]: r for r in rows}
    assert unmapped == ["xm1-ddd/t-ddd__x"] and set(by) == {"xm1-aaa", "xm1-bbb", "xm1-ccc", "xm1-eee"}
    assert by["xm1-aaa"]["success"] is True and by["xm1-aaa"]["model"] == "openrouter/x/y"
    assert by["xm1-bbb"]["success"] is False and by["xm1-bbb"]["seat_rounds"] == 12 and by["xm1-bbb"]["missed"] == 1
    assert not by["xm1-bbb"]["truncated"] and by["xm1-eee"]["truncated"]
    assert (by["xm1-eee"]["seat_rounds"], by["xm1-eee"]["missed"], by["xm1-eee"]["success"]) == (12, 9, False)
    assert not by["xm1-ccc"]["valid"] and by["xm1-ccc"]["reasons"] == ["harbor exception AgentTimeoutError"]
    assert by["xm1-aaa"]["cost"] == 0.004 and by["xm1-aaa"]["tokens"] == {"input": 200, "cache": 80, "output": 20}
    out = tmp_path / "out.json"
    base = [str(jobs), "xm1-*", "--tasks", str(tasks)]
    with pytest.raises(SystemExit):
        XM.main(base + ["--new-models", "openrouter/x/y"])
    with pytest.raises(SystemExit):
        XM.main(base + ["--new-models", "m/a", "--replication-model", "m/rep"])
    capsys.readouterr()
    XM.main(base + ["--new-models", "openrouter/x/y", "--replication-model", "m/rep", "--json", str(out)])
    printed = capsys.readouterr().out
    excluded = "| 0 | 0 | 0 | 1 | excluded: silent missed share 0.417 > 0.1 |"
    assert "invalid 1" in printed and f"| openrouter/x/y | new {excluded}" in printed
    assert "truncated: xm1-eee/t-eee__x split 1 silent openrouter/x/y: 2 of 6 rounds logged, agreed False" in printed
    assert "| openrouter/x/y | silent | 2 | 2 | 2 | 1 | 0.417 |" in printed and "Sensitivity" in printed
    saved = json.loads(out.read_text())
    assert saved["unmapped"] == unmapped and len(saved["duplicates"]) == 1
    assert by["xm1-aaa"]["window"] == by["xm1-ccc"]["window"] == XM.window_of("split", params)
    assert by["xm1-aaa"]["below_outside"] == [] and "## split: feasibility windows and deal quality" in printed
    assert [entry["instance"] for entry in saved["split_descriptive"]["instances"]] == [0, 1]
    XM.main(base + ["--new-models", "m/a", "--replication-model", "m/rep", "--allow-other"])
    assert f"| openrouter/x/y | other {excluded}" in capsys.readouterr().out


def test_feasibility_window_counts_the_rounds_whose_pot_covers_both_outside_options():
    params = {"capacity": 100, "shrink": [4, 5], "rounds": 6, "seats": ["p1", "p2"], "outside": {"p1": 50, "p2": 30}}
    found = XM.window_of("split", params)
    assert found["pots"] == [100, 80, 64, 51, 40, 32] and found["outside_sum"] == 80
    assert (found["feasible"], found["feasible_rounds"]) == ([1, 2], 2)
    for outside, rounds in ((81, 1), (100, 1), (101, 0), (64, 3), (32, 6), (0, 6)):
        params["outside"] = {"p1": outside // 2, "p2": outside - outside // 2}
        assert XM.window_of("split", params)["feasible_rounds"] == rounds, outside
    assert XM.window_of("commons", params) is None and XM.window_of("split", None) is None
    assert [XM.window_bucket(n) for n in (0, 1, 2, 3, 6)] == ["0", "1", "2+", "2+", "2+"]
    with pytest.raises(AssertionError):
        XM.window_bucket(-1)


def test_seeded_windows_match_the_world_pot_and_the_outside_option_deal():
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        for instance in range(30):
            params = SPLIT.generate(seed, instance, 2, rng.choice(("a", "b")))
            found = XM.window_of("split", params)
            never = SPLIT.play(params, {seat: SPLIT.fixed_policy(params["capacity"] + 1) for seat in params["seats"]})
            assert [record["stock"] for record in never] == found["pots"]
            for number, pot in enumerate(found["pots"], 1):
                fits = SPLIT.settle(pot, params["outside"], params)["agreed"]
                assert fits == (number in found["feasible"]), (seed, instance, number)
            assert found["feasible"] == list(range(1, found["feasible_rounds"] + 1))
            assert found["feasible_rounds"] >= 1


def test_below_outside_flags_a_deal_under_a_seats_own_outside_option():
    params = SPLIT.generate(1, 8, 2, "b")
    outside = params["outside"]
    strong = max(outside, key=outside.get)
    under = {seat: SPLIT.fixed_policy(outside[seat] - (seat == strong)) for seat in params["seats"]}
    log = SPLIT.play(params, under)
    found = XM.played("split", params, log, SPLIT.score_game(params, log))
    assert found["success"] and found["below_outside"] == [
        {"seat": strong, "payoff": outside[strong] - 1, "outside": outside[strong]}]
    exact = SPLIT.play(params, {seat: SPLIT.fixed_policy(outside[seat]) for seat in params["seats"]})
    assert XM.played("split", params, exact, SPLIT.score_game(params, exact))["below_outside"] == []
    stuck = SPLIT.play(params, {seat: SPLIT.fixed_policy(0 if seat == strong else params["capacity"] + 1)
                                for seat in params["seats"]})
    assert XM.played("split", params, stuck, SPLIT.score_game(params, stuck))["below_outside"] == []
    commons = COMMONS.generate(1, 4, 3, "b")
    whole = COMMONS.play(commons, {seat: COMMONS.fair_policy(commons) for seat in commons["seats"]})
    assert XM.played("commons", commons, whole, COMMONS.score_game(commons, whole))["below_outside"] == []
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        for _ in range(40):
            params = SPLIT.generate(seed, rng.randrange(20), 2, rng.choice(("a", "b")))
            claims = {seat: rng.randint(0, params["capacity"] // 2) for seat in params["seats"]}
            log = SPLIT.play(params, {seat: SPLIT.fixed_policy(n) for seat, n in claims.items()})
            found = XM.played("split", params, log, SPLIT.score_game(params, log))
            expected = [seat for seat in params["seats"] if found["success"] and claims[seat] < params["outside"][seat]]
            assert [entry["seat"] for entry in found["below_outside"]] == expected


def test_deal_quality_per_model_arm_splits_deals_by_window_and_counts_rational_deals(capsys):
    plan = ((0, 1, True, ()), (1, 1, True, ("p1",)), (2, 2, False, ()), (3, 3, True, ()), (4, 0, False, ()))
    rows = [row("split", instance, "talk", "m/a", success, feasible_rounds=rounds, below=below)
            for instance, rounds, success, below in plan]
    rows += [row("split", instance, "silent", "m/a", False, feasible_rounds=rounds) for instance, rounds, *_ in plan]
    late = row("split", 3, "talk", "m/a", True, feasible_rounds=3, below=("p2",), started="2026-11-24T05:00:00Z",
               job="xm1-late")
    commons = row("commons", 0, "talk", "m/a", True)
    result = XM.analyse([*rows, late, commons], NEW, REPLICATION)
    found = result["split_descriptive"]
    assert [(w["instance"], w["feasible_rounds"]) for w in found["instances"]] == [(0, 1), (1, 1), (2, 2), (3, 3),
                                                                                   (4, 0)]
    talk = next(entry for entry in found["model_arms"] if entry["arm"] == "talk")
    assert (talk["trials"], talk["deals"], talk["below_outside"], talk["rational_deals"]) == (5, 3, 1, 2)
    assert talk["by_window"] == {"0": {"trials": 1, "deals": 0}, "1": {"trials": 2, "deals": 2},
                                 "2+": {"trials": 2, "deals": 1}}
    assert talk["rational_rate"] == 2 / 5 and talk["rational_share_of_deals"] == 2 / 3
    assert [entry["instance"] for entry in talk["below_outside_trials"]] == [1]
    silent = next(entry for entry in found["model_arms"] if entry["arm"] == "silent")
    assert (silent["deals"], silent["rational_deals"], silent["rational_rate"]) == (0, 0, 0.0)
    assert silent["rational_share_of_deals"] is None
    below = [(entry["job"], entry["counted"]) for entry in found["below_outside_all"]]
    assert below == [(rows[1]["job"], True), ("xm1-late", False)]
    XM.report(result, [])
    printed = capsys.readouterr().out
    assert "| m/a | talk | 5 | 3 | 0 of 1 | 2 of 2 | 1 of 2 | 1 | 2 | 0.400 |" in printed
    assert "- xm1-late/t instance 3 talk m/a: p2 1 < 2 (not counted)" in printed


def test_seeded_waves_keep_deal_quality_invariants():
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        rows = [r | {"window": window(rng.choice((1, 2, 3))) if r["family"] == "split" else None}
                for r in random_wave(rng)]
        windows = {}
        for r in rows:
            if r["family"] == "split":
                r["window"] = windows.setdefault(r["instance"], r["window"])
                if r["success"] and rng.random() < 0.3:
                    r["below_outside"] = [{"seat": "p1", "payoff": 1, "outside": 2}]
        result = XM.analyse(rows, NEW, REPLICATION)
        arms = {(a["model"], a["arm"]): a for a in result["arms"] if a["family"] == "split"}
        for entry in result["split_descriptive"]["model_arms"]:
            assert entry["trials"] == arms[(entry["model"], entry["arm"])]["counted"]
            assert entry["deals"] == arms[(entry["model"], entry["arm"])]["successes"]
            assert entry["below_outside"] + entry["rational_deals"] == entry["deals"]
            assert sum(b["trials"] for b in entry["by_window"].values()) == entry["trials"]
            assert sum(b["deals"] for b in entry["by_window"].values()) == entry["deals"]
            assert entry["by_window"]["0"] == {"trials": 0, "deals": 0}
        counted = sum(entry["counted"] for entry in result["split_descriptive"]["below_outside_all"])
        assert counted == sum(entry["below_outside"] for entry in result["split_descriptive"]["model_arms"])


def test_gated_vs_talk_is_a_primary_contrast_present_only_with_gated_trials(capsys):
    new = [r for model in NEW for r in pair_rows("split", model, outcomes(3, 0, ties=5), first="gated",
                                                 second="talk")]
    rows = [*new, *pair_rows("split", REPLICATION, outcomes(0, 4, ties=4), first="gated", second="talk")]
    result = XM.analyse(rows, NEW, REPLICATION)
    test, lines = pooled(result, "split", "gated", "talk")
    assert (test["pairs"], test["b"], test["c"], test["alpha"], test["min_pairs"]) == (24, 9, 0, 0.025, 24)
    assert test["decision"] == "PASS" and test["p"] == XM.stratified_p([(3, 0)] * 3)
    assert test["models"] == list(NEW) and test["consistency"]["label"] == "consistent"
    assert lines[REPLICATION]["role"] == "replication" and (lines[REPLICATION]["b"], lines[REPLICATION]["c"]) == (0, 4)
    assert pooled(result, "split", "gated", "talk", contrasts=result["sensitivity"]["first_attempt"])[0] == test
    short = pooled(XM.analyse(new[:-2], NEW, REPLICATION), "split", "gated", "talk")[0]
    assert (short["pairs"], short["decision"]) == (23, "INCONCLUSIVE")
    reversed_wave = [r for model in NEW for r in pair_rows("split", model, outcomes(0, 8), first="gated",
                                                           second="talk")]
    assert pooled(XM.analyse(reversed_wave, NEW, REPLICATION), "split", "gated", "talk")[0]["reason"].startswith(
        "reversed")
    quality = {(e["model"], e["arm"]): e for e in result["split_descriptive"]["model_arms"]}
    assert (quality[("m/a", "gated")]["trials"], quality[("m/a", "gated")]["deals"]) == (8, 8)
    assert (quality[("m/a", "talk")]["trials"], quality[("m/a", "talk")]["deals"]) == (8, 5)
    XM.report(result, [])
    assert "split gated vs talk (primary; b = only gated succeeded, c = only talk succeeded" in capsys.readouterr().out
    without = [r for r in rows if r["arm"] != "gated"]
    without += [row("split", r["instance"], "silent", r["model"], False) for r in without]
    result = XM.analyse(without, NEW, REPLICATION)
    contrasts = result["contrasts"] + result["sensitivity"]["first_attempt"]
    assert contrasts and all("gated" not in (c["a"], c["b_arm"]) for c in contrasts)
    assert pooled(result)[0]["pairs"] == 24
    XM.report(result, [])
    assert "gated" not in capsys.readouterr().out


def test_missed_cutoff_one_keeps_a_gated_arm_that_missed_30_percent(capsys):
    rows = []
    for model in NEW:
        mine = pair_rows("split", model, outcomes(3, 0, ties=5), first="gated", second="talk", seat_rounds=10)
        for r in mine:
            r["missed"] = 3 if r["arm"] == "gated" else 0
        rows += mine
    rows += [row("split", 8, "gated", "m/a", True, valid=False), row("split", 8, "talk", "m/a", False, seat_rounds=10)]
    strict = XM.analyse(rows, NEW, REPLICATION)
    assert strict == XM.analyse(rows, NEW, REPLICATION, missed_cutoff=0.10) and "missed_cutoff" not in strict
    test, lines = pooled(strict, "split", "gated", "talk")
    assert (test["pairs"], test["decision"]) == (0, "INCONCLUSIVE")
    assert lines["m/a"]["excluded"] == ["gated missed share 0.300 > 0.1"]
    loose = XM.analyse(rows, NEW, REPLICATION, missed_cutoff=1.0)
    assert loose["missed_cutoff"] == 1.0
    test, lines = pooled(loose, "split", "gated", "talk")
    assert (test["pairs"], test["b"], test["c"], test["decision"]) == (24, 9, 0, "PASS")
    assert lines["m/a"]["excluded"] == [] and lines["m/a"]["pairs"] == 8
    gated = next(a for a in loose["arms"] if (a["model"], a["arm"]) == ("m/a", "gated"))
    assert gated["arm_valid"] and gated["missed_share"] == 0.3 and (gated["trials"], gated["valid"]) == (9, 8)
    assert [r["instance"] for r in loose["invalid"]] == [8]
    XM.report(loose, [])
    assert "model-arm validity: missed share at most 1.0 (--missed-cutoff" in capsys.readouterr().out
    XM.report(strict, [])
    assert "model-arm validity" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        XM.main(["jobs", "x-*", "--tasks", "t", "--new-models", "m/a", "--replication-model", "m/rep",
                 "--missed-cutoff", "1.5"])
