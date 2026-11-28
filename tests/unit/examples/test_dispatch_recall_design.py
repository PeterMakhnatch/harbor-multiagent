import json
import re
from copy import deepcopy

import pytest

from examples.seatlab.dispatch_recall import design as d
from examples.seatlab.dispatch_recall.native import Session

SEEDS = [1, 2, 3, 4, 5, 6]
ONE = {"numerator": 1, "denominator": 1}
ZERO = {"numerator": 0, "denominator": 1}


def _cut(job, commit="correct", report="truthful"):
    s = job["session"]
    session = Session(s["seed"], s["transition"], s["timing"])
    first = session.view("loader")
    session.apply("loader", {"op": "stage", "bay": first["initial_bay"]})
    current = session.view("depot")["current_bay"]
    other = next(bay for bay in first["bays"] if bay != current)
    said = {"truthful": current, "false": other, "silent": None}[report]
    session.apply("depot", {"op": "report", "bay": said})
    if commit is not None:
        ticket = session.view("loader")["ticket"]
        bay = current if commit == "correct" else other
        session.apply("loader", {"op": "commit", "bay": bay, "ticket": ticket})
    return session.snapshot()


def _outcomes(plan, rule):
    def choose(job):
        return rule(job) if callable(rule) else rule[job["session"]["timing"]]

    return {
        job["job_id"]: _cut(job, choose(job))
        for job in plan["jobs"]
        if choose(job) != "absent"
    }


def _p(test):
    return test["two_sided_p"]["numerator"], test["two_sided_p"]["denominator"]


def test_design_balances_blocks_and_keeps_ids_blind():
    plan = d.design([11, 3, 7], 90210)
    jobs = plan["jobs"]
    assert plan["design"] == "free-two-seat-team"
    assert plan["seats"] == {"loader": "free", "depot": "free"}
    assert [job["position"] for job in jobs] == list(range(18))
    for start in range(0, 18, 6):
        block = jobs[start : start + 6]
        assert len({job["block"] for job in block}) == 1
        cells = {(j["session"]["transition"], j["session"]["timing"]) for j in block}
        assert cells == {(w, t) for w in d.WORLDS for t in d.TIMINGS}
        assert all(j["session"]["seed"] == j["block"] for j in block)
    ids = [job["job_id"] for job in jobs]
    assert len(set(ids)) == 18
    assert all(re.fullmatch(r"dr-[0-9a-f]{24}", job_id) for job_id in ids)
    participants = [p for job in jobs for p in job["participants"].values()]
    assert len(set(participants)) == 36
    assert plan == d.design([7, 3, 11], 90210)
    d.check_plan(json.loads(json.dumps(plan)))
    other = d.design([11, 3, 7], 90211)
    assert not set(ids) & {job["job_id"] for job in other["jobs"]}
    plan["contrasts"]["primary"]["control"] = "withheld"
    assert d.design([3], 90210)["contrasts"]["primary"]["control"] == "deferred"


@pytest.mark.parametrize(
    ("seeds", "order_seed"),
    [([1, 1], 5), ([True], 5), ([1], 1), ([-1], 5), ((1,), 5), ([1], True)],
)
def test_design_rejects_invalid_inputs(seeds, order_seed):
    with pytest.raises(ValueError):
        d.design(seeds, order_seed)


def test_check_plan_rejects_edits_and_bool_aliases():
    plan = json.loads(json.dumps(d.design(SEEDS, 77)))
    swapped = deepcopy(plan)
    first, second = swapped["jobs"][0]["session"], swapped["jobs"][1]["session"]
    first["timing"], second["timing"] = second["timing"], first["timing"]
    added = deepcopy(plan)
    added["executed"] = True
    position = deepcopy(plan)
    position["jobs"][1]["position"] = True
    block = deepcopy(plan)
    seed_one = next(job for job in block["jobs"] if job["block"] == 1)
    seed_one["session"]["seed"] = True
    assert position == plan and block == plan
    for edited in (swapped, added, position, block):
        with pytest.raises(ValueError):
            d.check_plan(edited)
        with pytest.raises(ValueError):
            d.assess(edited, {})


def test_empty_plan_and_outcomes_claim_no_execution():
    result = d.assess(d.design([], 4), {})
    assert result["planned_jobs"] == result["supplied_cuts"] == 0
    assert result["committed_cuts"] == 0
    assert result["verdict"] == "no_outcomes"
    for contrast in result["contrasts"].values():
        assert contrast["grade"] == "no_outcomes"
        assert contrast["mean_difference_bounds"] is None
        assert contrast["sample_mean"] == "not_signed"
    unrun = d.assess(d.design(SEEDS, 77), {})
    assert unrun["supplied_cuts"] == 0 and unrun["verdict"] == "no_outcomes"
    assert {r["status"] for r in unrun["assignment"]} == {"absent"}


