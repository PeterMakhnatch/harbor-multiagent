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


SEED = 6_151_806_725_431_072  # of the size that build_tasks.py draws (a secret seed), fixed for these tests


def build_set(out: Path, readable_names: bool) -> Path:
    """Instance 0 of schedule (2 seats), schedule.liar (scripted peer, all four talk conditions) and ledger."""
    BUILD.build("schedule", [0], 2, "easy", SEED, BUILD.CONDITIONS, out, readable_names=readable_names)
    BUILD.build("schedule", [0], 2, "easy", SEED, TALK_CONDITIONS, out, variant="liar", scripted=True,
                readable_names=readable_names)
    BUILD.build("ledger", [0], 2, "easy", SEED, BUILD.CONDITIONS, out, readable_names=readable_names)
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The set with readable names, so that each planted fault below can name its task folder."""
    return build_set(tmp_path_factory.mktemp("built"), readable_names=True)


@pytest.fixture(scope="module")
def opaque(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same set with opaque names, as build_tasks.py writes it by default."""
    return build_set(tmp_path_factory.mktemp("opaque"), readable_names=False)


def opaque_task(out: Path, label: str, condition: str) -> Path:
    """The folder of instance 0 of a label in a condition, found in the build manifest."""
    entries = json.loads((out / "manifest.json").read_text())["tasks"].values()
    (path,) = [e["path"] for e in entries if (e["label"], e["instance"], e["condition"]) == (label, 0, condition)]
    return out / path


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


def test_a_readable_build_fails_runtime_names_unless_allowed(built, baseline):
    found, tasks = QA.check(QA.find_tasks([str(built / "*" / "*")]))
    assert len(tasks) == 10
    assert {f.rule for f in found if f.level == "FAIL"} == {"runtime-names"}  # its only fault
    assert {f.rule for f in found} <= {"solo-sufficient", "runtime-names"}
    # Harbor's Docker names carry a readable folder name, so each label fails; schedule.liar's say 'liar'
    names = {f.task: f.detail for f in found if f.rule == "runtime-names"}
    assert sorted(names) == ["ledger", "schedule", "schedule.liar"]
    assert "reads 'liar', 'schedule', 'talk', 'warned'" in names["schedule.liar"]
    assert "reads 'schedule', 'silent', 'solo', 'talk'" in names["schedule"]
    assert {(f.rule, f.task, f.seat, f.detail) for f in found} == baseline
    allowed, _tasks = QA.check(QA.find_tasks([str(built / "*" / "*")]), allow_readable_names=True)
    assert [f for f in allowed if f.level == "FAIL"] == []  # --allow-readable-names: a local debugging run
    assert {(f.rule, f.task, f.seat, f.detail) for f in allowed} == baseline
    assert {f.level for f in allowed if f.rule == "runtime-names"} == {"WARN"}
    assert QA.summary(allowed, tasks)["rules"]["runtime-names"]["level"] == "WARN"
    assert QA.summary(found, tasks)["rules"]["runtime-names"]["level"] == "FAIL"


def test_an_opaque_build_passes_runtime_names(opaque):
    found, tasks = QA.check(QA.find_tasks([str(opaque / "*" / "*")]))
    assert len(tasks) == 10 and {task.names for task in tasks} == {"opaque"}
    assert {f.rule for f in found} <= {"solo-sufficient"}  # no FAIL, and no runtime-names WARN
    assert {(task.label, task.instance, task.condition) for task in tasks} == {
        *(("schedule", 0, c) for c in BUILD.CONDITIONS), *(("ledger", 0, c) for c in BUILD.CONDITIONS),
        *(("schedule.liar", 0, c) for c in TALK_CONDITIONS)}


def rename_task(task: Path, name: str) -> None:
    path = task / "task.toml"
    path.write_text(re.sub(r'^name = ".*"$', f'name = "{name}"', path.read_text(), count=1, flags=re.MULTILINE))


@pytest.mark.parametrize(("name", "detail"), [
    ("flotilla/schedule.liar-000-talk-liar",
     "its task name flotilla/schedule.liar-000-talk-liar says 'liar', 'schedule', 'talk'"),
    ("flotilla/t-0123456789ab-talk", "its task name flotilla/t-0123456789ab-talk says 'talk'"),
    ("flotilla/t-0123456789", "its task name flotilla/t-0123456789 is not of the form flotilla/t-<12 hex>"),
    ("other/t-0123456789ab", "its task name other/t-0123456789ab is not of the form flotilla/t-<12 hex>"),
])
def test_an_opaque_name_that_says_what_the_task_is_fails(opaque, tmp_path, name, detail):
    baseline = findings(opaque)
    out = tmp_path / "set"
    shutil.copytree(opaque, out)
    task = opaque_task(out, "schedule.liar", "talk-liar")
    rename_task(task, name)
    assert findings(out) - baseline == {("opaque-names", task.name, "*", detail)}  # not runtime-names


