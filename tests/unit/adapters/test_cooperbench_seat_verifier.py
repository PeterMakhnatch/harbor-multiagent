"""The CooperBench seat verifier (seats/tests/test.sh) and the per-seat oracle
(seats/solution/solve.sh), run as shell scripts on throwaway git repositories,
without Docker. A stand-in for the image's runner.sh applies the merged patch
and a feature's test patch, then runs the check that test patch adds."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

TEMPLATE = (
    Path(__file__).parents[3]
    / "adapters/cooperbench/src/cooperbench/task-template/seats"
)
VERIFIER = TEMPLATE / "tests/test.sh"
SOLVE = TEMPLATE / "solution/solve.sh"
COOP_MSG = TEMPLATE / "environment/seat/coop_msg.py"
SEATS = ("agent1", "agent2")

FAKE_RUNNER = """#!/bin/bash
# Stand-in for runner.sh: reset, apply the merged patch, apply the test
# patch, run the check the test patch adds.
set -e
cd "$COOPERBENCH_REPO"
git reset -q --hard HEAD
git clean -fdxq
git apply "$COOPERBENCH_PATCHES/$2"
git apply "$COOPERBENCH_PATCHES/$1"
if [ -n "${TAMPER:-}" ] && [ "$1" = tests1.patch ]; then
    echo garbage > "$COOPERBENCH_PATCHES/tests2.patch"
    echo garbage > "$COOPERBENCH_PATCHES/merged.patch"
fi
bash tests/check.sh
"""


def check_patch(command: str, *, final_newline: bool = True) -> str:
    """A test patch that adds tests/check.sh running ``command``."""
    patch = (
        "diff --git a/tests/check.sh b/tests/check.sh\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/check.sh\n"
        "@@ -0,0 +1 @@\n"
        f"+{command}\n"
    )
    return patch if final_newline else patch.rstrip("\n")


def git(cwd: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@dataclass
class World:
    root: Path
    env: dict[str, str]
    seats: dict[str, Path] = field(default_factory=dict)

    @property
    def out(self) -> Path:
        return self.root / "verifier"

    def seat_file(self, seat: str, path: str) -> Path:
        target = self.seats[seat] / path
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def verify(
        self, exit_code: int = 0, **extra_env: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run the verifier; Harbor reads only its reward file, not its exit
        code, so a verifier error exits non-zero after writing reward 0."""
        result = subprocess.run(
            ["bash", str(VERIFIER)],
            env={**self.env, **extra_env},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == exit_code, result.stdout + result.stderr
        reward = json.loads((self.out / "reward.json").read_text())
        details = json.loads((self.out / "details.json").read_text())
        return reward, details

    def merged(self) -> bytes:
        return (self.out / "patches/merged.patch").read_bytes()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """main's base repository, one copy per seat, the test patches, the
    stand-in runner, and main's message logs."""
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    (tmp_path / "gitconfig").write_text("")
    repo = tmp_path / "main/repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/app.py").write_text("def app():\n    return 0\n")
    (repo / "src/other.py").write_text("def other():\n    return 0\n")
    (repo / "docs/win.txt").write_bytes(b"old\r\ncontext\r\n")
    (repo / "tests/test_app.py").write_text("def test_app():\n    pass\n")
    git(repo, "init", "-q", env=env)
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", "base", env=env)

    w = World(root=tmp_path, env=env)
    for seat in SEATS:
        w.seats[seat] = tmp_path / "agents" / seat / "repo"
        shutil.copytree(repo, w.seats[seat], symlinks=True)

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "tests1.patch").write_text(check_patch("grep -q FEATURE_ONE src/app.py"))
    (tests / "tests2.patch").write_text(check_patch("grep -q FEATURE_TWO src/other.py"))
    runner = tmp_path / "runner.sh"
    runner.write_text(FAKE_RUNNER)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)

    coop = tmp_path / "coop"
    coop.mkdir()
    message = {"id": "m1", "from": "agent1", "to": "agent2", "content": "plan"}
    (coop / "messages.jsonl").write_text(json.dumps(message) + "\n")
    recv = {"seq": 2, "seat": "agent2", "kind": "recv", "data": {"ids": ["m1"]}}
    (coop / "events.jsonl").write_text(json.dumps(recv) + '\n{"seq": 3, "trunc')

    env.update(
        COOPERBENCH_REPO=str(repo),
        COOPERBENCH_SEAT_REPOS=str(tmp_path / "agents"),
        COOPERBENCH_PATCHES=str(tmp_path / "patches"),
        COOPERBENCH_TESTS=str(tests),
        COOPERBENCH_VERIFIER_LOGS=str(tmp_path / "verifier"),
        COOPERBENCH_COOP_LOGS=str(coop),
        COOPERBENCH_RUNNER=str(runner),
    )
    return w


