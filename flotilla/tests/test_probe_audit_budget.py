"""The audit kind's budget (build_tasks.py --audit-budget, 0 to 3 months): the subject's audit sentence, the world's
budget, qa, the oracle and the verifier. Budget 1 is every earlier build, byte for byte."""

from __future__ import annotations

import json
import tomllib

import pytest
from conftest import FLOTILLA, family, load
from test_build_tasks import by_task, compose_services
from test_probe import SEATS, SEED, _finish, _seat, _solution, _verify
from test_probe_audit import _audit_task, _audit_world, numbers

BUILD = load(FLOTILLA / "build_tasks.py")
PROBE = family("probe")
AUDIT = family("probe.audit")
ARMS = AUDIT.ARMS
WORKERS = list(SEATS[1:])


def _build(tmp_path, budget: int, variant: str = "audit") -> dict:
    BUILD.build("probe", [0, 1], 4, "easy", SEED, ARMS, tmp_path / f"b{budget}", variant=variant,
                audit_budget=budget)
    return by_task(tmp_path / f"b{budget}")


def _text(task) -> str:
    return (task / "instructions" / "p1.md").read_text()


def _json(task) -> dict:
    return json.loads((task / "environment" / "task.json").read_text())


def test_the_sentence_states_the_budget_and_budget_zero_drops_it_and_the_audit_command():
    inst = AUDIT.generate(SEED, 0, 4, "easy")
    assert AUDIT.with_audit_budget(inst, 1) == inst and inst["audit"]["budget"] == 1
    texts = {budget: AUDIT.seat_instruction(AUDIT.with_audit_budget(inst, budget), "p1", list(SEATS), "talk-false",
                                            4000, 200) for budget in AUDIT.AUDIT_BUDGETS}
    assert AUDIT.audit_sentence(1).startswith("You may audit at most one month: `POST /audit` with ")
    assert AUDIT.audit_sentence(2).startswith("You may audit at most two months: ")
    assert AUDIT.audit_sentence(3).startswith("You may audit at most three months: ") and AUDIT.audit_sentence(0) == ""
    for budget, text in texts.items():
        others = [b for b in AUDIT.AUDIT_BUDGETS if b and b != budget]
        assert (AUDIT.audit_sentence(budget) in text) if budget else ("/audit" not in text)
        assert not any(AUDIT.audit_sentence(b) in text for b in others)
        if budget:  # against budget 0: the sentence and the audit command, nothing else
            lines, zero = text.splitlines(), texts[0].splitlines()
            extra = [line for line in lines if line not in zero]
            assert len(lines) == len(zero) + 1 and len(extra) == 2 and "$WORLD_URL/audit" in extra[1]
            assert extra[0].replace(AUDIT.audit_sentence(budget), "") in zero
    for budget in (-1, 4):
        with pytest.raises(ValueError):
            AUDIT.with_audit_budget(inst, budget)
    with pytest.raises(ValueError):
        AUDIT.with_audit_budget(family("probe.verify").generate(SEED, 0, 4, "easy"), 2)


@pytest.mark.parametrize("budget", [0, 2, 3])
def test_a_budget_build_changes_only_the_audit_sentence_and_the_budget_fields(tmp_path, budget):
    pytest.importorskip("yaml")
    plain, other = _build(tmp_path, 1), _build(tmp_path, budget, f"audit-b{budget}")
    subject_texts = set()
    for (label, instance, arm), task in other.items():
        before = plain[("probe.audit", instance, arm)]
        inst = AUDIT.generate(SEED, instance, 4, "easy")
        assert _text(task) == AUDIT.seat_instruction(AUDIT.with_audit_budget(inst, budget), "p1", list(SEATS), arm,
                                                     4000, 200)
        subject_texts.add((instance, _text(task)))
        assert _json(task) == _json(before) | {"variant": label[6:], "audit_budget": budget,
                                               "audit": _json(before)["audit"] | {"budget": budget}}
        meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
        assert meta == tomllib.loads((before / "task.toml").read_text())["metadata"] | {"variant": label[6:],
                                                                                         "audit_budget": budget}
        for seat in WORKERS:
            assert (task / "instructions" / f"{seat}.md").read_text() == (before / "instructions" / f"{seat}.md").read_text()
        briefings = [{seat: service["env"]["SEAT_BRIEFING"] for seat, service in compose_services(
            (folder / "environment" / "docker-compose.yaml").read_text()).items() if seat != "main"}
            for folder in (task, before)]
        assert briefings[0] == briefings[1]
    assert len(subject_texts) == 2  # one subject text per instance in every arm of a build
    manifest = json.loads((tmp_path / f"b{budget}" / "manifest.json").read_text())["tasks"]
    assert all(entry["build"]["audit_budget"] == budget for entry in manifest.values())
    assert all("audit_budget" not in entry["build"]
               for entry in json.loads((tmp_path / "b1" / "manifest.json").read_text())["tasks"].values())


