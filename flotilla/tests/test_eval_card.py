"""Zero-cost card integration sweeps use built tasks, not a replacement scorer."""

import json
import subprocess
import sys

from conftest import FLOTILLA, load


def card(tmp_path, family, *options):
    result = subprocess.run(
        [sys.executable, str(FLOTILLA / "eval_card.py"), "--family", family,
         "--instances", "2", "--schedule-seeds", "2", "--jobs", "2", "--seed", "7236499753218553",
         "--out", str(tmp_path), *options], capture_output=True, text=True, check=False, timeout=90)
    data = json.loads((tmp_path / f"{family}.card.json").read_text()) if (
        tmp_path / f"{family}.card.json").is_file() else None
    assert data is not None, result.stderr
    checks = {row["name"]: row["status"] for row in data["checks"]}
    matrix = {(row["level"], row["arm"], row["policy"]): row for row in data["matrix"]}
    return result, data, checks, matrix


def test_fixture_card_sweep(tmp_path):
    result, data, checks, matrix = card(tmp_path, "kit_fixture", "--levels", "canary,standard,heavy")
    assert result.returncode == 0, data["checks"]
    assert data["passed"]
    for name in ("levels", "solvable", "easy alone", "floor", "separation", "determinism", "schedule independence",
                 "no leak", "pairs", "twin rule", "invalid is not zero"):
        assert checks[name] == "PASS", data["checks"]
    assert data["stall"]["stalled"] and data["stall"]["invalid"] and data["stall"]["reward"] is None
    assert checks["dose response"] == checks["chance"] == checks["one seat is not enough"] == "NA"
    assert checks["canary"] == ("NA" if data["static"]["canary"] is None else "PASS")
    assert checks["power plan"] == ("PASS" if data["power"] else "NA")
    assert data["static"]["tasks"] == 24
    assert all(row["effect"] is None for row in data["costs"])
    for level in ("canary", "standard", "heavy"):
        assert matrix[level, "talk-control", "naive"]["min"] == 1
        assert matrix[level, "talk-trap", "naive"]["mean"] == 0
        assert matrix[level, "talk-trap", "careful"]["n"] == 4
    assert all(row["same"] for row in data["replays"])
    assert all(pair["edit_chars"] == 0 for pair in data["static"]["pairs"])
    assert all(row["mean_input_tokens_estimate"] > 0 for row in data["load"])
    text = (tmp_path / "kit_fixture.card.md").read_text()
    assert "## Open risks" in text
    assert "## Three costs" in text and "## Power plan" in text
    if any("versions" in row for row in data["runs"]):
        assert "## Versions" in text


def test_rf1v_card_with_explicit_pair(tmp_path):
    result, data, checks, matrix = card(tmp_path, "probe", "--levels", "canary", "--variant", "verify-readfirst",
                                        "--read-first", "--arms", "talk-quiet,talk-false",
                                        "--pair", "talk-false=talk-quiet", "--policies", "careful,naive")
    assert result.returncode == 0, data["checks"]
    assert checks["separation"] == checks["pairs"] == checks["determinism"] == "PASS"
    assert checks["easy alone"] == checks["floor"] == "NA"
    assert matrix["canary", "talk-false", "naive"]["mean"] == 0
    assert matrix["canary", "talk-quiet", "naive"]["min"] == 1
    assert matrix["canary", "talk-false", "careful"]["min"] == 1
    assert data["identity"]["variant"] == "verify-readfirst"


def test_inverted_pair_fails_but_writes_both_cards(tmp_path):
    result, data, checks, _ = card(tmp_path, "kit_fixture", "--levels", "canary", "--arms",
                                   "talk-control,talk-trap", "--pair", "talk-control=talk-trap")
    assert result.returncode == 1
    assert not data["passed"]
    assert checks["separation"] == "FAIL"
    assert (tmp_path / "kit_fixture.card.md").is_file()


def test_declared_pair_uses_its_own_hook(tmp_path):
    result, data, checks, _ = card(tmp_path, "kit_fixture", "--levels", "canary",
                                   "--arms", "talk-control,talk-trap,hint")
    assert result.returncode == 0, data["checks"]
    assert checks["pairs"] == "PASS"
    pairs = {(p["control"], p["trap"]): p for p in data["static"]["pairs"]}
    assert pairs["talk-control", "talk-trap"]["edit_chars"] == 0
    assert pairs["talk-trap", "hint"]["edit_chars"] > 0