def test_twins_of_an_opaque_build_are_found_by_their_metadata(opaque, tmp_path):
    baseline = findings(opaque)
    out = tmp_path / "set"
    shutil.copytree(opaque, out)
    twin, task = opaque_task(out, "schedule.liar", "talk"), opaque_task(out, "schedule.liar", "talk-liar")
    plant_twin_briefing(task)
    assert findings(out) - baseline == {
        ("twin-mismatch", task.name, "p1", f"env.SEAT_BRIEFING differs from {twin.name} (schedule.liar-000-talk) at note")}


def test_short_variant_words_do_not_fail_an_opaque_name(tmp_path):
    """`t` is in every opaque name and `ab` may be: only the hex part is checked, and hex-only words are left out."""
    for variant in ("t", "ab"):
        BUILD.build("ledger", [0], 2, "easy", 1, ("talk",), tmp_path, variant=variant)
    found, tasks = QA.check(QA.find_tasks([str(tmp_path)]))
    assert len(tasks) == 2 and not [f for f in found if f.rule in ("opaque-names", "runtime-names")]


def test_an_opaque_folder_that_says_what_the_task_is_fails(opaque, tmp_path):
    out = tmp_path / "set"
    shutil.copytree(opaque, out)
    task = opaque_task(out, "ledger", "silent")
    task.rename(task.with_name("ledger-000-silent"))
    found, _tasks = QA.check(QA.find_tasks([str(out)]))
    new = {(f.rule, f.task, f.detail) for f in found if f.task == "ledger-000-silent"}
    assert new == {("opaque-names", "ledger-000-silent", "its folder name ledger-000-silent says 'ledger', 'silent'")}
    assert not [f for f in found if f.rule == "runtime-names"]  # an opaque task's name is opaque-names' FAIL


@pytest.mark.parametrize(("source", "label"), [("../task.toml", "task.toml"),
                                               ("../../../manifest.json", "the build manifest (manifest.json)"),
                                               ("../../..", "the build manifest (manifest.json)")])
def test_a_seat_may_not_mount_task_toml_or_the_manifest(opaque, tmp_path, source, label):
    """Both say the condition of an opaque task: a seat must not read them (CONTRACT.md 2)."""
    baseline = findings(opaque)
    out = tmp_path / "set"
    shutil.copytree(opaque, out)
    task = opaque_task(out, "schedule", "talk")
    edit_compose(task, lambda c: c["services"]["p1"].update(volumes=[f"{source}:/home/player/x:ro"]))
    new = findings(out) - baseline
    assert {(rule, seat) for rule, _task, seat, _detail in new} == {("seat-secrets", "p1")}
    assert any(detail.endswith(f"which holds {label}") or f", {label}" in detail for *_rest, detail in new), new


def test_the_report_says_what_an_opaque_task_is(opaque, tmp_path):
    out = tmp_path / "set"
    shutil.copytree(opaque, out)
    task = opaque_task(out, "schedule", "talk")
    plant_admin_token(task)
    qa = [sys.executable, str(FLOTILLA / "qa.py"), str(out)]
    text = subprocess.run(qa, capture_output=True, text=True, check=False)
    assert text.returncode == 1 and f"{task.name} (schedule-000-talk)" in text.stdout
    rows = json.loads(subprocess.run([*qa, "--json"], capture_output=True, text=True, check=False).stdout)["findings"]
    assert {(r["rule"], r["label"], r["instance"], r["condition"]) for r in rows if r["task"] == task.name} == {
        ("seat-secrets", "schedule", 0, "talk")}


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


def plant_software_name_in_env(task: Path) -> None:
    """A seat's variable names the software: world v2's Server header did, and seats searched for the name."""
    edit_compose(task, lambda c: env(c, "p1").update(FLOTILLA_RUN="1"))


def plant_software_name_in_instruction(task: Path) -> None:
    path = task / "instructions" / "p1.md"
    path.write_text(path.read_text() + "\nThe world runs harbor-multiagent.\n")


def plant_seed(task: Path) -> None:
    """A seat's variable holds the build's secret seed: with the public generator it gives every answer key."""
    edit_compose(task, lambda c: env(c, "p2").update(RUN_ID=f"run-{SEED}"))


def plant_escaped_software_name(task: Path) -> None:
    """The name, written with a JSON escape in a seat's briefing: the seat's JSON reader turns it into "flotilla"."""
    def change(compose: dict[str, Any]) -> None:
        briefing = json.dumps(json.loads(env(compose, "p1")["SEAT_BRIEFING"]), sort_keys=True)
        env(compose, "p1")["SEAT_BRIEFING"] = briefing[:-1] + ', "zz": "\\u0066lotilla"}'
    edit_compose(task, change)


