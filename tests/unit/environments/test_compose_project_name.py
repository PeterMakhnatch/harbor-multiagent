"""The Docker Compose project name carries no part of the trial or task name.

Compose puts the project name into container, network and volume names, which
processes in the environment can read (reverse DNS, /proc/self/mountinfo). A
session id is ``{trial_name}__{role}`` and a trial name starts with the task
name, so the project name must be a hash of the session id.
"""

import ast
import re
from pathlib import Path

import pytest

from harbor.environments import base
from harbor.environments.docker.compose_env import compose_project_name

# Session ids as Harbor builds them: {task_name[:32]}__{7 random chars}__{role}.
SESSION_IDS = {
    "schedule-004-silent__cdeu7ht__env": "schedule-004-silent",
    "schedule-004-silent__cdeu7ht__verifier__grade": "schedule-004-silent",
    "saboteur-role.v2__Q3xYz9a__env": "saboteur-role.v2",
    "install-windows-3.11__rEVvMQt__env": "install-windows-3.11",
    "hello-seats__bZZeEkw__env": "hello-seats",
}


@pytest.mark.parametrize("session_id", SESSION_IDS)
def test_name_carries_no_part_of_the_session_id(session_id: str) -> None:
    name = compose_project_name(session_id)
    task_name = SESSION_IDS[session_id]
    trial_name = "__".join(session_id.split("__")[:2])

    # Only a fixed prefix and hex digits, so no word of the task name fits.
    assert re.fullmatch(r"hb-[0-9a-f]{12}", name), name
    assert task_name.lower() not in name
    assert trial_name.lower() not in name
    for word in re.split(r"[^a-z]+", task_name.lower()):
        if len(word) > 2:
            assert word not in name


@pytest.mark.parametrize("session_id", [*SESSION_IDS, "My Task/Run:42.0", "--x"])
def test_name_is_a_valid_compose_project_name_and_dns_label(session_id: str) -> None:
    name = compose_project_name(session_id)

    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
    assert re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", name)


def test_name_is_stable_for_one_session_id() -> None:
    # Pinned: start, exec, stop and cleanup, in this process or a later one,
    # must all reach the same project for one session id.
    assert compose_project_name("schedule-004-silent__cdeu7ht__env") == (
        "hb-15a2f00b60a6"
    )
    assert compose_project_name("schedule-004-silent__cdeu7ht__env") == (
        compose_project_name("schedule-004-silent__cdeu7ht__env")
    )


def test_each_session_gets_its_own_project() -> None:
    names = {compose_project_name(session_id) for session_id in SESSION_IDS}

    assert len(names) == len(SESSION_IDS)


# The two definitions of the name: the helper and the BaseEnvironment property.
_ALLOWED = {
    ("docker/compose_env.py", "compose_project_name"),
    ("base.py", "compose_project_name"),
}


def _derives_project_name_from_session_id(function: ast.AST, name: str) -> bool:
    names: set[str] = set()
    constants: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants.add(node.value)
    if "session_id" not in names:
        return False
    return (
        "project" in name
        or "--project-name" in constants
        or ("-p" in constants and "compose" in constants)
        or any("com.docker.compose.project" in constant for constant in constants)
    )


def test_no_environment_derives_a_compose_project_name_from_the_session_id() -> None:
    """Static check, like test_compose_contract: a function that builds a
    Compose project name (or a ``-p``/``--project-name`` argument, or a
    project label filter) must take it from ``compose_project_name``, not
    read ``session_id`` itself. Guards Harbor merges that add a provider."""
    root = Path(base.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (relative, node.name) in _ALLOWED:
                continue
            if _derives_project_name_from_session_id(node, node.name):
                offenders.append(f"{relative}:{node.lineno} {node.name}")

    assert not offenders, (
        "These functions build a Compose project name from session_id, which "
        "carries the task name into container and network names. Use "
        f"BaseEnvironment.compose_project_name instead: {offenders}"
    )


def test_static_check_flags_a_session_id_project_name() -> None:
    """Guard the heuristic itself against silent rot."""
    source = (
        "class Ops:\n"
        "    @property\n"
        "    def _project_name(self):\n"
        "        return self._env.session_id.lower()\n"
        "    def _compose_cmd(self, sub):\n"
        "        return ['docker', 'compose', '-p', self._env.session_id, *sub]\n"
        "    def _compose_neutral(self, sub):\n"
        "        return ['docker', 'compose', '-p', self._project_name, *sub]\n"
    )
    flagged = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and _derives_project_name_from_session_id(node, node.name)
    }

    assert flagged == {"_project_name", "_compose_cmd"}
