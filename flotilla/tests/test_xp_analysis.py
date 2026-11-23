"""The split cross-play analysis (docs/results/scripts/xp_analysis.py) on seeded synthetic trial folders: the deal
matrix, the self against cross sign test with ties and dropped instances, the role-balanced advantage and the
model-arm validity exclusion."""

from __future__ import annotations

import json
import math
import random
from fractions import Fraction

from conftest import FLOTILLA, load

XP = load(FLOTILLA / "docs" / "results" / "scripts" / "xp_analysis.py", "flotilla_test_xp_analysis")
SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_xp_split")
A, B, C = "or/a", "or/b", "or/c"
SIMULATION_SEEDS = (3, 11, 20261123)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class Wave:
    def __init__(self, root, instances=8, arm="talk"):
        self.tasks, self.jobs, self.arm, self.count = root / "tasks", root / "jobs", arm, 0
        self.params = {i: SPLIT.generate(1, i, 2, "b") for i in range(instances)}
        entries = {}
        for i, params in self.params.items():
            write(self.tasks / "split" / f"t-{arm}{i}" / "environment" / "task.json", {"game": params})
            entries[f"flotilla/t-{arm}{i}"] = {"family": "split", "instance": i, "condition": arm,
                                               "path": f"split/t-{arm}{i}"}
        write(self.tasks / "manifest.json", {"format": "flotilla-manifest", "tasks": entries})

    def log(self, instance, claims=None, missed=()):
        params = self.params[instance]
        amounts = claims or (params["capacity"], params["capacity"])
        log = SPLIT.play(params, {seat: SPLIT.fixed_policy(n) for seat, n in zip(("p1", "p2"), amounts, strict=True)})
        for record in log:
            record["missed"] = list(missed)
        return log

    def trial(self, instance, models, *, deal=False, claims=None, missed=(), exception=None, messages=()):
        params = self.params[instance]
        if deal and claims is None:
            claims = (params["outside"]["p1"], params["outside"]["p2"])
        log = self.log(instance, claims if deal else None, missed)
        self.count += 1
        trial = self.jobs / f"xp-{self.count:06x}" / f"t-{self.arm}{instance}__x"
        config = {"task": {"path": str(self.tasks / "split" / f"t-{self.arm}{instance}")},
                  "participants": [{"model_name": m, "seat": s} for s, m in zip(("p1", "p2"), models, strict=True)]}
        write(trial / "config.json", config)
        write(trial / "result.json", {
            "config": config, "started_at": f"2026-11-24T00:{self.count // 60:02d}:{self.count % 60:02d}Z",
            "exception_info": {"exception_type": exception} if exception else None,
            "seat_results": [{"seat": s, "exception_info": None, "agent_result": {"cost_usd": 0.001}}
                             for s in ("p1", "p2")]})
        write(trial / "verifier" / "score.json", {"error": None, "instance": instance, "condition": self.arm})
        write(trial / "verifier" / "world-state.json", {"game": {"log": log}, "messages": list(messages)})

    def analyse(self):
        rows, unmapped = XP.XM.read_trials(self.jobs, "xp-*", XP.XM.load_manifests([self.tasks]), reader=XP.trial_row)
        assert unmapped == []
        return XP.analyse(rows)


def test_matrix_and_sign_test_with_ties_and_dropped_instances(tmp_path):
    wave = Wave(tmp_path)
    plan = {0: {(A, A): True, (B, B): True, (A, B): False, (B, A): False},
            1: {(A, A): True, (B, B): False, (A, B): True, (B, A): False},
            2: {(A, A): False, (B, B): False, (A, B): True, (B, A): True},
            3: {(A, A): True},
            5: {(A, A): True, (B, B): True, (A, B): False, (B, A): True}}
    for instance, cells in plan.items():
        for models, deal in cells.items():
            wave.trial(instance, models, deal=deal)
    wave.trial(4, (A, A), deal=True)
    wave.trial(4, (A, B), deal=True, exception="AgentTimeoutError")
    wave.trial(0, (A, A), deal=False)
    result = wave.analyse()
    arm = result["arms"][0]
    cells = arm["matrix"]["cells"]
    assert {key: (c["deals"], c["trials"]) for key, c in cells.items()} == {
        f"{A} vs {A}": (5, 6), f"{A} vs {B}": (2, 4), f"{B} vs {A}": (2, 4), f"{B} vs {B}": (2, 4)}
    test = arm["self_vs_cross"]
    assert (test["n_plus"], test["n_minus"], test["ties"], test["dropped"]) == (2, 1, 1, [3, 4])
    assert test["p"] == 1.0 and test["mean_self"] == 0.625 and test["mean_cross"] == 0.5
    assert len(result["duplicates"]) == 1 and len(result["invalid"]) == 1