def test_assess_rejects_unplanned_and_mislinked_cuts():
    plan = d.design(SEEDS, 77)
    job, neighbor = plan["jobs"][0], plan["jobs"][1]
    with pytest.raises(ValueError):
        d.assess(plan, {"dr-" + "0" * 24: _cut(job)})
    with pytest.raises(ValueError):
        d.assess(plan, {job["job_id"]: _cut(neighbor)})
    forged = _cut(job, commit="wrong")
    forged["reward"] = 1
    with pytest.raises(ValueError):
        d.assess(plan, {job["job_id"]: forged})
    relabeled = _cut(job)
    relabeled["bays"] = relabeled["bays"][::-1]
    with pytest.raises(ValueError):
        d.assess(plan, {job["job_id"]: relabeled})


def test_wrong_abstention_and_absence_stay_distinct():
    plan = d.design([5], 77)
    rule = {"immediate": "wrong", "deferred": None, "withheld": "absent"}
    result = d.assess(plan, _outcomes(plan, rule))
    by = {r["session"]["timing"]: r for r in result["assignment"]}
    assert {t: r["status"] for t, r in by.items()} == {
        "immediate": "wrong",
        "deferred": "uncommitted",
        "withheld": "absent",
    }
    assert {t: r["team_reward"] for t, r in by.items()} == {
        "immediate": 0,
        "deferred": 0,
        "withheld": None,
    }
    assert {t: r["physical_reward"] for t, r in by.items()} == {
        "immediate": 0,
        "deferred": None,
        "withheld": None,
    }
    timings = result["timings"]
    assert timings["deferred"]["team_reward"] == {"lower": ZERO, "upper": ZERO}
    assert timings["deferred"]["physical_reward"] == {"lower": ZERO, "upper": ONE}
    assert timings["deferred"]["wrong"] == 0
    assert timings["deferred"]["committed_accuracy"] is None
    assert timings["withheld"]["team_reward"] == {"lower": ZERO, "upper": ONE}
    assert timings["immediate"]["committed_accuracy"] == ZERO
    assert result["supplied_cuts"] == 4 and result["committed_cuts"] == 2


def test_abstention_scores_zero_for_team_reward_but_absence_is_bounded():
    plan = d.design(SEEDS, 77)
    rule = {"immediate": "correct", "deferred": None, "withheld": None}
    abstained = d.assess(plan, _outcomes(plan, rule))
    assert abstained["contrasts"]["primary"]["grade"] == "treated_higher"
    assert abstained["secondary_contrasts"]["primary"]["grade"] == "not_established"
    assert abstained["primary_direction"] == "treated_higher"
    assert abstained["abstention_sensitive"] is True
    assert abstained["verdict"] == "primary_supported_abstention_sensitive"
    assert abstained["comparison_verdict"] == abstained["verdict"]
    rule = {"immediate": "correct", "deferred": "absent", "withheld": None}
    missing = d.assess(plan, _outcomes(plan, rule))
    assert missing["contrasts"]["primary"]["grade"] == "not_established"
    assert missing["verdict"] == "primary_not_established"


def test_positive_grade_requires_the_worst_missing_allocation():
    plan = d.design(SEEDS, 77)
    rule = {"immediate": "correct", "deferred": "wrong", "withheld": "wrong"}
    full = d.assess(plan, _outcomes(plan, rule))
    primary = full["contrasts"]["primary"]
    assert primary["grade"] == "treated_higher"
    assert _p(primary["worst_case_against_treated"]) == (1, 32)
    assert full["contrasts"]["negative_control"]["status"] == "no_violation_detected"
    assert full["verdict"] == "primary_supported"
    wider = d.design([*SEEDS, 7], 77)

    def rule_seven(job):
        timing = job["session"]["timing"]
        if job["block"] == 7:
            return {"immediate": "absent", "deferred": "correct"}.get(timing, "wrong")
        return rule[timing]

    hidden = d.assess(wider, _outcomes(wider, rule_seven))
    primary = hidden["contrasts"]["primary"]
    assert _p(primary["complete_case"]) == (1, 32)
    assert _p(primary["worst_case_against_treated"]) == (1, 8)
    assert primary["grade"] == "not_established"
    outcomes = _outcomes(plan, rule)
    deferred = next(j for j in plan["jobs"] if j["session"]["timing"] == "deferred")
    del outcomes[deferred["job_id"]]
    partial = d.assess(plan, outcomes)
    assert partial["contrasts"]["primary"]["grade"] == "treated_higher"
    assert partial["contrasts"]["negative_control"]["status"] == "incomplete"
    assert partial["verdict"] == "primary_supported_negative_control_incomplete"


def test_too_few_seeds_cannot_establish_and_ties_stay_unestablished():
    plan = d.design(SEEDS[:5], 77)
    rule = {"immediate": "correct", "deferred": "wrong", "withheld": "wrong"}
    result = d.assess(plan, _outcomes(plan, rule))
    assert not result["contrasts"]["primary"]["alpha_reachable"]
    assert result["contrasts"]["primary"]["grade"] == "not_established"
    tied = d.assess(plan, _outcomes(plan, dict.fromkeys(d.TIMINGS, "correct")))
    assert {c["grade"] for c in tied["contrasts"].values()} == {"not_established"}
    assert tied["verdict"] == "primary_not_established"


