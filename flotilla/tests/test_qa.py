"""qa.py: a fresh build has no FAIL finding, and each planted validity fault fires exactly its own rule."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FLOTILLA, load

yaml = pytest.importorskip("yaml")  # qa.py reads Compose files with PyYAML
BUILD = load(FLOTILLA / "build_tasks.py")
QA = load(FLOTILLA / "qa.py")
TALK_CONDITIONS = ("talk", "talk-liar", "talk-warned", "talk-liar-warned")
Findings = set[tuple[str, str, str, str]]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Instance 0 of schedule (2 seats), schedule.liar (scripted peer, all four talk conditions) and ledger."""
    out = tmp_path_factory.mktemp("built")
    BUILD.build("schedule", [0], 2, "easy", 1, BUILD.CONDITIONS, out)
    BUILD.build("schedule", [0], 2, "easy", 1, TALK_CONDITIONS, out, variant="liar", scripted=True)
    BUILD.build("ledger", [0], 2, "easy", 1, BUILD.CONDITIONS, out)
    return out


def findings(out: Path) -> Findings:
    found, _tasks = QA.check(QA.find_tasks([str(out)]))
    return {(f.rule, f.task, f.seat, f.detail) for f in found}


@pytest.fixture(scope="module")
def baseline(built: Path) -> Findings:
    return findings(built)


@pytest.fixture
def copy(built: Path, tmp_path: Path) -> Path:
    out = tmp_path / "set"
    shutil.copytree(built, out)
    return out


def edit_compose(task: Path, change: Callable[[dict[str, Any]], Any]) -> None:
    path = task / "environment" / "docker-compose.yaml"
    compose = yaml.safe_load(path.read_text())
    change(compose)
    path.write_text(yaml.safe_dump(compose, sort_keys=False))


def env(compose: dict[str, Any], service: str) -> dict[str, str]:
    return compose["services"][service]["environment"]


def answer_key(task: Path) -> dict[str, Any]:
    return json.loads((task / "environment" / "task.json").read_text())["answer_key"]


def test_a_fresh_build_has_no_fail(built, baseline):
    found, tasks = QA.check(QA.find_tasks([str(built / "*" / "*")]))
    assert len(tasks) == 10
    assert [f for f in found if f.level == "FAIL"] == []
    assert {f.rule for f in found} <= {"solo-sufficient", "runtime-names"}
    # Harbor's Docker names carry the task name, and the scripted variant's label is `liar` (a WARN per label)
    assert [(f.rule, f.task) for f in found if f.rule == "runtime-names"] == [("runtime-names", "schedule.liar")]
    assert {(f.rule, f.task, f.seat, f.detail) for f in found} == baseline


# --- One planted fault per rule (FINDINGS.md names the real runs where each one happened) ---


def plant_answer_example(task: Path) -> None:
    """The answer format's example is the answer key (FINDINGS.md 04:35)."""
    path = task / "instructions" / "p1.md"
    path.write_text(re.sub(r'`\{"day": [^`]*\}`', f"`{json.dumps(answer_key(task))}`", path.read_text()))


def plant_answer_in_submit_example(task: Path) -> None:
    """The key, wrapped as a submission, in the curl example of the world service section."""
    path = task / "instructions" / "p1.md"
    wrapped = json.dumps({"answer": answer_key(task)})
    path.write_text(path.read_text().replace("""'{"answer": {...}}'""", f"'{wrapped}'"))


def plant_condition_in_env(task: Path) -> None:
    """A seat environment value names the condition (world v1's /whoami did, FINDINGS.md 04:50)."""
    edit_compose(task, lambda c: env(c, "p1").update(RUN_MODE="talk-liar"))


def plant_twin_briefing(task: Path) -> None:
    """One twin's briefing differs from the other's."""
    def change(compose: dict[str, Any]) -> None:
        briefing = json.loads(env(compose, "p1")["SEAT_BRIEFING"])
        env(compose, "p1")["SEAT_BRIEFING"] = json.dumps({**briefing, "note": "checked"}, sort_keys=True)
    edit_compose(task, change)


def plant_shared_network(task: Path) -> None:
    """Two seats on one network (world v1 put every seat on one network, FINDINGS.md 05:10)."""
    edit_compose(task, lambda c: c["services"]["p2"].update(networks=["seat-p1"]))


