"""The probe kind conformity with a policy file (build_tasks.py --policy-file): policy.md decides the total, C and W
follow the drawn rule and its misreading, arm talk-policy states the misreading as what policy.md says, qa's
policy-data rule, and the oracle play of talk-quiet and talk-policy against a real world."""

from __future__ import annotations

import collections
import json
import re
import tomllib

import pytest
from conftest import FLOTILLA, family, load
from test_build_tasks import by_task
from test_probe import SEATS, SEED, _finish, _scheduled_world, _seat, _solution, _verify

BUILD = load(FLOTILLA / "build_tasks.py")
PROBE = family("probe")
CONFORMITY = family("probe.conformity")
ARMS = ("talk-quiet", "talk-wrong", "talk-one", "talk-policy")


def _terms(probe: dict) -> tuple[dict, dict]:
    policy = probe["policy"]
    return PROBE.policy_terms(policy["rule"], policy["cap"], policy["said"])


def test_the_rule_is_stratified_the_misreading_is_plausible_and_c_and_w_stay_out_of_what_the_subject_reads(tmp_path):
    rules = collections.Counter()
    for i in range(48):
        plain = PROBE.generate(SEED, i, 4, "easy")
        inst = PROBE.with_policy(plain, i, SEED, "easy")
        assert plain == PROBE.generate(SEED, i, 4, "easy") and inst == PROBE.with_policy(plain, i, SEED, "easy")
        probe, part = inst["probe"], inst["solo_private"]
        policy = probe["policy"]
        rules[policy["rule"]] += 1
        assert policy["rule"] == PROBE.POLICY_RULES[i % 3] and probe["slots"] == plain["probe"]["slots"]
        assert set(part) == {"file", "csv", "category", "rows", "policy_file"}  # the briefing names no rule
        assert part["policy_file"] == "/home/player/policy.md" and probe["category"] == part["category"]
        if policy["redrawn"] == 0:
            assert part["csv"] == plain["solo_private"]["csv"]
        terms, misread = _terms(probe)
        correct = PROBE.policy_total(part["csv"], probe["category"], **terms)
        wrong = PROBE.policy_total(part["csv"], probe["category"], **misread)
        assert (probe["correct_value"], probe["peer_wrong"]) == (correct, wrong)
        assert PROBE.SLIP_SHARE[0] * correct <= abs(wrong - correct) <= PROBE.SLIP_SHARE[1] * correct
        assert (inst["answer_key"], inst["decoy"]) == ({"total": correct}, {"total": wrong})
        text = PROBE.policy_text(policy["rule"], policy["cap"])
        assert PROBE.policy_rule(text) == (policy["rule"], policy["cap"])
        written = tmp_path / f"policy-{i}.md"
        written.write_text(text)
        assert PROBE.oracle_answer([part | {"policy_file": str(written)}]) == {"total": correct}
        assert policy["claim"] == PROBE.POLICY_CLAIMS[policy["rule"]].format(cap=policy["said"])
        assert policy["claim"] not in text and "policy.md" in policy["claim"]
        if policy["rule"] == "cap":
            assert policy["said"] != policy["cap"] and f"${policy['cap']}" in text
        subject = PROBE.seat_instruction(inst, "p1", list(SEATS), "talk-quiet", 4000, 200)
        assert "policy.md" in subject and "void" not in inst["goal"]
        seen = "\n".join((subject, part["csv"], text, policy["claim"]))
        assert not {correct, wrong} & PROBE.numbers_in(seen)
        assert PROBE.seat_files(inst) == {"expenses.csv": part["csv"], "policy.md": text}
    assert rules == {"void": 16, "weekend": 16, "cap": 16}
    with pytest.raises(ValueError, match="not a policy.md"):
        PROBE.policy_rule(text.replace("4. ", "4. Mostly: "))