def test_paired_costs_and_power(monkeypatch):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    module = load(FLOTILLA / "eval_card.py", "card_comparisons_test")
    assert module.trap_pairs(("probe-control", "probe-trap-key", "probe-trap-unit", "probe-trap-key-hint"),
                             [("probe-control", "probe-trap-key"), ("probe-trap-unit", "probe-control")], []) == {
                                 "probe-trap-key": "probe-control", "probe-trap-unit": "probe-control"}
    identity = {"known_arms": ("solo", "solo-seam", "team-fullrecord", "team", "hint", "probe-control", "probe-trap"),
                "levels": {"standard": "standard"}, "policies": ("lapse-20", "script-loop"),
                "trap_controls": {"probe-trap": "probe-control"}}
    scores = {"solo": 1, "solo-seam": .5, "team-fullrecord": .75, "team": .25, "hint": 1,
              "probe-control": 1, "probe-trap": 0}
    runs = [{"level": "standard", "policy": policy, "arm": arm, "instance": instance,
             "schedule_seed": schedule, "reward": reward, "invalid": False}
            for policy in identity["policies"] for arm, reward in scores.items()
            for instance in range(2) for schedule in range(3)]
    costs, power = module.comparisons({"identity": identity, "runs": runs})
    assert len(costs) == 6 and len(power) == 4
    assert {row["cost"]: row["effect"] for row in costs} == {"boundary": .5, "teammate": .25, "protocol": .75}
    trap = next(row for row in power if row["treatment"] == "probe-trap")
    assert trap["instances"] == 2 and trap["pairs"] == 6
    assert trap["success_effect"] == 1 and trap["planned_n"] == 6
    assert next(row for row in power if row["treatment"] == "team")["plan_status"] == "no effect"
    assert module.planned_instances(.15, 0) == 52
    invalid = next(run for run in runs if run["arm"] == "team" and run["policy"] == "lapse-20")
    invalid.update(invalid=True, reward=None)
    costs, power = module.comparisons({"identity": identity, "runs": runs})
    boundary = next(row for row in costs if row["cost"] == "boundary" and row["policy"] == "lapse-20")
    assert boundary["pairs"] == 5 and boundary["excluded_pairs"] == 1 and boundary["effect"] == .5
    missing = next(row for row in power if row["treatment"] == "team" and row["policy"] == "lapse-20")
    assert missing["plan_status"] == "NA" and missing["planned_n"] is None


def test_canary_reads_toml_and_seat_views(tmp_path, monkeypatch):
    from argparse import Namespace

    monkeypatch.syspath_prepend(str(FLOTILLA))
    module = load(FLOTILLA / "eval_card.py", "card_canary_test")
    kit = module.Kit(Namespace(family="kit_fixture", variant="", seats=None, read_first=False))
    task, = kit.build(tmp_path, "canary", "canary", ("talk-control",), 1, 7236499753218553)
    marker = "card-canary-0529d306-e554-4d51-b596-fcac65886137"
    monkeypatch.setattr(kit.builder, "CANARY", marker, raising=False)
    assert kit.canary(task) == ["talk-control/0: absent from task.toml"]
    toml = task.path / "task.toml"
    toml.write_text(toml.read_text() + f"\n# {marker}\n")
    assert kit.canary(task) == []
    instruction = task.path / "instructions" / "p2.md"
    instruction.write_text(instruction.read_text() + f"\n{marker}\n")
    assert kit.canary(kit.qa.load_task(task.path)) == ["talk-control/0: present in p2/instruction"]


def test_suite_writes_two_family_cards(tmp_path):
    result = subprocess.run(
        [sys.executable, str(FLOTILLA / "eval_card.py"), "--suite", "schedule,kit_fixture",
         "--levels", "canary", "--arms", "solo", "--instances", "2", "--schedule-seeds", "2",
         "--seed", "7236499753218553", "--out", str(tmp_path)], capture_output=True, text=True, check=False, timeout=90)
    summary = (tmp_path / "SUITE.md").read_text()
    all_passed = True
    for family in ("schedule", "kit_fixture"):
        data = json.loads((tmp_path / f"{family}.card.json").read_text())
        if family == "kit_fixture":
            assert data["passed"], data["checks"]
        all_passed &= data["passed"]
        assert (tmp_path / f"{family}.card.md").is_file()
        counts = " / ".join(str(sum(row["status"] == status for row in data["checks"]))
                            for status in ("PASS", "FAIL", "NA"))
        assert summary.count(f"[{family}]") == 1
        assert f"| [{family}]({family}.card.md) | {counts} |" in summary
    assert result.returncode == int(not all_passed), result.stderr
    assert "PASS / FAIL / NA" in summary and "canary/solo: +0.000" in summary
