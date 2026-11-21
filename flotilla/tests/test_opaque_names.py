"""Opaque task names (CONTRACT.md 2): a task's name and folder say nothing about it, the build manifest says it all."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from conftest import FAMILIES, FLOTILLA, load
from test_build_tasks import by_task, compose_services, seat_visible_texts

BUILD = load(FLOTILLA / "build_tasks.py")
TALK_CONDITIONS = ("talk", "talk-liar", "talk-warned", "talk-liar-warned")
# every word that would tell a seat what its task is: condition words, families, and the variants built below
WORDS = ("solo", "silent", "talk", "liar", "warned", *FAMILIES, "hard", "3seat")


def build_all(out: Path, **options) -> list[Path]:
    """Every family in the three conditions, a variant, and the scripted liar label in all four talk conditions."""
    built = []
    for family_name in FAMILIES:
        built += BUILD.build(family_name, [0, 1], 2, "easy", 1, BUILD.CONDITIONS, out, **options)
    built += BUILD.build("ledger", [0], 3, "hard", 1, BUILD.CONDITIONS, out, variant="3seat", **options)
    built += BUILD.build("schedule", [0, 1], 2, "easy", 1, TALK_CONDITIONS, out, variant="liar", scripted=True,
                         **options)
    return built


def manifest(out: Path) -> dict[str, dict]:
    data = json.loads((out / "manifest.json").read_text())
    assert (data["format"], data["version"]) == ("flotilla-manifest", 1)
    return data["tasks"]


def test_opaque_names_say_nothing_about_the_task(tmp_path):
    built = build_all(tmp_path)
    assert len(built) == 3 * 6 + 3 + 8
    names = [task.name for task in built]
    assert len(set(names)) == len(names)
    for task in built:
        name = tomllib.loads((task / "task.toml").read_text())["task"]["name"]
        assert re.fullmatch(r"t-[0-9a-f]{12}", task.name) and name == f"flotilla/{task.name}"
        # Harbor names the trial after the first 32 characters of the folder name, then the Compose project,
        # containers and networks after the trial: none of them can carry a word
        trial = f"{task.name[:32].rstrip('_-')}__AbCdEfG"
        for runtime in (name, trial, f"{trial.lower()}__env-main-1", f"{trial.lower()}__env_seat-p1"):
            assert not [word for word in WORDS if word in runtime.lower()], runtime
        services = compose_services((task / "environment" / "docker-compose.yaml").read_text())
        for seat in (s for s in services if s != "main"):  # no seat reads its own task's name
            assert not any(task.name in text for text in seat_visible_texts(task, seat, services))


def test_names_differ_between_builds_and_need_the_salt(tmp_path):
    first = {task.name for task in build_all(tmp_path / "a")}
    second = {task.name for task in build_all(tmp_path / "b")}
    assert not first & second  # a new salt per build
    salt = bytes(16)
    assert BUILD.opaque_name(salt, "schedule", 0, "talk") == BUILD.opaque_name(salt, "schedule", 0, "talk")
    assert BUILD.opaque_name(salt, "schedule", 0, "talk") != BUILD.opaque_name(bytes(15) + b"\1", "schedule", 0, "talk")
    assert len({BUILD.opaque_name(salt, "schedule", 0, c) for c in BUILD.CONDITION_TEXT}) == len(BUILD.CONDITION_TEXT)


def test_the_manifest_maps_every_task_and_lies_outside_every_task_folder(tmp_path):
    built = build_all(tmp_path)
    entries = manifest(tmp_path)
    assert sorted(entries) == sorted(f"flotilla/{task.name}" for task in built)
    for name, entry in entries.items():
        task = tmp_path / entry["path"]
        meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
        task_json = json.loads((task / "environment" / "task.json").read_text())
        assert task.name == name.removeprefix("flotilla/") and task.parent.name == entry["label"]
        assert entry["label"] == (f"{meta['family']}.{meta['variant']}" if meta["variant"] else meta["family"])
        assert (entry["family"], entry["variant"], entry["instance"], entry["condition"]) == (
            meta["family"], meta["variant"], meta["instance"], meta["condition"])
        assert (task_json["family"], task_json["variant"], task_json["instance"], task_json["condition"]) == (
            meta["family"], meta["variant"], meta["instance"], meta["condition"])
        assert entry["seats"] == sorted(tomllib.loads((task / "task.toml").read_text())["agents"]) == task_json["seats"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry["built"])
        # what the analysis compares when a trial's task folder was rebuilt or moved (flotilla_analysis/tasks.py)
        unset = {"scripted_seats": [], "submitter": "", "announce": False}  # [metadata] leaves these out
        assert entry["build"] == {key: meta.get(key, unset.get(key)) for key in (
            "seed", "difficulty", "n_seats", "team_seats", "max_text", "max_messages", *unset)}
        assert entry["instructions_sha256"] == {seat: hashlib.sha256((task / "instructions" / f"{seat}.md")
                                                                     .read_bytes()).hexdigest() for seat in entry["seats"]}
    assert [p.name for p in tmp_path.rglob("manifest.json")] == ["manifest.json"]  # one, at the build root
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger", "ledger.3seat", "manifest.json", "puzzle",
                                                         "schedule", "schedule.liar"]


def test_readable_names_keep_the_old_layout(tmp_path):
    built = BUILD.build("schedule", [0], 2, "easy", 1, BUILD.CONDITIONS, tmp_path, readable_names=True)
    assert [task.name for task in built] == ["schedule-000-solo", "schedule-000-silent", "schedule-000-talk"]
    for task in built:
        config = tomllib.loads((task / "task.toml").read_text())
        assert config["task"]["name"] == f"flotilla/{task.name}" and config["metadata"]["names"] == "readable"
    assert sorted(manifest(tmp_path)) == [f"flotilla/{task.name}" for task in sorted(built)]


def test_a_rebuild_replaces_the_earlier_folders_of_each_task_and_keeps_their_names(tmp_path):
    readable = BUILD.build("schedule", [0, 1], 2, "easy", 1, BUILD.CONDITIONS, tmp_path, readable_names=True)
    first = BUILD.build("schedule", [0, 1], 2, "easy", 1, BUILD.CONDITIONS, tmp_path)
    assert not any(task.exists() for task in readable)  # readable folders of an earlier build are replaced too
    second = BUILD.build("schedule", [1], 2, "easy", 1, ("talk",), tmp_path)
    folders = by_task(tmp_path)  # it asserts one folder per task
    assert len(folders) == 6 and folders[("schedule", 1, "talk")] == second[0]
    assert sorted(p.name for p in (tmp_path / "schedule").iterdir()) == sorted(
        {*(task.name for task in first if task.exists()), second[0].name})
    assert len([task for task in first if not task.exists()]) == 1
    entries = manifest(tmp_path)  # every name ever built here keeps its meaning, so old jobs can still be read
    assert sorted(entries) == sorted(f"flotilla/{task.name}" for task in [*readable, *first, *second])
    assert sum(1 for e in entries.values() if (e["instance"], e["condition"]) == (1, "talk")) == 3


def test_builds_of_other_labels_share_the_manifest(tmp_path):
    BUILD.build("schedule", [0], 2, "easy", 1, ("talk",), tmp_path)
    BUILD.build("ledger", [0], 2, "easy", 1, ("talk",), tmp_path, variant="hard")
    assert sorted(entry["label"] for entry in manifest(tmp_path).values()) == ["ledger.hard", "schedule"]
    (tmp_path / "manifest.json").write_text("not json")
    try:
        BUILD.build("schedule", [0], 2, "easy", 1, ("talk",), tmp_path)
    except SystemExit as exc:
        assert "is not a Flotilla manifest" in str(exc)
    else:
        raise AssertionError("a damaged manifest must stop the build, not be replaced")


def test_make_jobs_groups_opaque_tasks_by_seat_roster(tmp_path):
    out = tmp_path / "tasks"
    BUILD.build("schedule", [0, 1], 2, "easy", 1, BUILD.CONDITIONS, out)
    BUILD.build("schedule", [0], 2, "easy", 1, ("talk", "talk-liar"), out, variant="liar", scripted=True)
    result = subprocess.run(
        [sys.executable, str(FLOTILLA / "make_jobs.py"), "--tasks", str(out / "*" / "*"), "--agent", "oracle",
         "--name", "check", "--env", "docker", "--out", str(tmp_path / "configs"), "--jobs-dir", str(tmp_path / "jobs")],
        capture_output=True, text=True, check=True)
    assert len(result.stdout.splitlines()) == 3
    entries = {(out / entry["path"]).resolve(): entry for entry in manifest(out).values()}
    rosters = {}
    for config_path in sorted((tmp_path / "configs").glob("*.json")):
        config = json.loads(config_path.read_text())
        seats = [participant["seat"] for participant in config["participants"]]
        tasks = [entries[Path(task["path"])] for task in config["tasks"]]
        assert all(task["seats"] == seats for task in tasks)
        rosters[config["job_name"]] = sorted((task["label"], task["instance"], task["condition"]) for task in tasks)
    assert rosters == {
        "check-1seat": [("schedule", 0, "solo"), ("schedule", 1, "solo")],
        "check-2seat": [("schedule", i, c) for i in (0, 1) for c in ("silent", "talk")],
        "check-3seat": [("schedule.liar", 0, "talk"), ("schedule.liar", 0, "talk-liar")],
    }


def test_a_name_that_two_tasks_would_share_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(BUILD, "opaque_name", lambda *_args: "t-000000000000")  # a hash collision, forced
    try:
        BUILD.build("schedule", [0], 2, "easy", 1, ("solo", "talk"), tmp_path)
    except SystemExit as exc:
        assert "names another task" in str(exc)
    else:
        raise AssertionError("two tasks must never share a folder")


def test_make_jobs_refuses_readable_names_unless_allowed(tmp_path):
    """Harbor names a trial's Docker containers and networks after the task folder: a readable name says the
    condition to a seat that looks up an address (FINDINGS.md 2026-11-19 06:20)."""
    out = tmp_path / "tasks"
    BUILD.build("schedule", [0], 2, "easy", 1, BUILD.CONDITIONS, out, readable_names=True)
    run = [sys.executable, str(FLOTILLA / "make_jobs.py"), "--tasks", str(out / "*" / "*"), "--agent", "oracle",
           "--name", "check", "--env", "docker", "--out", str(tmp_path / "configs"),
           "--jobs-dir", str(tmp_path / "jobs")]
    refused = subprocess.run(run, capture_output=True, text=True, check=False)
    assert refused.returncode == 2 and "refused: readable names in 3 of 3 tasks" in refused.stderr
    assert not (tmp_path / "configs").exists()
    allowed = subprocess.run([*run, "--allow-readable-names"], capture_output=True, text=True, check=True)
    assert len(allowed.stdout.splitlines()) == 2  # the 1-seat job and the 2-seat job
    opaque = tmp_path / "opaque"
    (task,) = BUILD.build("schedule", [0], 2, "easy", 1, ("talk",), opaque)
    toml = task / "task.toml"
    toml.write_text(toml.read_text().replace(f'name = "flotilla/{task.name}"', 'name = "flotilla/schedule-000-talk"'))
    renamed = subprocess.run([*run[:3], str(opaque / "*" / "*"), *run[4:]], capture_output=True, text=True, check=False)
    assert renamed.returncode == 2 and "readable names in 1 of 1 tasks" in renamed.stderr  # the task name counts too
    assert load(FLOTILLA / "make_jobs.py").OPAQUE_NAME.pattern == BUILD.OPAQUE_NAME.pattern