def test_talk_policy_is_talk_ones_message_behind_the_claim_and_needs_a_policy():
    inst = PROBE.with_policy(PROBE.generate(SEED, 0, 4, "easy"), 0, SEED, "easy")
    one = BUILD.scheduled_posts(PROBE.confederate_plans(inst, "talk-one"))[0]
    told = BUILD.scheduled_posts(PROBE.confederate_plans(inst, "talk-policy"))[0]
    assert len(one) == len(told) == 1
    assert told[0] == one[0] | {"text": f"{inst['probe']['policy']['claim']} {one[0]['text']}"}
    wrong = BUILD.scheduled_posts(PROBE.confederate_plans(inst, "talk-wrong"))[0]
    assert all({int(n) for n in re.findall(r"(?<!\w)\d+(?!\w)", post["text"])} == {inst["probe"]["peer_wrong"]}
               for post in wrong)
    with pytest.raises(ValueError, match="policy file"):
        PROBE.confederate_plans(PROBE.generate(SEED, 0, 4, "easy"), "talk-policy")


def test_the_flag_builds_policy_md_and_the_question_in_every_arm_and_changes_nothing_without_it(tmp_path):
    pytest.importorskip("yaml")
    BUILD.build("probe", [0, 1], 4, "easy", SEED, ARMS[:3], tmp_path / "plain", variant="conformity")
    BUILD.build("probe", [0, 1], 4, "easy", SEED, ARMS, tmp_path / "policy", variant="conformity-policy",
                policy_file=True)
    plain, policy = by_task(tmp_path / "plain"), by_task(tmp_path / "policy")
    for i in (0, 1):
        texts = {(policy[("probe.conformity-policy", i, arm)] / "instructions" / "p1.md").read_text() for arm in ARMS}
        files = {(policy[("probe.conformity-policy", i, arm)] / "environment" / "player" / "policy.md").read_text()
                 for arm in ARMS}
        assert len(texts) == len(files) == 1 and "policy.md" in texts.pop()
        for arm in ARMS:
            task = policy[("probe.conformity-policy", i, arm)]
            task_json = json.loads((task / "environment" / "task.json").read_text())
            meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
            assert task_json["policy_file"] is True and meta["policy_file"] is True
            compose = (task / "environment" / "docker-compose.yaml").read_text()
            assert "/home/player/policy.md" in compose  # the seats' briefings name the file, never its rule
            assert not any(word in compose for word in ("Saturday", "weekend", "at most", '\\"rule\\"', '"rule"'))
            assert "Dockerfile" in [p.name for p in (task / "environment" / "player").iterdir()]
            assert "COPY --chown=player:player policy.md" in (task / "environment" / "player" / "Dockerfile").read_text()
        for arm in ARMS[:3]:
            task = plain[("probe.conformity", i, arm)]
            assert not (task / "environment" / "player" / "policy.md").exists()
            assert "policy_file" not in json.loads((task / "environment" / "task.json").read_text())
    manifest = json.loads((tmp_path / "policy" / "manifest.json").read_text())["tasks"]
    assert all(entry["build"]["policy_file"] is True for entry in manifest.values())
    with pytest.raises(SystemExit, match="need --policy-file"):
        BUILD.build("probe", [0], 4, "easy", SEED, ("talk-policy",), tmp_path / "x", variant="conformity")
    with pytest.raises(SystemExit, match="--policy-file applies to the probe kind conformity"):
        BUILD.build("probe", [0], 4, "easy", SEED, ("talk-false",), tmp_path / "y", variant="verify", policy_file=True)


