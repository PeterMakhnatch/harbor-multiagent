import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examples.seatlab.workbench import isolate
from harbor.models.job.lock import build_trial_lock
from harbor.models.trial.config import TrialConfig

STOCK = Path(__file__).resolve().parents[3] / "examples" / "tasks" / "hello-seats"
LABEL = "seatlab_preflight_label"
PROBE = "seatlab_preflight_probe"
SECRET = "preflight-private-literal-7f3c"
BASIS = "prospective_local_configs"
CANONICAL = {
    "name": "oracle",
    "skip_model_parsing": False,
    "skills": [],
    "resume_trajectory": False,
    "extra_allowed_hosts": [],
    "kwargs": {},
    "mcp_servers": [],
    "seat": "p1",
}
MINIMAL = {"seat": "p2", "import_path": f"{LABEL}.agent:Support"}


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    left, right = (tmp_path.resolve() / side / "hello-seats" for side in ("l", "r"))
    for task in (left, right):
        shutil.copytree(STOCK, task, symlinks=True)
    return left, right


def config(
    task: Path, name: str, p1: dict[str, Any], p2: dict[str, Any], tone: str
) -> dict[str, Any]:
    return {
        "task": {"path": str(task)},
        "trial_name": name,
        "trials_dir": str(task.parents[1] / "trials"),
        "environment": {"type": "docker", "delete": True, "env": {"HELLO_TONE": tone}},
        "participants": [p1, p2],
    }


def write(task: Path, name: str, document: dict[str, Any]) -> Path:
    path = task.parent / name
    path.write_text(json.dumps(document))
    return path


def saved(
    roots: tuple[Path, Path], documents: tuple[dict[str, Any], ...]
) -> list[Path]:
    pairs = zip(roots, documents, strict=True)
    return [write(task, "config.json", document) for task, document in pairs]


def native_lock(task: Path, document: dict[str, Any]) -> Path:
    download = SimpleNamespace(
        path=task, content_hash=None, resolved_git_commit_id=None
    )
    lock = build_trial_lock(
        trial_config=TrialConfig.model_validate(document),
        task_download_result=download,
    )
    return write(task, "lock.json", lock.model_dump(mode="json", exclude_none=True))


def run(capsys, *argv: object) -> tuple[int, str, str]:
    code = isolate.main([str(arg) for arg in argv])
    output = capsys.readouterr()
    return code, output.out, output.err


def checked(capsys, *argv: object) -> tuple[int, dict[str, Any]]:
    code, out, err = run(capsys, *argv, "--json")
    assert err == ""
    return code, json.loads(out)


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def untouched(left: Path, right: Path) -> None:
    for task in (left, right):
        assert not (task.parents[1] / "trials").exists()
    assert LABEL not in sys.modules and PROBE not in sys.modules


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["participant", "role_instruction"])
def test_unrun_configs_compare_like_their_native_locks(
    roots: tuple[Path, Path], capsys, mode: str
) -> None:
    left, right = roots
    support: dict[str, Any] = MINIMAL
    if mode == "participant":
        support = {**MINIMAL, "kwargs": {"cite": True}}
    else:
        append(right / "instructions" / "p2.md", "Answer in one line.\n")
    documents = (
        config(left, "hello-left", CANONICAL, MINIMAL, "plain"),
        config(right, "hello-right", CANONICAL, support, "plain"),
    )
    configs = saved(roots, documents)
    locks = [native_lock(t, d) for t, d in zip(roots, documents, strict=True)]
    target = ("--" + mode.replace("_", "-"), "p2")
    code, locked = checked(capsys, left, locks[0], right, locks[1], *target)
    assert code == 0 and locked["status"] == "isolated"
    assert "input_basis" not in locked
    code, planned = checked(
        capsys, "--configs", left, configs[0], right, configs[1], *target
    )
    assert code == 0 and planned.pop("input_basis") == BASIS
    planned.pop("input_note", None)
    assert planned == locked
    assert planned["target"]["changed"] and planned["other_changes"] == {}
    assert planned["native_task_digest_changed"] is (mode == "role_instruction")
    pin = ("--expected-comparison", locked["comparison"])
    code, pinned = checked(
        capsys, "--configs", left, configs[0], right, configs[1], *target, *pin
    )
    assert code == 0 and pinned["matches_expected"] is True
    assert pinned["comparison"] == locked["comparison"]
    assert pinned["trial_lock_records"] == locked["trial_lock_records"]
    code, text, err = run(
        capsys, "--configs", left, configs[0], right, configs[1], *target
    )
    assert code == 0 and err == "" and locked["comparison"] in text
    assert "prospective" in text and str(left.parent) not in text
    code, text, _ = run(capsys, left, locks[0], right, locks[1], *target)
    assert code == 0 and "prospective" not in text
    untouched(left, right)