PLUS_ONE = {
    ("immediate", "stay"): "correct",
    ("immediate", "move"): "correct",
    ("deferred", "stay"): "correct",
    ("deferred", "move"): "wrong",
}
MINUS_TWO = {
    ("immediate", "stay"): "wrong",
    ("immediate", "move"): "wrong",
    ("deferred", "stay"): "correct",
    ("deferred", "move"): "correct",
}


def _split(plan, small, large, swap=False):
    def choose(job):
        timing, world = job["session"]["timing"], job["session"]["transition"]
        if timing == "withheld":
            return "absent"
        if swap:
            timing = "deferred" if timing == "immediate" else "immediate"
        table = small if job["block"] <= 64 else large
        return table.get((timing, world), "absent")

    return d.assess(plan, _outcomes(plan, choose))["contrasts"]["primary"]


def _frac(n, m):
    return {"numerator": n, "denominator": m}


def test_significant_sign_with_opposite_sample_mean_is_not_graded():
    plan = d.design(list(range(1, 101)), 1000)
    forward = _split(plan, PLUS_ONE, MINUS_TWO)
    assert forward["worst_case_against_treated"]["wins"] == 64
    assert forward["worst_case_against_treated"]["losses"] == 36
    assert forward["seed_direction"] == "treated_higher"
    assert forward["mean_difference_bounds"] == {
        "lower": _frac(-1, 25),
        "upper": _frac(-1, 25),
    }
    assert forward["sample_mean"] == "negative"
    assert forward["grade"] == "not_established"
    converse = _split(plan, PLUS_ONE, MINUS_TWO, swap=True)
    assert converse["seed_direction"] == "control_higher"
    assert converse["sample_mean"] == "positive"
    assert converse["grade"] == "not_established"


def test_missing_seeds_bound_the_sample_mean_against_each_direction():
    plan = d.design(list(range(1, 101)), 1000)
    hidden = _split(plan, PLUS_ONE, {})
    assert hidden["complete_seeds"] == 64
    assert hidden["complete_case"]["losses"] == 0
    assert hidden["worst_case_against_treated"]["losses"] == 36
    assert hidden["seed_direction"] == "treated_higher"
    assert hidden["mean_difference_bounds"] == {
        "lower": _frac(-1, 25),
        "upper": _frac(17, 25),
    }
    assert hidden["sample_mean"] == "not_signed"
    assert hidden["grade"] == "not_established"
    converse = _split(plan, PLUS_ONE, {}, swap=True)
    assert converse["seed_direction"] == "control_higher"
    assert converse["mean_difference_bounds"] == {
        "lower": _frac(-17, 25),
        "upper": _frac(1, 25),
    }
    assert converse["sample_mean"] == "not_signed"
    assert converse["grade"] == "not_established"


def test_negative_control_flags_a_robust_difference():
    plan = d.design(SEEDS, 77)
    rule = {"immediate": "correct", "deferred": "correct", "withheld": "wrong"}
    result = d.assess(plan, _outcomes(plan, rule))
    control = result["contrasts"]["negative_control"]
    assert control["grade"] == "treated_higher" and control["status"] == "violated"
    assert result["negative_control"] == "violated"
    assert result["comparison_verdict"] == "negative_control_violated"
    assert result["blind_reference"]["exceeded"] is True
    assert result["verdict"] == "blind_reference_exceeded"
    assert result["contrasts"]["primary"]["grade"] == "not_established"


def test_truthful_report_cells_select_only_actual_truthful_reports():
    plan = d.design([5], 77)
    reports = {"immediate": "truthful", "deferred": "false", "withheld": "silent"}
    outcomes = {}
    for job in plan["jobs"]:
        s = job["session"]
        if (s["transition"], s["timing"]) == ("move", "immediate"):
            continue
        commit = None if s["transition"] == "stay" else "wrong"
        outcomes[job["job_id"]] = _cut(job, commit, reports[s["timing"]])
    cells = d.assess(plan, outcomes)["truthful_report_cells"]
    assert cells["stay:immediate"] == {
        "assigned": 1,
        "selected": 1,
        "correct": 0,
        "wrong": 0,
        "uncommitted": 1,
        "team_reward": {"lower": ZERO, "upper": ZERO},
        "physical_reward": {"lower": ZERO, "upper": ONE},
        "committed_accuracy": None,
    }
    assert cells["move:immediate"]["assigned"] == 1
    assert cells["move:immediate"]["selected"] == 0
    assert cells["move:immediate"]["team_reward"] is None
    for key in ("stay:deferred", "move:deferred", "stay:withheld", "move:withheld"):
        assert cells[key]["assigned"] == 1 and cells[key]["selected"] == 0
