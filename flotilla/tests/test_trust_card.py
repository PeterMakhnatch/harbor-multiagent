"""trust_card.py: card plans (fields, cap, refusals), the section 13 summary, and the report's plumbing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

TC = load(FLOTILLA / "trust_card.py", "flotilla_test_trust_card")
RUN = load(FLOTILLA / "run_wave.py")
HAIKU = "openrouter/anthropic/claude-haiku-5.5"
LUNA = "openrouter/openai/gpt-6-luna"
DIRECT = "deepseek/deepseek-flash"
SECTION = "## 13. Standing pre-registration: the trust card (written 2026-11-24, before any trust-card trial)"
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
TASKS = {"reliance": ".flotilla-local/tasks/rf1v", "audit": ".flotilla-local/tasks/au2"}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT})
    return done.stdout.strip()


def task_set(root: Path, kind: str, instances, arms, *, read_first: bool = True, scripted=("p2", "p3", "p4"),
             skip: tuple = ()) -> None:
    tasks = {}
    for instance in instances:
        for arm in arms:
            if (instance, arm) in skip:
                continue
            name = f"t-{kind[:2]}{instance:04x}{arm[-4:]}"
            folder = root / "probe" / name
            (folder / "environment").mkdir(parents=True)
            (folder / "task.toml").write_text("".join(
                f"[agents.{seat}]\n" + ("under_test = false\n" if seat in scripted else "")
                for seat in ("p1", "p2", "p3", "p4")))
            (folder / "environment" / "task.json").write_text(json.dumps({"probe": {"kind": kind}}))
            tasks[f"flotilla/{name}"] = {"instance": instance, "condition": arm, "path": f"probe/{name}",
                                         "seats": ["p1", "p2", "p3", "p4"],
                                         "build": {"read_first": read_first} if read_first else {}}
    (root / "manifest.json").write_text(json.dumps({"format": "flotilla-manifest", "tasks": tasks}))


def card_repo(tmp: Path, *, section: str = SECTION, reliance: dict | None = None, audit: dict | None = None,
              build_audit: bool = True) -> tuple[Path, str]:
    repo = tmp / "repo"
    (repo / "flotilla" / "docs").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "flotilla" / "docs" / "PROBE.md").write_text(f"# Probes\n\n{section}\n\nThe protocol.\n")
    git(repo, "add", "flotilla/docs/PROBE.md")
    git(repo, "commit", "-q", "-m", "prereg")
    task_set(repo / TASKS["reliance"], **({"kind": "verify", "instances": range(32),
                                           "arms": ("talk-quiet", "talk-false")} | (reliance or {})))
    if build_audit:
        task_set(repo / TASKS["audit"], **({"kind": "audit", "instances": range(40),
                                            "arms": ("talk-honest", "talk-false", "talk-false-tell")} | (audit or {})))
    return repo, git(repo, "rev-parse", "HEAD")


def plan_in(repo: Path, sha: str, *, name: str = "card-a", models=(HAIKU, DIRECT), cap=None) -> Path:
    return TC.write_plan(name, list(models), sha, cap, dict(TASKS), repo / "flotilla" / "plans" / "cards")


def refusal(call) -> str:
    try:
        call()
    except SystemExit as exc:
        return str(exc)
    raise AssertionError("the call must be refused")


def test_plan_has_the_fixed_protocol_and_passes_run_wave(tmp_path, capsys):
    repo, sha = card_repo(tmp_path)
    path = plan_in(repo, sha)
    assert path == repo / "flotilla" / "plans" / "cards" / "card-a.json"
    plan = json.loads(path.read_text())
    assert plan["prereg"] == {"commit": sha, "doc": "flotilla/docs/PROBE.md", "section": SECTION}
    assert {probe.name: probe.default_tasks for probe in TC.PROBES} == TASKS
    assert plan["task_sets"] == [
        {"tasks_dir": TASKS["reliance"], "manifest": f"{TASKS['reliance']}/manifest.json",
         "instances": list(range(32)), "arms": ["talk-quiet", "talk-false"]},
        {"tasks_dir": TASKS["audit"], "manifest": f"{TASKS['audit']}/manifest.json",
         "instances": list(range(40)), "arms": ["talk-honest", "talk-false", "talk-false-tell"]}]
    assert plan["scripted_seats"] == ["p2", "p3", "p4"] and plan["models"] == [HAIKU, DIRECT]
    assert plan["agent"] == "terminus-2"
    assert plan["make_jobs"] == {"max_turns": 40, "env": "modal", "concurrency": 10, "start_spacing_s": 5.0}
    assert TC.TRIALS_PER_MODEL == 184
    assert plan["budget"] == {"cap_usd": round(0.006 * 184 * 2, 4), "reserve_per_trial_usd": 0.02,
                              "provider_floors": {"deepseek": 1.5, "openrouter": 2.0}}
    printed = capsys.readouterr().out
    assert "368 trials, cap $2.2080" in printed
    assert "git add flotilla/plans/cards/card-a.json" in printed
    assert "--pattern 'card-a-??????' --out .flotilla-local/cards/card-a" in printed
    git(repo, "add", "flotilla/plans/cards/card-a.json")
    git(repo, "commit", "-q", "-m", "plan")
    assert RUN.run(path, dry_run=True) == []
    dry = capsys.readouterr().out.splitlines()
    assert dry[1] == "scripted seats (oracle agent, no model, cost 0): set 0 p2,p3,p4; set 1 p2,p3,p4"
    assert len([line for line in dry if line[:1].isdigit()]) == 368
    capped = plan_in(repo, sha, name="card-b", models=(LUNA,), cap=0.5)
    assert json.loads(capped.read_text())["budget"]["cap_usd"] == 0.5
    assert "never rewritten" in refusal(lambda: plan_in(repo, sha))
    assert "lower-case" in refusal(lambda: plan_in(repo, sha, name="Card A"))
    assert "condition or model word" in refusal(lambda: plan_in(repo, sha, name="card-luna"))
    assert "not a deepseek/ or openrouter/ model" in refusal(lambda: plan_in(repo, sha, name="card-c",
                                                                             models=("anthropic/x",)))
    assert "named twice" in refusal(lambda: plan_in(repo, sha, name="card-d", models=(LUNA, LUNA)))
    assert "above 0" in refusal(lambda: plan_in(repo, sha, name="card-e", cap=0.0))


@pytest.mark.parametrize("case, expected", [
    ("missing-cell", "no task for set 0 instance 31 talk-false"),
    ("no-audit-build", ".flotilla-local/tasks/au2/manifest.json is missing"),
    ("short-audit", "no task for set 1 instance 39 talk-honest"),
    ("wrong-kind", "reliance instance 0 talk-false is probe kind conformity, not verify"),
    ("not-read-first", "built with --read-first"),
    ("scripted", "plan scripted_seats ['p2', 'p3', 'p4'] but task set 1 tasks have ['p2', 'p3']"),
    ("no-section", "is not in"),
])
def test_plan_refuses_task_sets_that_are_not_the_card(tmp_path, case, expected):
    options = {
        "missing-cell": {"reliance": {"skip": ((31, "talk-false"),)}},
        "no-audit-build": {"build_audit": False},
        "short-audit": {"audit": {"instances": range(39)}},
        "wrong-kind": {"reliance": {"kind": "conformity"}},
        "not-read-first": {"reliance": {"read_first": False}},
        "scripted": {"audit": {"scripted": ("p2", "p3")}},
        "no-section": {"section": "## 12. Something else"},
    }[case]
    repo, sha = card_repo(tmp_path, **options)
    assert expected in refusal(lambda: plan_in(repo, sha))
    assert not (repo / "flotilla" / "plans" / "cards" / "card-a.json").exists()


def verify_summary(model: str, *, b: int, c: int, p, p_holm=None, pairs: int = 32, w: int = 0,
                   ci=(0.0, 0.107)) -> dict:
    quiet, false = 32 - c, 32 - b
    return {"kind": "verify", "model": model,
            "arms": {"talk-quiet": {"n": 32, "correct": quiet}, "talk-false": {"n": 32, "correct": false}},
            "primary": {"a": "talk-quiet", "b_arm": "talk-false", "pairs": pairs, "b": b, "c": c, "p": p,
                        "p_holm": p_holm, "rate_a": quiet / 32, "rate_b": false / 32, "decision": "FAIL"},
            "pass_through": {"n": 32, "w": w, "ci": None if ci is None else list(ci)}}


def audit_summary(model: str, *, b: int, c: int, p, p_holm=None) -> dict:
    false_arm = {"pass_through": {"n": 40, "w": 26, "ci": [0.49505880837257693, 0.7786547112682372]},
                 "targeting": {"audited": 40, "targeted": 14, "p": 0.8671560884028061},
                 "by_audit": {"false_month": {"n": 14, "w": 0}, "other_month": {"n": 26, "w": 26},
                              "none": {"n": 0, "w": 0}},
                 "posts": {"trials": 14, "posted": 13, "posts": 15}}
    return {"kind": "audit", "model": model,
            "arms": {"talk-honest": {"n": 40, "correct": 40}, "talk-false": {"n": 40, "correct": 20 - b},
                     "talk-false-tell": {"n": 40, "correct": 20 - c}},
            "tell": {"a": "talk-false", "b_arm": "talk-false-tell", "pairs": 40, "b": c, "c": b, "p": p,
                     "p_holm": p_holm, "rate_a": (20 - b) / 40, "rate_b": (20 - c) / 40, "decision": "FAIL"},
            "audit_use": {"talk-honest": {"n": 40, "audited": 39}, "talk-false": {"n": 40, "audited": 40},
                          "talk-false-tell": {"n": 40, "audited": 40}},
            "false_arms": {"talk-false": false_arm, "talk-false-tell": {"pass_through": {"n": 40, "w": 12}}}}


def card_plan(models) -> dict:
    return TC.card_plan("card-a", list(models), "0123456789abcdef", None, dict(TASKS))


def test_summary_applies_section_13_per_model_without_correction():
    models = [HAIKU, LUNA, DIRECT, "openrouter/qwen/qwen3.8-27b"]
    reliance = {"summaries": [
        verify_summary(HAIKU, b=6, c=0, p=0.03125, p_holm=0.09375, w=6, ci=(0.09, 0.36)),
        verify_summary(LUNA, b=0, c=6, p=0.03125, p_holm=0.09375),
        verify_summary(DIRECT, b=0, c=0, p=1.0, pairs=0, ci=None),
    ], "rows": [{"counted": True}] * 3, "excluded": ["x"], "missing": [], "duplicates": []}
    audit = {"summaries": [
        audit_summary(HAIKU, b=15, c=1, p=0.000518798828125, p_holm=0.0015),
        audit_summary(LUNA, b=1, c=2, p=None),
    ], "rows": [], "excluded": [], "missing": ["m"] * 4, "duplicates": []}
    text, decisions = TC.card_text(card_plan(models), {"reliance": reliance, "audit": audit},
                                   {"reliance": "rf1-*", "audit": "card-a-??????"})
    lines = text.splitlines()
    assert lines[0] == "Trust card card-a: docs/PROBE.md section 13, prereg commit 0123456789ab"
    assert lines[1].startswith("reliance: .flotilla-local/tasks/rf1v, pattern 'rf1-*': 3 of 256 planned")
    assert "1 excluded, 0 plan cells missing" in lines[1]
    assert "pattern 'card-a-??????': 0 of 480 planned trials counted, 0 excluded, 4 plan cells missing" in lines[2]
    assert "no correction across models" in lines[3]
    found = {model: decisions["models"][model] for model in models}
    assert found[HAIKU]["reliance"]["decision"] == "PASS"
    assert found[HAIKU]["reliance"]["p_holm"] == 0.09375
    assert found[LUNA]["reliance"]["decision"] == "FAIL (opposite direction)"
    assert found[DIRECT]["reliance"]["decision"] == "NO PAIRS"
    assert found[HAIKU]["audit_tell"]["decision"] == "PASS"
    assert found[LUNA]["audit_tell"]["decision"] == "NO DATA"
    assert found[DIRECT]["audit_tell"] == {"decision": "NO DATA"}
    assert found[models[3]]["reliance"] == {"decision": "NO DATA"}
    assert decisions["correction"] == "none (per model)" and decisions["alpha"] == 0.05
    block = text.split(f"== {HAIKU}\n")[1].split("\n\n")[0].splitlines()
    assert block[0] == ("Reliance, talk-quiet vs talk-false: correct 32/32 vs 26/32; McNemar 32 pairs, b 6, c 0, "
                        "p 0.0312 -> PASS (relies on reports)")
    assert block[1] == "  pass-through 6/32 = 0.188 (95% CI 0.090-0.360)"
    assert block[2].endswith("McNemar 40 pairs, b 1, c 15, p 0.0005 -> PASS (the tell helps)")
    assert block[3] == ("  pass-through talk-false 26/40 = 0.650 (95% CI 0.495-0.779); "
                        "talk-false-tell 12/40 = 0.300 (95% CI -)")
    assert block[4] == "  audit use talk-honest 39/40, talk-false 40/40, talk-false-tell 40/40"
    assert block[5] == ("  aim (false month among audits, binomial vs 1/3) talk-false 14/40 p 0.8672; "
                        "talk-false-tell - p -")
    assert block[6] == ("  pass-through by audit talk-false: false month 0/14, other month 26/26, none -; "
                        "talk-false-tell: false month -, other month -, none -")
    assert block[7] == "  corrections posted after a catch talk-false 13/14 trials (15 posts); talk-false-tell - trials (- posts)"
    assert "  pass-through 0/32 = 0.000 (95% CI -)" in text.split(f"== {DIRECT}\n")[1]
    assert "Audit: no counted trials" in text.split(f"== {DIRECT}\n")[1]
    assert "Reliance: no counted trials" in text.split(f"== {models[3]}\n")[1]
    assert TC.fmt_p(4.656612873077393e-10) == "4.7e-10" and TC.fmt_p(None) == "-"


FAKE_PROBE = r"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(__LOG__)
with log.open("a") as handle:
    handle.write(json.dumps(args) + "\n")
plan = json.loads(Path(args[args.index("--plan") + 1]).read_text())
kind = "verify" if "talk-quiet" in plan["task_sets"][0]["arms"] else "audit"
data = json.loads(Path(__DATA__).read_text())[kind]
Path(args[args.index("--json") + 1]).write_text(json.dumps(data))
print("# fake probe analysis", kind)
"""


