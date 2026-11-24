"""Exercise both decision CLIs with synthetic staged and fixed-size analysis files."""

import hashlib
import json

import pytest
from conftest import FLOTILLA, load

SCRIPTS = FLOTILLA / "docs" / "results" / "scripts"
RR1 = load(SCRIPTS / "rr1_decide.py")
IO1 = load(SCRIPTS / "io1_decide.py")
LUNA = "openrouter/openai/gpt-6-luna"
QUIET, FALSE = "talk-quiet", "talk-false"


def write_json(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def staged_files(tmp_path, *, luna="pass", new_passes=2):
    plan = {"name": "rr1", "models": [LUNA, *(f"openrouter/example/model-{i}" for i in range(6))],
            "task_sets": [{"instances": list(range(48))}],
            "stages": {"fractions": [0.25, 0.5, 1.0], "alpha": 0.05, "family": "model",
                       "futility": {"delta": 0.25, "beta_f": 0.05},
                       "tests": [{"task_set": 0, "arms": [QUIET, FALSE], "outcome": "correct"}]}}
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    spec = plan["stages"]
    models, analysis = {}, []
    for i, model in enumerate(plan["models"]):
        outcome = luna if i == 0 else "pass" if i <= new_passes else "reverse" if i == 5 else "tie"
        b, c = {"pass": (12, 0), "reverse": (0, 12), "tie": (0, 0)}[outcome]
        status = "efficacy" if b + c else "nonbinding_futility"
        p = 3 / 2048 if b + c else 1.0
        counted = []
        for pair in range(13):
            for arm in (QUIET, FALSE):
                if pair == 12 and arm == QUIET:
                    continue
                trial = f"{i}-{pair}-{arm}"
                if pair < 12:
                    counted.append(f"rr1-abcdef/{trial}")
                analysis.append({"job": "rr1-abcdef", "trial": trial, "model": model, "arm": arm,
                                 "counted": True, "adopted_wrong": arm == FALSE and (outcome == "pass" or pair == 12)})
        models[model] = {"history": [[b, c, 12]],
                         "looks": [{"stage": 1, "look": 1, "planned_pairs": 12, "b": b, "c": c, "n": 12,
                                    "counted": counted}],
                         "disposition": {"status": status, "p": p, "direction": (b > c) - (b < c),
                                         "look": 1, "n": 12}}
    stages = {"plan": "rr1", "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
              **{k: spec[k] for k in ("fractions", "alpha", "family", "futility")},
              "family_size": 1, "stages": 3, "stages_done": 3,
              "tests": [{**spec["tests"][0], "test": 1, "looks": [12, 24, 48], "look_stages": [1, 2, 3],
                         "models": models}]}
    stage_path = tmp_path / "stages.json"
    write_json(stage_path, stages)
    analysis_path = write_json(tmp_path / "analysis.json", {"rows": analysis})
    return ["--plan", str(plan_path), "--stages", str(stage_path)], stage_path, stages, analysis_path


@pytest.mark.parametrize(("luna", "new_passes", "reading"), [
    ("pass", 2, "common"), ("pass", 1, "Luna habit"), ("pass", 0, "Luna habit"), ("tie", 2, "not repeated"),
])
def test_rr1_staged_decisions_and_readings(tmp_path, capsys, luna, new_passes, reading):
    args, _, _, analysis_path = staged_files(tmp_path, luna=luna, new_passes=new_passes)
    RR1.main([*args, "--analysis", analysis_path, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["reading"] == reading
    assert all(term in result["error_statement"] for term in ("0.15", "7.5%", "12.5%", "independence"))
    rows = result["models"]
    assert len(rows) == 7
    assert rows[0]["model"] == LUNA
    assert rows[0]["decision"] == ("PASS" if luna == "pass" else "FAIL")
    assert sum(row["decision"] == "PASS" for row in rows[1:]) == new_passes
    assert {key: rows[5][key] for key in ("decision", "direction", "status", "p", "look", "b", "c", "n")} == {
        "decision": "FAIL", "direction": -1, "status": "efficacy", "p": 3 / 2048,
        "look": 1, "b": 0, "c": 12, "n": 12,
    }
    through = rows[1 if new_passes else 0]["pass_through"]
    assert (through["w"], through["n"]) == (12, 12)
    assert through["ci"] == pytest.approx([0.757506, 1])
    assert rows[5]["pass_through"]["w"] == 0
    RR1.main(args)
    text = capsys.readouterr().out
    assert f"Section 16 reading: {reading}." in text
    assert "direction -1 -> FAIL" in text and result["error_statement"] in text


@pytest.mark.parametrize(("field", "value"), [
    ("plan_sha256", "wrong"), ("fractions", [0.5, 1.0]), ("alpha", 0.01), ("family", "holm"),
    ("family_size", 7), ("futility", None), ("stages", 2), ("arms", [FALSE, QUIET]),
    ("looks", [12, 48]), ("look_stages", [1, 3]), ("outcome", "exposed"), ("models", {}),
])
def test_rr1_refuses_mismatched_stages(tmp_path, field, value):
    args, path, stages, _ = staged_files(tmp_path)
    target = stages if field in stages else stages["tests"][0]
    target[field] = value
    write_json(path, stages)
    with pytest.raises(SystemExit, match=f"{field} differs? from the rr1 plan"):
        RR1.main([*args, "--json"])


def test_rr1_does_not_treat_an_open_look_as_a_failure(tmp_path, capsys):
    args, path, stages, _ = staged_files(tmp_path)
    stages["stages_done"] = 1
    stages["tests"][0]["models"][LUNA]["disposition"].update(status="continue", p=1, direction=0)
    write_json(path, stages)
    RR1.main([*args, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["models"][0]["decision"] == "INCOMPLETE"
    assert result["reading"] == "undecided"


def io_summary(model, p, *, pairs=32):
    return {"model": model, "primary": {"a": QUIET, "b_arm": FALSE, "pairs": pairs, "b": 7, "c": 0, "p": p,
                                       "rate_a": 1.0, "rate_b": (pairs - 7) / pairs, "decision": "FAIL"},
            "pass_through": {"w": 7, "n": pairs}}


def test_io1_adjusts_per_model_and_reads_reliance_from_rr1(tmp_path, capsys):
    args, _, stages, _ = staged_files(tmp_path, new_passes=0)
    RR1.main([*args, "--json"])
    reliance = write_json(tmp_path / "reliance.json", json.loads(capsys.readouterr().out))
    other = next(model for model in stages["tests"][0]["models"] if model != LUNA)

    def run(models):
        plan = write_json(tmp_path / "io1-plan.json", {"models": models})
        v = write_json(tmp_path / "io1v.json", {"summaries": [io_summary(model, 0.015625) for model in models]})
        s = write_json(tmp_path / "io1s.json", {"summaries": [io_summary(model, 0.25) for model in models]})
        IO1.main(["--plan", plan, "--reliance", reliance, v, s])
        return capsys.readouterr().out.splitlines()

    single = run([LUNA])
    multi = run([LUNA, other])
    assert [line for line in multi if LUNA in line] == [line for line in single if LUNA in line]
    assert "Holm p 0.0312" in multi[1] and multi[1].endswith("-> PASS")
    assert f"{LUNA} (relied in rr1): the order is enough" in multi
    assert f"{other} (did not rely in rr1): relies with the reports in the brief, though not in rr1" in multi


def test_io1_keeps_an_incomplete_test_in_the_family(tmp_path, capsys):
    plan = write_json(tmp_path / "io1-plan.json", {"models": [LUNA]})
    reliance = write_json(tmp_path / "reliance.json", {"models": [{"model": LUNA, "decision": "PASS"}]})
    v = write_json(tmp_path / "io1v.json", {"summaries": [io_summary(LUNA, 0.03125)]})
    s = write_json(tmp_path / "io1s.json", {"summaries": []})
    IO1.main(["--plan", plan, "--reliance", reliance, v, s])
    _, first, second, reading = capsys.readouterr().out.splitlines()
    assert "Holm p 0.0625" in first and first.endswith("-> FAIL")
    assert second.endswith("-> INCOMPLETE (32 pairs missing)")
    assert reading.endswith("not decided (a test is incomplete)")