def plant_default_network(task: Path) -> None:
    """A seat that names no network is on Compose's implicit default network."""
    edit_compose(task, lambda c: c["services"]["p1"].pop("networks"))


def plant_admin_token(task: Path) -> None:
    edit_compose(task, lambda c: env(c, "p1").update(FLOTILLA_ADMIN=env(c, "main")["FLOTILLA_ADMIN"]))


def plant_duplicate_token(task: Path) -> None:
    edit_compose(task, lambda c: env(c, "p2").update(SEAT_TOKEN=env(c, "p1")["SEAT_TOKEN"]))


def plant_stale_world(task: Path) -> None:
    path = task / "environment" / "world.py"
    path.write_text(path.read_text() + "# edited after the build\n")


def plant_missing_instruction(task: Path) -> None:
    (task / "instructions" / "p2.md").unlink()


PLANTED = [
    ("example-leak", "schedule/schedule-000-silent", plant_answer_example),
    ("example-leak", "ledger/ledger-000-silent", plant_answer_in_submit_example),
    ("condition-leak", "schedule/schedule-000-talk", plant_condition_in_env),
    ("twin-mismatch", "schedule.liar/schedule.liar-000-talk-liar", plant_twin_briefing),
    ("seat-network", "schedule/schedule-000-talk", plant_shared_network),
    ("seat-network", "ledger/ledger-000-silent", plant_default_network),
    ("seat-secrets", "schedule/schedule-000-talk", plant_admin_token),
    ("token-map", "schedule/schedule-000-talk", plant_duplicate_token),
    ("stale-code", "ledger/ledger-000-talk", plant_stale_world),
    ("qa-error", "ledger/ledger-000-silent", plant_missing_instruction),
]


@pytest.mark.parametrize(("rule", "task", "plant"), PLANTED, ids=[plant.__name__ for _r, _t, plant in PLANTED])
def test_each_planted_fault_fires_exactly_its_rule(copy, baseline, rule, task, plant):
    plant(copy / task)
    after = findings(copy)
    new = after - baseline
    assert new, f"{rule} did not fire"
    assert {found[0] for found in new} == {rule}, sorted(new)
    assert {found[1] for found in new} == {Path(task).name}
    assert baseline <= after  # and nothing else changed


def test_planted_faults_read_as_expected(copy, baseline):
    plant_condition_in_env(copy / "schedule" / "schedule-000-talk")
    plant_twin_briefing(copy / "schedule.liar" / "schedule.liar-000-talk-liar")
    plant_shared_network(copy / "ledger" / "ledger-000-talk")
    plant_duplicate_token(copy / "ledger" / "ledger-000-silent")
    new = findings(copy) - baseline
    assert ("condition-leak", "schedule-000-talk", "p1", "env.RUN_MODE contains 'talk-liar'") in new
    assert ("twin-mismatch", "schedule.liar-000-talk-liar", "p1",
            "env.SEAT_BRIEFING differs from schedule.liar-000-talk at note") in new
    assert ("seat-network", "ledger-000-talk", "p1", "it shares network seat-p1 with p2") in new
    assert ("seat-network", "ledger-000-talk", "p2", "it shares network seat-p1 with p1") in new
    assert ("token-map", "ledger-000-silent", "p1,p2", "p1 and p2 hold the same token") in new
    assert ("token-map", "ledger-000-silent", "p2", "main's FLOTILLA_TOKENS maps its token to p1") in new


def test_the_decoy_as_an_example_is_a_leak(copy, baseline):
    decoy = json.loads((copy / "schedule.liar" / "schedule.liar-000-talk" / "environment" / "task.json").read_text())[
        "decoy"]
    for condition in ("talk", "talk-liar"):  # both twins, so that they still look the same
        path = copy / "schedule.liar" / f"schedule.liar-000-{condition}" / "instructions" / "p2.md"
        path.write_text(re.sub(r'`\{"day": [^`]*\}`', f"`{json.dumps(decoy)}`", path.read_text()))
    new = findings(copy) - baseline
    assert {(rule, task, seat) for rule, task, seat, _detail in new} == {
        ("example-leak", "schedule.liar-000-talk", "p2"), ("example-leak", "schedule.liar-000-talk-liar", "p2")}
    assert all(detail.endswith("scores 1 against the decoy") for *_rest, detail in new)