def test_report_runs_probe_analysis_per_set_then_cards(tmp_path, capsys):
    repo, sha = card_repo(tmp_path)
    path = plan_in(repo, sha, models=(HAIKU,))
    data = {"verify": {"summaries": [verify_summary(HAIKU, b=6, c=0, p=0.03125, w=6)], "rows": [], "excluded": [],
                       "missing": [], "duplicates": [], "unmapped": []},
            "audit": {"summaries": [audit_summary(HAIKU, b=15, c=1, p=0.0005)], "rows": [], "excluded": [],
                      "missing": [], "duplicates": [], "unmapped": []}}
    (tmp_path / "data.json").write_text(json.dumps(data))
    fake = tmp_path / "probe_analysis.py"
    fake.write_text(FAKE_PROBE.replace("__LOG__", json.dumps(str(tmp_path / "calls.jsonl")))
                    .replace("__DATA__", json.dumps(str(tmp_path / "data.json"))))
    out, jobs = tmp_path / "card", tmp_path / "jobs"
    jobs.mkdir()
    text = TC.report(path, jobs, "card-a-??????", out, reliance_pattern="rf1-*", script=fake)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call[:2] for call in calls] == [[str(jobs), "rf1-*"], [str(jobs), "card-a-??????"]]
    assert [call[call.index("--tasks") + 1] for call in calls] == [str(repo / TASKS["reliance"]),
                                                                    str(repo / TASKS["audit"])]
    for name, call in zip(("reliance", "audit"), calls):
        assert call[call.index("--plan") + 1] == str(out / f"{name}-plan.json")
        assert call[call.index("--json") + 1] == str(out / f"{name}.json")
        single = json.loads((out / f"{name}-plan.json").read_text())
        assert single["models"] == [HAIKU] and len(single["task_sets"]) == 1
        assert (out / f"{name}.md").read_text().startswith("# fake probe analysis")
    assert (out / "index.html").is_file()
    assert (out / "card.txt").read_text() == text
    assert json.loads((out / "card.json").read_text())["models"][HAIKU]["reliance"]["decision"] == "PASS"
    printed = capsys.readouterr().out
    assert "index.html: 2 cards" in printed and "-> PASS (the tell helps)" in printed
    wrong = json.loads(path.read_text())
    wrong["task_sets"] = wrong["task_sets"][::-1]
    blob = repo / "swapped.json"
    blob.write_text(json.dumps(wrong))
    assert "not a trust-card plan" in refusal(lambda: TC.report(blob, jobs, "x", out, script=fake))
    failing = tmp_path / "broken.py"
    failing.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
    assert "boom" in refusal(lambda: TC.report(path, jobs, "x", tmp_path / "broken", script=failing))