def implement(w: World, *seats: str) -> None:
    if "agent1" in seats:
        with w.seat_file("agent1", "src/app.py").open("a") as fh:
            fh.write("FEATURE_ONE = 1\n")
    if "agent2" in seats:
        with w.seat_file("agent2", "src/other.py").open("a") as fh:
            fh.write("FEATURE_TWO = 2\n")


def test_both_features_earn_reward_one(world: World):
    implement(world, "agent1", "agent2")

    reward, details = world.verify()

    assert reward == {"reward": 1}
    assert details["strategy"] == "naive"
    assert details["patch_files"] == {
        "agent1": ["src/app.py"],
        "agent2": ["src/other.py"],
    }
    assert details["apply_ok"] == {"agent1": True, "agent2": True}
    # main's logs are read line by line; the truncated last line is skipped
    assert details["messages_sent"] == {"agent1": 1, "agent2": 0}
    assert details["messages_received"] == {"agent1": 0, "agent2": 1}


def test_an_idle_seat_earns_reward_zero(world: World):
    implement(world, "agent1")

    reward, details = world.verify()

    assert reward == {"reward": 0}
    assert (details["feature1_passed"], details["feature2_passed"]) == (True, False)
    assert details["patch_files"]["agent2"] == []
    assert details["apply_ok"]["agent2"] is None


def test_a_crlf_edit_survives_the_test_file_filter(world: World):
    implement(world, "agent1", "agent2")
    world.seat_file("agent1", "docs/win.txt").write_bytes(b"new\r\ncontext\r\n")

    reward, details = world.verify()

    assert reward == {"reward": 1}
    assert details["apply_ok"] == {"agent1": True, "agent2": True}
    assert b"+new\r\n" in world.merged()


def test_non_utf8_content_is_kept_byte_for_byte(world: World):
    implement(world, "agent1", "agent2")
    with world.seat_file("agent2", "src/other.py").open("ab") as fh:
        fh.write(b"# caf\xe9\n")

    reward, details = world.verify()

    assert reward == {"reward": 1}
    assert b"+# caf\xe9\n" in world.merged()


def test_test_files_and_test_infrastructure_are_dropped(world: World):
    implement(world, "agent1", "agent2")
    world.seat_file("agent1", "conftest.py").write_text("import os\nos._exit(0)\n")
    world.seat_file("agent1", "pytest.ini").write_text("[pytest]\naddopts = -p no:x\n")
    world.seat_file("agent1", "tests/test_app.py").write_text(
        "def test_app():\n    1/0\n"
    )

    reward, details = world.verify()

    assert reward == {"reward": 1}
    assert sorted(details["dropped_files"]["agent1"]) == [
        "conftest.py",
        "pytest.ini",
        "tests/test_app.py",
    ]
    merged = world.merged()
    assert b"conftest.py" not in merged and b"pytest.ini" not in merged
    assert b"tests/test_app.py" not in merged