def test_a_warning_in_an_unwarned_condition_is_a_condition_leak(copy, baseline):
    for condition in ("talk", "talk-liar"):
        path = copy / "schedule.liar" / f"schedule.liar-000-{condition}" / "instructions" / "p1.md"
        path.write_text(path.read_text().replace("through the world.", f"through the world. {BUILD.WARNING}"))
    path = copy / "schedule.liar" / "schedule.liar-000-talk-warned" / "instructions" / "p2.md"
    path.write_text(path.read_text().replace(BUILD.WARNING, ""))
    new = findings(copy) - baseline
    assert new == {
        ("condition-leak", "schedule.liar-000-talk", "p1",
         "instruction has the warning sentence, but talk is not a warned condition"),
        ("condition-leak", "schedule.liar-000-talk-liar", "p1",
         "instruction has the warning sentence, but talk-liar is not a warned condition"),
        ("condition-leak", "schedule.liar-000-talk-warned", "p2",
         "instruction lacks the warning sentence of the talk-warned condition"),
        ("twin-mismatch", "schedule.liar-000-talk-liar-warned", "p2",
         "instruction differs from schedule.liar-000-talk-warned at line 3"),
    }


def test_a_seat_image_may_not_copy_world_files(copy, baseline):
    task = copy / "ledger" / "ledger-000-talk"
    dockerfile = task / "environment" / "player" / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text() + "COPY task.json world.py /home/player/\n")
    edit_compose(task, lambda c: c["services"]["p1"].update(build={"context": ".", "dockerfile": "player/Dockerfile"}))
    new = {detail for rule, _task, seat, detail in findings(copy) - baseline if (rule, seat) == ("seat-secrets", "p1")}
    assert {"environment/player/Dockerfile copies environment/task.json into the seat image",
            "environment/player/Dockerfile copies environment/world.py into the seat image",
            "image:environment/task.json holds the answer key"} <= new


def test_a_briefing_that_holds_everything_is_solo_sufficient(copy, baseline):
    solo = yaml.safe_load((copy / "schedule" / "schedule-000-solo" / "environment" / "docker-compose.yaml").read_text())
    edit_compose(copy / "schedule" / "schedule-000-silent",
                 lambda c: env(c, "p1").update(SEAT_BRIEFING=env(solo, "p1")["SEAT_BRIEFING"]))
    found, _tasks = QA.check(QA.find_tasks([str(copy)]))
    new = {(f.level, f.rule, f.task, f.seat) for f in found} - {
        (QA.RULES[rule][0], rule, task, seat) for rule, task, seat, _detail in baseline}
    assert new == {("WARN", "solo-sufficient", "schedule-000-silent", "p1")}


def test_command_line(built, copy, tmp_path):
    qa = [sys.executable, str(FLOTILLA / "qa.py")]
    clean = subprocess.run([*qa, str(built / "*" / "*"), "--json"], capture_output=True, text=True, check=False)
    assert clean.returncode == 0, clean.stderr
    report = json.loads(clean.stdout)
    assert (report["tasks"], report["fail"], report["passed"]) == (10, 0, True)
    assert set(report["rules"]) == set(QA.RULES)
    task = copy / "schedule" / "schedule-000-talk"
    for plant in (plant_admin_token, plant_duplicate_token, plant_answer_example):
        plant(task)
    secrets = set(re.findall(r"[0-9a-f]{32}", "".join(
        p.read_text() for p in copy.glob("*/*/environment/docker-compose.yaml"))))
    keys = {json.dumps(answer_key(p)) for p in copy.glob("*/*")}
    for flags in ([], ["--json"]):
        bad = subprocess.run([*qa, str(copy), *flags], capture_output=True, text=True, check=False)
        assert bad.returncode == 1, bad.stderr
        assert {"seat-secrets", "token-map", "example-leak"} <= set(re.findall(r"[a-z]+-[a-z]+", bad.stdout))
        assert not any(secret in bad.stdout for secret in secrets | keys)  # no output names a token or a key
    nothing = subprocess.run([*qa, str(tmp_path / "no-such-folder")], capture_output=True, text=True, check=False)
    assert nothing.returncode == 2 and "no task folder" in nothing.stderr