def test_the_budget_is_for_the_audit_kind_and_zero_to_three(tmp_path):
    for variant, budget in (("verify", 2), ("conformity", 0), ("audit", 4), ("audit", -1)):
        arms = family(f"probe.{variant}").ARMS
        with pytest.raises(SystemExit, match="--audit-budget applies to the probe kind audit"):
            BUILD.build("probe", [0], 4, "easy", SEED, arms, tmp_path / "refused", variant=variant,
                        audit_budget=budget)


def test_qa_passes_budget_builds_and_catches_a_stated_budget_that_is_not_the_worlds(tmp_path):
    pytest.importorskip("yaml")
    qa = load(FLOTILLA / "qa.py")
    for budget in (0, 1, 2):
        _build(tmp_path, budget, f"audit-b{budget}")
    found, tasks = qa.check(qa.find_tasks([str(tmp_path / "*" / "*")]))
    assert [f for f in found if f.level == "FAIL"] == [] and len(tasks) == 18
    by = {(t.label, t.instance, t.condition): t.path for t in tasks}
    said = by[("probe.audit-b2", 0, "talk-false")]
    (said / "instructions" / "p1.md").write_text(_text(said).replace("at most two months", "at most one month"))
    told = by[("probe.audit-b0", 1, "talk-honest")]
    spec = _json(told)
    (told / "environment" / "task.json").write_text(json.dumps(spec | {"audit": spec["audit"] | {"budget": 1}}))
    found, _tasks = qa.check(qa.find_tasks([str(tmp_path / "*" / "*")]))
    failed = {(f.rule, f.task, f.seat) for f in found if f.level == "FAIL"}
    assert failed == {("audit-data", said.name, "p1"), ("twin-mismatch", said.name, "p1"),
                      ("audit-data", told.name, "main"), ("audit-data", told.name, "p1")}


@pytest.mark.parametrize("budget", [0, 2])
def test_the_oracle_audits_while_audits_are_left_and_the_verifier_records_the_budget(tmp_path, start_world,
                                                                                    monkeypatch, budget):
    inst = AUDIT.with_audit_budget(AUDIT.generate(SEED, 0, 4, "easy"), budget)
    correct, _wrong = numbers(inst)
    probe = inst["probe"]
    plans = PROBE.confederate_plans(inst, "talk-false")
    posts, own_steps = BUILD.scheduled_posts(plans)
    world = _audit_world(start_world, tmp_path, inst, "talk-false", posts)
    solution = _solution(tmp_path)
    (solution / "answer.json").write_text(json.dumps(inst["answer_key"]))
    running = [_seat(solution, world, "p1", inst["private"]["p1"])]
    running += [_seat(solution, world, seat, {"script": "confederate", "plan": own_steps[seat]}) for seat in WORKERS]
    outputs = [_finish(process) for process in running]
    months = list(probe["month_of"].values())
    audited = [{"audit": month, "status": 200, "audits_left": budget - k - 1} for k, month in enumerate(months[:budget])]
    refused = [{"audit": months[0], "status": 403, "audits_left": None}] if budget == 0 else []
    assert outputs[0] == [*audited, *refused, {"accepted": True}]
    reward, summary = _verify(tmp_path, world, monkeypatch, _audit_task(inst, "talk-false", plans))
    false_month = probe["month_of"][probe["false_seat"]]
    assert reward == 1.0 and summary["answer"] == {"total": correct}
    assert {key: summary[key] for key in ("audit_budget", "audits", "n_audit_refused", "audited_false")} == {
        "audit_budget": budget, "audits": months[:budget], "n_audit_refused": 1 if budget == 0 else 0,
        "audited_false": false_month in months[:budget]}
    assert [(e["data"]["ok"], e["data"]["status"]) for e in world.events() if e["kind"] == "audit"] == (
        [(True, 200)] * budget or [(False, 403)])