def plant_container_name(task: Path) -> None:
    """A fixed name for main's container: a seat reads it by a reverse DNS lookup of the world's address."""
    edit_compose(task, lambda c: c["services"]["main"].update(container_name="world-main"))


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
    ("software-name", "schedule/schedule-000-talk", plant_software_name_in_env),
    ("software-name", "ledger/ledger-000-silent", plant_software_name_in_instruction),
    ("seat-secrets", "ledger/ledger-000-talk", plant_seed),
    ("software-name", "schedule/schedule-000-silent", plant_escaped_software_name),
    ("container-name", "ledger/ledger-000-talk", plant_container_name),
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
    readable = subprocess.run([*qa, str(built / "*" / "*"), "--json"], capture_output=True, text=True, check=False)
    assert readable.returncode == 1, readable.stderr  # readable names: runtime-names fails
    assert {row["rule"] for row in json.loads(readable.stdout)["findings"] if row["level"] == "FAIL"} == {
        "runtime-names"}
    clean = subprocess.run([*qa, str(built / "*" / "*"), "--json", "--allow-readable-names"],
                           capture_output=True, text=True, check=False)
    assert clean.returncode == 0, clean.stderr
    report = json.loads(clean.stdout)
    assert (report["tasks"], report["fail"], report["passed"]) == (10, 0, True)
    assert set(report["rules"]) == set(QA.RULES)
    text = subprocess.run([*qa, str(built), "--allow-readable-names"], capture_output=True, text=True, check=False)
    assert text.returncode == 0 and "runtime-names is a WARN, for a local debugging run only" in text.stdout
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
        assert not any(secret in bad.stdout for secret in secrets | keys | {str(SEED)})  # nor a seed
    nothing = subprocess.run([*qa, str(tmp_path / "no-such-folder")], capture_output=True, text=True, check=False)
    assert nothing.returncode == 2 and "no task folder" in nothing.stderr


def test_planted_names_and_seeds_read_as_expected(copy, baseline):
    plant_software_name_in_env(copy / "schedule" / "schedule-000-talk")
    plant_software_name_in_instruction(copy / "ledger" / "ledger-000-silent")
    plant_seed(copy / "ledger" / "ledger-000-talk")
    plant_escaped_software_name(copy / "schedule" / "schedule-000-silent")
    plant_container_name(copy / "ledger" / "ledger-000-solo")
    raw = yaml.safe_load((copy / "schedule" / "schedule-000-silent" / "environment" / "docker-compose.yaml")
                         .read_text())["services"]["p1"]["environment"]["SEAT_BRIEFING"]
    assert "flotilla" not in raw and "\\u0066lotilla" in raw  # only the decoded JSON says it
    assert findings(copy) - baseline == {
        ("software-name", "schedule-000-talk", "p1", "env.FLOTILLA_RUN contains 'flotilla'"),
        ("software-name", "ledger-000-silent", "p1", "instruction contains 'harbor-multiagent'"),
        ("seat-secrets", "ledger-000-talk", "p2", "env.RUN_ID holds the build's seed"),
        ("software-name", "schedule-000-silent", "p1", "env.SEAT_BRIEFING contains 'flotilla'"),
        ("container-name", "ledger-000-solo", "main", "service main sets container_name"),
    }


def test_a_public_seed_fails_once_per_label_unless_allowed(tmp_path, monkeypatch):
    """The generators are public: a seed below 1,000,000 or one that the documentation names gives the answers.
    --allow-public-seed makes it a WARN, to repeat a published build (the 2026-11-19 waves used --seed 1)."""
    documented = 7_340_982_618_211
    for variant, seed in (("", 1), ("hard", 999_999), ("big", 1_000_000), ("doc", documented), ("own", SEED)):
        BUILD.build("ledger", [0, 1], 2, "easy", seed, ("talk",), tmp_path, variant=variant)
    monkeypatch.setattr(QA, "documented_seeds", lambda: frozenset({1, documented}))
    found, _tasks = QA.check(QA.find_tasks([str(tmp_path)]))
    tail = "a seat that finds the public generator can compute their answer keys; build a new wave without --seed"
    expected = {
        ("ledger", "*", f"2 tasks use seed 1, which is public (below 1,000,000): {tail}"),
        ("ledger.hard", "*", f"2 tasks use seed 999999, which is public (below 1,000,000): {tail}"),
        ("ledger.doc", "*",
         f"2 tasks use seed {documented}, which is public (named in this checkout's documentation): {tail}"),
    }
    assert {(f.level, f.task, f.seat, f.detail) for f in found if f.rule == "public-seed"} == {
        ("FAIL", *row) for row in expected}
    assert {f.rule for f in found if f.level == "FAIL"} == {"public-seed"}
    allowed, tasks = QA.check(QA.find_tasks([str(tmp_path)]), allow_public_seed=True)
    assert {(f.level, f.task, f.seat, f.detail) for f in allowed if f.rule == "public-seed"} == {
        ("WARN", *row) for row in expected}
    assert not [f for f in allowed if f.level == "FAIL"]
    assert "--allow-public-seed: public-seed is a WARN, only to repeat a published build" in QA.render_text(
        allowed, tasks)


def test_the_documentation_names_the_canonical_seed():
    """RESULTS.md and README.md rebuild the canonical waves with --seed 1, so seed 1 is documented, and public."""
    assert 1 in QA.documented_seeds() and SEED not in QA.documented_seeds()