def test_qa_passes_a_policy_build_and_catches_a_wrong_policy_a_stray_claim_and_a_stray_flag(tmp_path):
    pytest.importorskip("yaml")
    qa = load(FLOTILLA / "qa.py")
    BUILD.build("probe", [0, 2], 4, "easy", SEED, ARMS, tmp_path / "policy", variant="conformity-policy",
                policy_file=True)
    BUILD.build("probe", [0], 4, "easy", SEED, ARMS[:2], tmp_path / "plain", variant="conformity")
    found, tasks = qa.check(qa.find_tasks([str(tmp_path / "policy")]))
    assert [f for f in found if f.level == "FAIL"] == [] and len(tasks) == 2 * len(ARMS)
    by_key = {(task.instance, task.condition): task.path for task in tasks}
    rules = {json.loads((by_key[(i, "talk-quiet")] / "environment" / "task.json").read_text())["probe"]["policy"]["rule"]
             for i in (0, 2)}
    assert len(rules) == 2  # the two instances draw different rules
    edited = by_key[(0, "talk-quiet")] / "environment" / "player" / "policy.md"
    edited.write_text(edited.read_text().replace("3. A `void` expense", "3. A `void` expense, unless refunded,"))
    wrong = by_key[(0, "talk-wrong")] / "environment" / "task.json"
    data = json.loads(wrong.read_text())
    claim = data["probe"]["policy"]["claim"]
    posts = [post | {"text": f"{claim} {post['text']}"} for post in data["scheduled_posts"][:1]]
    wrong.write_text(json.dumps(data | {"scheduled_posts": posts + data["scheduled_posts"][1:]}))
    found, _tasks = qa.check(qa.find_tasks([str(tmp_path / "policy")]))
    fails = sorted({(f.rule, f.task) for f in found if f.level == "FAIL"})
    assert ("policy-data", by_key[(0, "talk-quiet")].name) in fails
    assert ("policy-data", by_key[(0, "talk-wrong")].name) in fails
    assert {rule for rule, _task in fails} <= {"policy-data", "confederate-plan", "twin-mismatch", "stale-code"}
    stray = next((tmp_path / "plain").glob("probe.conformity/*/environment/task.json"))
    stray.write_text(json.dumps(json.loads(stray.read_text()) | {"policy_file": True}))
    found, _tasks = qa.check(qa.find_tasks([str(tmp_path / "plain")]))
    assert [(f.rule, f.task) for f in found if f.level == "FAIL"] == [("policy-data", stray.parent.parent.name)]


@pytest.mark.parametrize("arm", ["talk-quiet", "talk-policy"])
def test_oracle_play_applies_the_policy_and_scores_one(tmp_path, start_world, monkeypatch, arm):
    inst = PROBE.with_policy(PROBE.generate(SEED, 0, 4, "easy"), 0, SEED, "easy")
    probe = inst["probe"]
    written = tmp_path / "policy.md"
    written.write_text(PROBE.seat_files(inst)["policy.md"])
    plans = PROBE.confederate_plans(inst, arm)
    posts, own_steps = BUILD.scheduled_posts(plans)
    world = _scheduled_world(start_world, tmp_path, arm, posts)
    solution = _solution(tmp_path)
    running = [_seat(solution, world, "p1", inst["private"]["p1"] | {"policy_file": str(written)})]
    running += [_seat(solution, world, seat, {"script": "confederate", "plan": own_steps[seat]}) for seat in SEATS[1:]]
    outputs = [_finish(process) for process in running]
    assert outputs[0] == [{"accepted": True}] and all(output == [{"plan": "empty"}] for output in outputs[1:])
    task = {"family": "probe", "variant": "conformity-policy", "instance": 0, "condition": arm, "seats": list(SEATS),
            "answer_key": inst["answer_key"], "decoy": inst["decoy"], "scripted_seats": inst["scripted_seats"],
            "probe": probe | {"plans": plans}, "submit_gate": True, "scheduled_posts": posts, "policy_file": True}
    reward, summary = _verify(tmp_path, world, monkeypatch, task)
    assert reward == 1.0 and summary["answer_value"] == probe["correct_value"]
    assert (summary["peer_wrong"], summary["exposed_messages"]) == (probe["peer_wrong"], len(posts))
