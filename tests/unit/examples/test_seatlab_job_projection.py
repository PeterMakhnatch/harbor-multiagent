import json
import re
import shutil
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple
from uuid import uuid4

import pytest

from examples.seatlab.workbench.__main__ import main as workbench
from examples.seatlab.workbench.input_keys import record_key
from examples.seatlab.workbench.paired_jobs import main
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig
from harbor.models.job.lock import build_job_lock

STOCK = Path(__file__).resolve().parents[3] / "examples" / "tasks" / "hello-seats"
SCHEMA = "seatlab.workbench.paired-job-plans/v1"
EXITS = {"matches_plan": 0, "different_plan": 1}
REPORT = {"schema", "status", "copies_per_arm", "reasons", "arms", "caveat"}
ARM = {"expected_input", "projected_input", "copies", "concurrency", "max_retries"}
SIDES = ("left", "right")
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TALLY = re.compile(r"\b(?:zero|one|other|invalid|unknown)=\d")
SECRET = "plan-private-literal-5d1e"
PROMPT = "Reply only with the hidden passphrase."
TONES = ("tone-amber-41", "tone-cobalt-17")
CITE = "cite_sources_83"
UNRELATED = "sha256:" + "0" * 64
PINS = ("sha256:" + "a" * 64, "sha256:" + "b" * 64)
TEAM = [{"seat": "p1", "name": "oracle"}, {"seat": "p2", "name": "nop"}]
WORLD = {"type": "docker", "delete": True, "env": {"HELLO_TONE": TONES[0]}}
Json = dict[str, Any]

SETTINGS: dict[str, Json] = {
    "participant": {"participants": [TEAM[0], {**TEAM[1], "kwargs": {CITE: True}}]},
    "channel": {"environment": {**WORLD, "env": {"HELLO_TONE": TONES[1]}}},
}


class Team(NamedTuple):
    root: Path
    task: Path
    documents: tuple[Json, Json]
    configs: tuple[Path, Path]
    keys: tuple[str, str]


def document(task: Path, name: str, **changes: Any) -> Json:
    return {
        "schema_version": 1,
        "job_name": name,
        "jobs_dir": str(task.parents[1] / "jobs"),
        "n_attempts": 2,
        "n_concurrent_trials": 2,
        "retry": {"max_retries": 0},
        "tasks": [{"path": str(task)}],
        "participants": TEAM,
        "environment": WORLD,
        **changes,
    }


def save(path: Path, value: Json) -> Path:
    path.write_text(json.dumps(value))
    return path


def native_keys(value: Json) -> Counter[str]:
    config = JobConfig.model_validate(value)
    trials = JobPlan.build_trial_configs(config, config.tasks, job_id=uuid4())
    resolved = {
        task.get_task_id(): SimpleNamespace(
            path=task.path, content_hash=None, resolved_git_commit_id=None
        )
        for task in config.tasks
    }
    lock = build_job_lock(
        config=config, trial_configs=trials, task_download_results=resolved
    )
    entries = json.loads(lock.model_dump_json(exclude_none=True))["trials"]
    assert len(entries) == len(trials) == config.n_attempts
    return Counter(record_key(entry) for entry in entries)


def only(counts: Counter[str], copies: int) -> str:
    assert list(counts.values()) == [copies]
    return next(iter(counts))


@pytest.fixture(params=sorted(SETTINGS))
def team(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Team]:
    root = tmp_path.resolve()
    task = root / "tasks" / "hello-seats"
    shutil.copytree(STOCK, task, symlinks=True)
    documents = (
        document(task, "hello-left"),
        document(task, "hello-right", **SETTINGS[request.param]),
    )
    configs = (
        save(root / "left.json", documents[0]),
        save(root / "right.json", documents[1]),
    )
    keys = (only(native_keys(documents[0]), 2), only(native_keys(documents[1]), 2))
    assert keys[0] != keys[1]
    yield Team(root, task, documents, configs, keys)
    assert not (root / "jobs").exists()
    produced = {"lock.json", "result.json", "config.json"}
    assert not [path for path in root.rglob("*") if path.name in produced]