@pytest.mark.unit
def test_receiver_prompt_with_other_changed_inputs_is_not_isolated(
    roots: tuple[Path, Path], capsys
) -> None:
    left, right = roots
    append(right / "instructions" / "p2.md", "Answer in one line.\n")
    outgoing = {**CANONICAL, "kwargs": {"hurry": True}}
    documents = (
        config(left, "hello-left", CANONICAL, MINIMAL, "plain"),
        config(right, "hello-right", outgoing, MINIMAL, "loud"),
    )
    configs = saved(roots, documents)
    code, result = checked(
        capsys,
        "--configs",
        left,
        configs[0],
        right,
        configs[1],
        "--role-instruction",
        "p2",
    )
    assert code == 1 and result["status"] == "not_isolated"
    assert result["target"]["changed"] and result["input_basis"] == BASIS
    assert result["other_changes"] == {"participant": 1, "world_setting": 1}
    assert result["reasons"] == ["additional_inputs_differ"]
    assert "loud" not in json.dumps(result) and "hurry" not in json.dumps(result)
    untouched(left, right)


def _replaced(**fields: Any) -> Callable[[dict[str, Any], Path], str]:
    return lambda document, _: json.dumps({**document, **fields})


def _support(**fields: Any) -> Callable[[dict[str, Any], Path], str]:
    return _replaced(participants=[CANONICAL, {**MINIMAL, **fields}])


def _environment(**fields: Any) -> Callable[[dict[str, Any], Path], str]:
    base = {"type": "docker", "delete": True, "env": {"HELLO_TONE": "plain"}}
    return _replaced(environment={**base, **fields})


def _other_task(document: dict[str, Any], other: Path) -> str:
    return json.dumps({**document, "task": {"path": str(other)}})


REFUSED: dict[str, Callable[[dict[str, Any], Path], str]] = {
    "other_task_path": _other_task,
    "remote_task": _replaced(
        task={"git_url": "https://example.invalid/tasks.git", "path": "hello-seats"}
    ),
    "external_skill": _replaced(
        agent={"name": "oracle", "skills": ["https://example.invalid/skills/probe"]}
    ),
    "custom_environment": _environment(type=None, import_path=f"{PROBE}.env:World"),
    "unknown_field": _replaced(surprise=True),
    "unknown_participant_field": _support(temperature=0.2),
    "duplicate_key": lambda d, _: '{"trial_name":"shadow",' + json.dumps(d)[1:],
    "coerced_bool": _environment(delete=1),
    "coerced_text": _support(skip_model_parsing="false"),
    "redacted_env": _environment(env={"HELLO_TONE": "plain", "SEAT_TOKEN": SECRET}),
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(REFUSED))
def test_unsupported_config_is_refused_without_a_result(
    roots: tuple[Path, Path], capsys, case: str
) -> None:
    left, right = roots
    good = write(left, "config.json", config(left, "l", CANONICAL, MINIMAL, "plain"))
    document = config(right, "r", CANONICAL, MINIMAL, "plain")
    bad = right.parent / "config.json"
    bad.write_text(REFUSED[case](document, left))
    code, out, err = run(
        capsys, "--configs", left, good, right, bad, "--participant", "p2", "--json"
    )
    assert code == 2 and out == ""
    assert err.startswith("Cannot verify the right local task")
    leaks = (SECRET, "****", str(left.parent), str(right.parent), "example.invalid")
    assert not [value for value in leaks if value in err]
    untouched(left, right)