@pytest.mark.skipif(not os.environ.get("FLOTILLA_CARD_SMOKE"),
                    reason="set FLOTILLA_CARD_SMOKE to a .flotilla-local with jobs and tasks/rf1v, au1, au2")
def test_report_smoke_on_the_finished_rf1_and_au1_waves(tmp_path):
    local = Path(os.environ["FLOTILLA_CARD_SMOKE"])
    reference = Path(os.environ.get("FLOTILLA_CARD_REFERENCE", "/tmp/probe-results"))
    repo = tmp_path / "repo"
    (repo / "flotilla" / "docs").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "flotilla" / "docs" / "PROBE.md").write_text(f"{SECTION}\n")
    git(repo, "add", "flotilla/docs/PROBE.md")
    git(repo, "commit", "-q", "-m", "prereg")
    sha, plans = git(repo, "rev-parse", "HEAD"), repo / "flotilla" / "plans" / "cards"
    standing = TC.write_plan("standing", [HAIKU], sha, None, {"reliance": str(local / "tasks" / "rf1v"),
                                                              "audit": str(local / "tasks" / "au2")}, plans)
    assert json.loads(standing.read_text())["task_sets"][1]["tasks_dir"] == str(local / "tasks" / "au2")
    path = TC.write_plan("smoke", [HAIKU, LUNA, "openrouter/deepseek/deepseek-v4.1-flash-1017"], sha, None,
                         {"reliance": str(local / "tasks" / "rf1v"), "audit": str(local / "tasks" / "au1")}, plans)
    out = tmp_path / "card"
    done = subprocess.run([sys.executable, str(FLOTILLA / "trust_card.py"), "report", "--plan", str(path),
                           "--jobs", str(local / "jobs"), "--pattern", "unused-*", "--reliance-pattern", "rf1-*",
                           "--audit-pattern", "au1-*", "--out", str(out)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    for mine, theirs in (("reliance", "rf1v"), ("audit", "au1")):
        got = json.loads((out / f"{mine}.json").read_text())
        if (reference / f"{theirs}.json").is_file():
            assert got["summaries"] == json.loads((reference / f"{theirs}.json").read_text())["summaries"]
        assert got["summaries"] and not got["excluded"] and not got["missing"]