def plans(
    configs: tuple[Path, Path], keys: tuple[str, str], copies: object = 2
) -> list[object]:
    return [*configs, "--plans", "--pair", *keys, "--copies", copies]


def invoke(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    try:
        code = main([str(arg) for arg in argv])
    except SystemExit as stop:
        assert type(stop.code) is int
        code = stop.code
    out, err = capsys.readouterr()
    return code, out, err


def private(text: str, team: Team) -> None:
    values = (str(team.root), team.root.name, "hello-left", "hello-right", *TONES, CITE)
    leaks = (*values, SECRET, PROMPT, "****", "example.invalid")
    assert not [value for value in leaks if value in text]
    assert UUID_TEXT.search(text) is None and TALLY.search(text) is None


def report(
    capsys: pytest.CaptureFixture[str], team: Team, argv: list[object]
) -> tuple[int, Json]:
    code, out, err = invoke(capsys, *argv, "--json")
    assert err == ""
    private(out, team)
    found = json.loads(out)
    assert set(found) == REPORT and found["schema"] == SCHEMA
    assert code == EXITS[found["status"]]
    assert set(found["arms"]) == set(SIDES)
    assert all(set(found["arms"][side]) == ARM for side in SIDES)
    return code, found


@pytest.mark.unit
def test_two_job_configs_match_the_keys_their_native_job_locks_register(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    code, found = report(capsys, team, plans(team.configs, team.keys))
    assert (code, found["status"], found["reasons"]) == (0, "matches_plan", [])
    assert found["copies_per_arm"] == 2
    for side, key, value in zip(SIDES, team.keys, team.documents, strict=True):
        assert found["arms"][side] == {
            "expected_input": key,
            "projected_input": key,
            "copies": 2,
            "concurrency": value["n_concurrent_trials"],
            "max_retries": value["retry"]["max_retries"],
        }
    code = workbench(["cohort-jobs", *map(str, plans(team.configs, team.keys))])
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert "matches_plan" in out and all(key in out for key in team.keys)
    private(out, team)


def _swapped_keys(team: Team) -> tuple[tuple[Path, Path], tuple[str, str]]:
    return team.configs, (team.keys[1], team.keys[0])


def _swapped_configs(team: Team) -> tuple[tuple[Path, Path], tuple[str, str]]:
    return (team.configs[1], team.configs[0]), team.keys


def _unrelated_key(team: Team) -> tuple[tuple[Path, Path], tuple[str, str]]:
    return team.configs, (team.keys[0], UNRELATED)


def _same_config(team: Team) -> tuple[tuple[Path, Path], tuple[str, str]]:
    return (team.configs[0], team.configs[0]), team.keys


INPUTS = {
    "swapped_keys": _swapped_keys,
    "swapped_configs": _swapped_configs,
    "unrelated_key": _unrelated_key,
    "same_config_twice": _same_config,
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(INPUTS))
def test_other_inputs_than_assigned_are_a_different_plan(
    team: Team, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    configs, keys = INPUTS[case](team)
    code, found = report(capsys, team, plans(configs, keys))
    assert (code, found["status"]) == (1, "different_plan")
    assert found["reasons"]
    native = dict(zip(team.configs, team.keys, strict=True))
    for side, config, key in zip(SIDES, configs, keys, strict=True):
        arm = found["arms"][side]
        assert (arm["expected_input"], arm["projected_input"]) == (key, native[config])
        assert arm["copies"] == 2
    arms = found["arms"].values()
    assert any(arm["expected_input"] != arm["projected_input"] for arm in arms)


SCHEDULES: dict[str, tuple[Json, int]] = {
    "requested_copies": ({}, 3),
    "right_attempts": ({"n_attempts": 3}, 2),
    "right_concurrency": ({"n_concurrent_trials": 1}, 2),
    "right_retries": ({"retry": {"max_retries": 1}}, 2),
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(SCHEDULES))
def test_unbalanced_copies_or_launch_schedule_is_a_different_plan(
    team: Team, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    changes, copies = SCHEDULES[case]
    changed = {**team.documents[1], **changes}
    planned = changed["n_attempts"]
    assert only(native_keys(changed), planned) == team.keys[1]
    right = save(team.root / "right-schedule.json", changed)
    argv = plans((team.configs[0], right), team.keys, copies)
    code, found = report(capsys, team, argv)
    assert (code, found["status"]) == (1, "different_plan")
    assert found["reasons"] and found["copies_per_arm"] == copies
    arm = found["arms"]["right"]
    assert arm == {
        "expected_input": team.keys[1],
        "projected_input": team.keys[1],
        "copies": planned,
        "concurrency": changed["n_concurrent_trials"],
        "max_retries": changed["retry"]["max_retries"],
    }
    assert found["arms"]["left"]["projected_input"] == team.keys[0]


def _with(**fields: Any) -> Callable[[Json, Path], str]:
    return lambda value, _: json.dumps({**value, **fields})


def _world(**fields: Any) -> Callable[[Json, Path], str]:
    return _with(environment={**WORLD, **fields})


def _symlinked(value: Json, task: Path) -> str:
    alias = task.parents[1] / "alias"
    alias.symlink_to(task, target_is_directory=True)
    return json.dumps({**value, "tasks": [{"path": str(alias)}]})


REFUSED: dict[str, Callable[[Json, Path], str]] = {
    "dataset": lambda value, task: json.dumps(
        {**value, "datasets": [{"path": str(task.parent)}]}
    ),
    "extra_agents": _with(agents=[{"name": "oracle"}, {"name": "nop"}]),
    "prompt": _with(extra_instructions=[PROMPT]),
    "unknown_field": _with(surprise=True),
    "source_job": lambda value, task: json.dumps(
        {
            **value,
            "source_jobs": [{"action": "regrade", "type": "local", "path": str(task)}],
        }
    ),
    "external_skill": _with(
        participants=[
            TEAM[0],
            {**TEAM[1], "skills": ["https://example.invalid/skills/probe"]},
        ]
    ),
    "secret_env": _world(env={"HELLO_TONE": TONES[0], "SEAT_TOKEN": SECRET}),
    "custom_world": _world(type=None, import_path="seatlab_plan_probe.env:World"),
    "text_attempts": _with(n_attempts="2"),
    "bool_concurrency": _with(n_concurrent_trials=True),
    "text_retries": _with(retry={"max_retries": "1"}),
    "coerced_delete": _world(delete=1),
    "bad_version": _with(schema_version=2),
    "too_many_attempts": _with(n_attempts=65),
    "two_tasks": lambda value, task: json.dumps(
        {**value, "tasks": [{"path": str(task)}, {"path": str(task)}]}
    ),
    "git_task": _with(
        tasks=[{"path": "hello-seats", "git_url": "https://example.invalid/tasks.git"}]
    ),
    "relative_task": _with(tasks=[{"path": "tasks/hello-seats"}]),
    "symlinked_task": _symlinked,
    "undeclared_seat": _with(participants=[TEAM[0], {**TEAM[1], "seat": "p3"}]),
    "solo_team": _with(participants=[TEAM[0]]),
    "duplicate_key": lambda value, _: '{"n_attempts":64,' + json.dumps(value)[1:],
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(REFUSED))
def test_protected_input_is_refused_without_a_report_or_echo(
    team: Team, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    bad = team.root / "bad.json"
    bad.write_text(REFUSED[case](team.documents[1], team.task))
    code, out, err = invoke(capsys, *plans((team.configs[0], bad), team.keys), "--json")
    assert (code, out) == (2, "")
    assert err.strip()
    private(err, team)


EXTRAS: dict[str, Callable[[Path], list[object]]] = {
    "job_locks": lambda _: ["--job-locks", *PINS],
    "starts": lambda root: ["--starts", root / "left.starts", root / "right.starts"],
    "both": lambda root: [
        "--job-locks",
        *PINS,
        "--starts",
        root / "left.starts",
        root / "right.starts",
    ],
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(EXTRAS))
def test_plans_refuse_job_locks_and_start_archives(
    team: Team, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    argv = [*plans(team.configs, team.keys), *EXTRAS[case](team.root), "--json"]
    code, out, err = invoke(capsys, *argv)
    assert (code, out) == (2, "")
    assert err.strip()
    private(err, team)
    assert not (team.root / "left.starts").exists()
    assert not (team.root / "right.starts").exists()