def test_a_patch_that_does_not_apply_fails_the_merge(world: World, tmp_path: Path):
    """Nothing is dropped silently: a non-empty patch that git apply rejects
    gives reward 0 and says so (the rejection is injected with a git shim)."""
    implement(world, "agent1", "agent2")
    shim = tmp_path / "shim"
    shim.mkdir()
    real_git = shutil.which("git", path=world.env["PATH"])
    (shim / "git").write_text(
        "#!/bin/bash\n"
        'if [ "$1" = apply ] && [ "$2" = --check ] && [[ "$3" == *agent2.patch ]]; then\n'
        '  echo "error: patch failed: src/other.py:1" >&2; exit 1\n'
        "fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    (shim / "git").chmod(0o755)

    reward, details = world.verify(PATH=f"{shim}{os.pathsep}{world.env['PATH']}")

    assert reward == {"reward": 0}
    assert details["merge_ok"] is False
    assert details["apply_ok"] == {"agent1": True, "agent2": False}
    assert details["error"] == (
        "agent2's patch does not apply: error: patch failed: src/other.py:1"
    )


def test_a_verifier_error_still_writes_reward_zero(world: World, tmp_path: Path):
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()

    reward, details = world.verify(exit_code=128, COOPERBENCH_REPO=str(not_a_repo))

    assert reward == {"reward": 0}
    assert details["error"].startswith("verifier error (exit 128) at line ")
    assert "git rev-parse HEAD" in details["error"]


def test_each_feature_run_gets_fresh_patches(world: World):
    """The first run's code overwrites the second run's test patch and the
    merged patch; the verifier copies both again before the second run."""
    implement(world, "agent1", "agent2")

    reward, _ = world.verify(TAMPER="1")

    assert reward == {"reward": 1}


def test_conflicting_edits_fall_back_to_a_union_merge(world: World):
    (world.root / "tests/tests2.patch").write_text(
        check_patch("grep -q FEATURE_TWO src/app.py")
    )
    implement(world, "agent1")
    with world.seat_file("agent2", "src/app.py").open("a") as fh:
        fh.write("FEATURE_TWO = 2\n")

    reward, details = world.verify()

    assert reward == {"reward": 1}
    assert details["strategy"] == "union"


def test_dspy_testfix_is_appended_to_a_test_patch_without_the_new_expectation(
    world: World,
):
    tests = world.root / "tests"
    (tests / "tests1.patch").write_text(
        check_patch(
            """grep -q FEATURE_ONE src/app.py && test ! -f tests/fix  # tool_calls": []"""
        )
    )
    (tests / "tests2.patch").write_text(
        check_patch(
            "grep -q FEATURE_TWO src/other.py && test -f tests/fix", final_newline=False
        )
    )
    (tests / "testfix.patch").write_text(
        "diff --git a/tests/fix b/tests/fix\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/fix\n"
        "@@ -0,0 +1 @@\n"
        "+fixed\n"
    )
    implement(world, "agent1", "agent2")

    reward, _ = world.verify()

    assert reward == {"reward": 1}


# -- the per-seat oracle ------------------------------------------------------


def test_the_oracle_applies_only_its_own_seats_patch(tmp_path: Path, world: World):
    spec = importlib.util.spec_from_file_location(
        "coop_server", TEMPLATE / "environment/coop_server.py"
    )
    assert spec and spec.loader
    server_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server_module)
    tokens = tmp_path / "tokens"
    for seat in SEATS:
        (tokens / seat).mkdir(parents=True)
    server = server_module.make_server(
        list(SEATS), tokens, tmp_path / "coop-logs", host="127.0.0.1", port=0
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("send", "broadcast", "recv", "peek", "agents"):
        wrapper = bin_dir / f"coop-{command}"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{COOP_MSG}" {command} "$@"\n'
        )
        wrapper.chmod(0o755)
    solution = tmp_path / "solution"
    solution.mkdir()
    shutil.copy2(SOLVE, solution / "solve.sh")
    (solution / "agent1.patch").write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def app():\n"
        "     return 0\n"
        "+FEATURE_ONE = 1\n"
    )
    (solution / "agent2.patch").write_text("not for agent1\n")
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        result = subprocess.run(
            ["bash", str(solution / "solve.sh")],
            env={
                **world.env,
                "PATH": f"{bin_dir}{os.pathsep}{world.env['PATH']}",
                "COOP_AGENT_ID": "agent1",
                "COOP_URL": url,
                "COOP_TOKEN_FILE": str(tokens / "agent1/token"),
                "COOPERBENCH_REPO": str(world.seats["agent1"]),
                "COOP_ORACLE_WAIT_SEC": "1",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        inbox = subprocess.run(
            [sys.executable, str(COOP_MSG), "recv"],
            env={
                **os.environ,
                "COOP_URL": url,
                "COOP_TOKEN_FILE": str(tokens / "agent2/token"),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FEATURE_ONE = 1" in (world.seats["agent1"] / "src/app.py").read_text()
    assert [(m["from"], m["content"]) for m in json.loads(inbox.stdout)] == [
        ("agent1", "I plan to modify: src/app.py"),
        ("agent1", "Done. I changed: src/app.py"),
    ]