def test_role_balanced_advantage_survives_a_role_swap(tmp_path):
    wave = Wave(tmp_path)
    expected = []
    for instance in range(3):
        params = wave.params[instance]
        pot = params["capacity"]
        ab, ba = (pot // 2, pot // 4), (pot // 4, pot // 2) if instance != 1 else (pot // 2, pot // 4)
        wave.trial(instance, (A, B), deal=True, claims=ab)
        wave.trial(instance, (B, A), deal=True, claims=ba)
        expected.append(Fraction(ab[0] + ba[1] - ba[0] - ab[1], pot))
    wave.trial(3, (A, B), deal=True, claims=(10, 10))
    wave.trial(3, (B, A), deal=False)
    result = wave.analyse()
    pair = result["arms"][0]["advantage"][0]
    assert (pair["a"], pair["b"], pair["n"], pair["n_plus"], pair["ties"]) == (A, B, 3, 2, 1)
    assert pair["mean"] == float(sum(expected) / 3) and [x["instance"] for x in pair["instances"]] == [0, 1, 2]
    shares = {(s["model"], s["play"], s["role"]): s for s in result["arms"][0]["role_shares"]}
    assert set(shares) == {(A, "cross", "strong"), (A, "cross", "weak"), (B, "cross", "strong"), (B, "cross", "weak")}
    assert sum(s["n"] for s in shares.values()) == 2 * 8


def test_validity_exclusion_drops_every_cell_of_the_failing_model(tmp_path):
    wave = Wave(tmp_path, instances=2)
    for instance in range(2):
        wave.trial(instance, (A, A), deal=True)
        wave.trial(instance, (A, B), deal=False)
        wave.trial(instance, (A, C), deal=False, missed=("p2",))
        wave.trial(instance, (C, C), deal=True)
    result = wave.analyse()
    table = {(m["model"], m["arm"]): m for m in result["model_arms"]}
    assert table[(C, "talk")]["missed_share"] == 12 / 16 and not table[(C, "talk")]["valid"]
    assert table[(A, "talk")]["missed_share"] == 0 and table[(A, "talk")]["seats_played"] == 8
    assert result["excluded"] == [[C, "talk"]]
    test = result["arms"][0]["self_vs_cross"]
    assert (test["n_plus"], test["ties"], test["dropped"]) == (2, 0, [])
    assert [c["cross_cells"] for c in test["instances"]] == [1, 1]
    assert result["arms"][0]["matrix"]["cells"][f"{A} vs {C}"] == {"p1": A, "p2": C, "deals": 0, "trials": 2}


def test_truncated_cross_trial_counts_unplayed_rounds_for_both_seat_models(tmp_path):
    wave = Wave(tmp_path, instances=1)
    params = wave.params[0]
    wave.trial(0, (A, B), deal=False)
    trial = next(wave.jobs.glob("xp-*/*/"))
    log = SPLIT.play(params, {s: SPLIT.fixed_policy(params["capacity"]) for s in ("p1", "p2")}, rounds=2)
    write(trial / "verifier" / "world-state.json", {"game": {"log": log}, "messages": [
        {"from": "p1", "to": "all", "t": 1.0, "text": f"My outside option is {params['outside']['p1']}."},
        {"from": "p2", "to": "all", "t": 2.0, "text": f"My outside option is {params['outside']['p2'] + 5}."}]})
    result = wave.analyse()
    row = result["rows"][0]
    assert row["truncated"] and row["seat_missed"] == {"p1": 4, "p2": 4} and row["success"] is False
    assert {(m["model"], m["missed_share"]) for m in result["model_arms"]} == {(A, 4 / 6), (B, 4 / 6)}
    assert result["speech"] == [{"speaker": A, "listener": B, "true": 1, "inflated": 0, "deflated": 0},
                                {"speaker": B, "listener": A, "true": 0, "inflated": 1, "deflated": 0}]


def test_seeded_cross_play_waves_keep_their_invariants(tmp_path):
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        wave = Wave(tmp_path / str(seed), instances=6)
        for instance in range(6):
            for models in ((A, A), (B, B), (A, B), (B, A)):
                if rng.random() < 0.8:
                    pot = wave.params[instance]["capacity"]
                    claims = (rng.randint(0, pot // 2), rng.randint(0, pot // 2))
                    wave.trial(instance, models, deal=rng.random() < 0.6, claims=claims)
        result = wave.analyse()
        arm = result["arms"][0]
        test = arm["self_vs_cross"]
        assert test["n_plus"] + test["n_minus"] + test["ties"] == len(test["instances"])
        assert sorted([k["instance"] for k in test["instances"]] + test["dropped"]) == list(range(6))
        for k in test["instances"]:
            assert math.isclose(k["d"], k["self"] - k["cross"], abs_tol=1e-12)
        for pair in arm["advantage"]:
            assert pair["n_plus"] + pair["n_minus"] + pair["ties"] == pair["n"]
            assert all(-2 <= x["adv"] <= 2 for x in pair["instances"])
        swapped = [r | {"pair": [B if m == A else A for m in r["pair"]]} for r in result["rows"]]
        for r in swapped:
            r["model"] = " vs ".join(r["pair"])
        mirror = XP.analyse(swapped)["arms"][0]
        for one, other in zip(arm["advantage"], mirror["advantage"], strict=True):
            assert [x["adv"] for x in one["instances"]] == [-x["adv"] for x in other["instances"]]
        cells = arm["matrix"]["cells"]
        assert sum(c["trials"] for c in cells.values()) == len(result["counted"])


def test_deal_quality_per_ordered_cell_and_self_against_cross_play(tmp_path, capsys):
    wave = Wave(tmp_path, instances=3)

    def under(instance):
        outside = wave.params[instance]["outside"]
        strong = max(outside, key=outside.get)
        return tuple(outside[seat] - (seat == strong) for seat in ("p1", "p2")), strong

    wave.trial(0, (A, A), deal=True, claims=under(0)[0])
    wave.trial(0, (A, B), deal=True)
    wave.trial(1, (A, B), deal=True, claims=under(1)[0])
    wave.trial(1, (B, A), deal=False)
    wave.trial(2, (B, B), deal=True)
    wave.trial(2, (B, B), deal=True, claims=under(2)[0])
    result = wave.analyse()
    quality = result["arms"][0]["deal_quality"]
    cells = {(c["p1"], c["p2"]): c for c in quality["cells"]}
    assert (cells[(A, A)]["deals"], cells[(A, A)]["below_outside"], cells[(A, A)]["rational_deals"]) == (1, 1, 0)
    assert tuple(cells[(A, B)][k] for k in ("trials", "deals", "below_outside", "rational_deals")) == (2, 2, 1, 1)
    assert cells[(B, A)]["rational_rate"] == 0.0 and cells[(B, A)]["rational_share_of_deals"] is None
    assert (cells[(B, B)]["trials"], cells[(B, B)]["below_outside"]) == (1, 0)
    play = {p["play"]: p for p in quality["play"]}
    assert tuple(play["self"][k] for k in ("trials", "deals", "below_outside", "rational_deals")) == (2, 2, 1, 1)
    assert tuple(play["cross"][k] for k in ("trials", "deals", "below_outside", "rational_deals")) == (3, 2, 1, 1)
    windows = {w["instance"]: {k: v for k, v in w.items() if k != "instance"} for w in result["split_windows"]}
    assert windows == {i: XP.XM.window_of("split", wave.params[i]) for i in range(3)}
    for kind in play.values():
        expected = {bucket: {"trials": 0, "deals": 0} for bucket in XP.XM.WINDOW_BUCKETS}
        for row in (r for r in result["rows"] if f"{r['job']}/{r['trial']}" in result["counted"]):
            if (row["pair"][0] == row["pair"][1]) == (kind["play"] == "self"):
                entry = expected[XP.XM.window_bucket(windows[row["instance"]]["feasible_rounds"])]
                entry["trials"] += 1
                entry["deals"] += row["success"]
        assert kind["by_window"] == expected
    below = [(e["instance"], e["seats"], e["counted"]) for e in result["below_outside_all"]]
    expected = []
    for instance in range(3):
        strong = under(instance)[1]
        outside = wave.params[instance]["outside"][strong]
        expected.append((instance, [{"seat": strong, "payoff": outside - 1, "outside": outside}], instance != 2))
    assert below == expected
    XP.report(result, [], 0)
    printed = capsys.readouterr().out
    assert "| self play | - | 2 | 2 |" in printed and "## feasibility windows and below-outside deals" in printed
