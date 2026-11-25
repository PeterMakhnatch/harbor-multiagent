"""qa.py solution-access on real file modes: Harbor uploads solution/ to a /solution owned by root and group root, so a
seat's player user reaches it only through the other bits; the task folder and its parents stay private."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

QA = load(FLOTILLA / "qa.py")
Finding = QA.Finding
PAYLOAD = ("family.py", "family_policy.py", "oracle.py", "policy.json", "policy.py")
SOLVE_SH = '#!/bin/bash\nset -eu\npython3 "$(dirname "$0")/policy.py"\n'
SEED = 6_151_806_725_431_072


@pytest.fixture
def umask() -> Iterator[None]:
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def write_task(task: Path, mask: int) -> Path:
    """A task folder written under a umask, the way build_tasks.py writes one: solve.sh chmod 0755, all else
    as the umask leaves it; environment/task.json and tests/ hold the truth outside the solver payload."""
    previous = os.umask(mask)
    try:
        solution = task / "solution"
        solution.mkdir(parents=True)
        (solution / "solve.sh").write_text(SOLVE_SH)
        (solution / "solve.sh").chmod(0o755)
        for name in PAYLOAD:
            (solution / name).write_text("{}\n")
        (task / "environment").mkdir()
        (task / "environment" / "task.json").write_text('{"answer_key": {"day": 1}}\n')
        (task / "tests").mkdir()
        (task / "tests" / "test.sh").write_text("#!/bin/bash\n")
        (task / "task.toml").write_text('[task]\nname = "flotilla/t-000000000000"\n')
    finally:
        os.umask(previous)
    return solution


def modes(root: Path) -> dict[Path, int]:
    return {path: path.lstat().st_mode for path in (root, *root.rglob("*"))}


def access(task: Path) -> list[Finding]:
    return list(QA.check_solution_access(QA.Task(path=task, name=task.name)))


def named(findings: list[Finding]) -> dict[str, str]:
    """Path -> detail of each solution-access finding."""
    assert all(f.rule == "solution-access" and f.level == "FAIL" and f.seat == "*" for f in findings)
    return {f.detail.split(" is mode ")[0].rsplit(" ", 1)[-1]: f.detail for f in findings}


def test_a_umask_077_payload_fails_at_each_path_and_qa_changes_no_mode(tmp_path):
    task = tmp_path / "out" / "t-000000000000"
    write_task(task, 0o077)
    before = modes(tmp_path)
    found = named(access(task))
    assert set(found) == {"solution", *(f"solution/{name}" for name in PAYLOAD)}
    assert found["solution"] == (
        "folder solution is mode 0700: the player user cannot list or enter it once uploaded; ship it with o+rx "
        "(chmod o+rx solution, inside solution/ only; the task folder and its parents stay private)")
    assert found["solution/policy.json"] == (
        "file solution/policy.json is mode 0600: the player user cannot read it once uploaded; ship it with o+r "
        "(chmod o+r solution/policy.json, inside solution/ only; the task folder and its parents stay private)")
    assert modes(tmp_path) == before


def test_a_private_root_with_an_accessible_payload_passes(tmp_path):
    task = tmp_path / "out" / "t-000000000000"
    solution = write_task(task, 0o077)
    lib = solution / "lib"
    lib.mkdir(mode=0o755)
    (lib / "solve.sh").write_text(SOLVE_SH)
    solution.chmod(0o755)
    lib.chmod(0o755)
    for path in (*(solution / name for name in PAYLOAD), lib / "solve.sh"):
        path.chmod(0o644)
    (tmp_path / "out").chmod(0o700)
    (solution / "truth.json").symlink_to(task / "environment" / "task.json")
    assert stat.S_IMODE(task.stat().st_mode) == 0o700
    assert stat.S_IMODE((task / "environment" / "task.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((task / "tests").stat().st_mode) == 0o700
    assert access(task) == []


@pytest.mark.parametrize(("mode", "missing", "verbs"), [(0o744, "x", "execute"), (0o711, "r", "read"),
                                                       (0o700, "rx", "read or execute")])
def test_the_entry_script_must_be_readable_and_executable(tmp_path, mode, missing, verbs):
    task = tmp_path / "t-000000000000"
    solution = write_task(task, 0o022)
    (solution / "solve.sh").chmod(mode)
    assert named(access(task)) == {"solution/solve.sh": (
        f"entry script solution/solve.sh is mode {mode:04o}: the player user cannot {verbs} it once uploaded; ship "
        f"it with o+{missing} (chmod o+{missing} solution/solve.sh, inside solution/ only; the task folder and its "
        "parents stay private)")}


def test_a_nested_folder_needs_list_and_enter(tmp_path):
    task = tmp_path / "t-000000000000"
    solution = write_task(task, 0o022)
    (solution / "lib").mkdir(mode=0o755)
    (solution / "lib" / "helper.py").write_text("\n")
    (solution / "lib" / "helper.py").chmod(0o644)
    (solution / "lib").chmod(0o751)
    assert set(named(access(task))) == {"solution/lib"}


def test_a_task_without_a_solver_payload_has_no_finding(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir(mode=0o700)
    (bare / "task.toml").write_text('[task]\nname = "other/bare"\n')
    assert access(bare) == []
    plain = tmp_path / "plain"
    write_task(plain, 0o022)
    assert access(plain) == []


def test_a_built_task_passes_until_its_payload_turns_private(tmp_path, umask):
    pytest.importorskip("yaml")
    build = load(FLOTILLA / "build_tasks.py")
    build.build("schedule", [0], 2, "easy", SEED, build.CONDITIONS[:1], tmp_path, readable_names=True)
    (path,) = QA.find_tasks([str(tmp_path)])
    tmp_path.chmod(0o700)
    path.chmod(0o700)
    found, _tasks = QA.check([path], allow_readable_names=True)
    assert not [f for f in found if f.rule == "solution-access"]
    (path / "solution").chmod(0o700)
    (path / "solution" / "oracle.py").chmod(0o600)
    found, _tasks = QA.check([path], allow_readable_names=True)
    assert set(named([f for f in found if f.rule == "solution-access"])) == {"solution", "solution/oracle.py"}
    assert all(f.task == path.name for f in found if f.rule == "solution-access")
