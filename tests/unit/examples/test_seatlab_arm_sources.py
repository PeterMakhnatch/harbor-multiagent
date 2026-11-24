import hashlib
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examples.seatlab.workbench import arm_sources
from examples.seatlab.workbench.arm_sources import GROUPS, Source, inspect_source
from harbor.models.job.lock import _build_lock_trial_task
from harbor.models.task.task import Task
from harbor.models.trial.config import TaskConfig
from harbor.publisher.packager import Packager

STOCK = Path(__file__).resolve().parents[3] / "examples" / "tasks"
SEATS = {"hello-seats": ("p1", "p2"), "ship-handover": ("outgoing", "relief")}


def copy(name: str, parent: Path, as_name: str | None = None) -> Path:
    target = parent / (as_name or name)
    shutil.copytree(STOCK / name, target, symlinks=True)
    return target


def native_lock(task_dir: Path) -> dict[str, Any]:
    download = SimpleNamespace(
        path=task_dir, content_hash=None, resolved_git_commit_id=None
    )
    lock = _build_lock_trial_task(TaskConfig(path=task_dir), download)
    return lock.model_dump(mode="json")


def refused(root: Path, lock: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError) as error:
        inspect_source(root, lock)
    assert str(error.value) == message


def changed(left: Source, right: Source) -> set[str]:
    return {group for group in GROUPS if left.groups[group] != right.groups[group]}


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def chmod(path: Path, executable: bool) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    path.chmod(mode | stat.S_IXUSR if executable else mode & ~bits)


def raw_role(root: Path, seat: str) -> str:
    data = (root / "instructions" / f"{seat}.md").read_bytes()
    return hashlib.sha256(data).hexdigest()


def loaded_role(root: Path, seat: str) -> str:
    text = Task(root).seat_instruction(seat)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def role_files(name: str) -> set[str]:
    return {f"instructions/{seat}.md" for seat in SEATS[name]}


def packaged(root: Path) -> set[str]:
    task_dir = root.resolve()
    return {
        path.relative_to(task_dir).as_posix()
        for path in Packager.collect_files(task_dir)
    }


def rewrite_role(path: Path, rewrite: str) -> None:
    data = path.read_bytes()
    assert b"\r" not in data and b"canary" not in data.lower()
    if rewrite == "crlf":
        path.write_bytes(data.replace(b"\n", b"\r\n"))
    else:
        assert rewrite == "canary"
        path.write_bytes(b"<!-- harbor canary 3f9c -->\n\n" + data)


@pytest.fixture
def legacy_packager(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    def omit_roles(paths: Any, spec: Any, file_filter: Any) -> list[Path]:
        return []

    monkeypatch.setattr(Packager, "_seat_instruction_files", staticmethod(omit_roles))
    return monkeypatch


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(SEATS))
def test_stock_source_binds_native_lock_with_role_coverage(
    tmp_path: Path, name: str
) -> None:
    root = copy(name, tmp_path)
    lock = native_lock(root)
    source = inspect_source(root, lock)
    assert lock["type"] == "local" and lock["name"] == name
    assert source.seats == SEATS[name]
    assert source.roles == {seat: raw_role(root, seat) for seat in SEATS[name]}
    assert source.effective_roles == {
        seat: loaded_role(root, seat) for seat in SEATS[name]
    }
    assert tuple(source.groups) == GROUPS
    assert role_files(name) <= packaged(root)
    assert source.native_digest == lock["digest"]
    assert source.native_digest == "sha256:" + Packager.compute_content_hash(root)[0]
    assert source.lock_covers_roles is True
    assert source.unpackaged_files == 0
    assert inspect_source(STOCK / name, lock) == source


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(SEATS))
def test_legacy_lock_omits_roles_and_fails_the_current_package(
    tmp_path: Path, name: str, legacy_packager: pytest.MonkeyPatch
) -> None:
    root = copy(name, tmp_path)
    lock = native_lock(root)
    legacy = inspect_source(root, lock)
    assert not role_files(name) & packaged(root)
    assert legacy.native_digest == lock["digest"]
    assert legacy.lock_covers_roles is False
    assert legacy.unpackaged_files == 0
    legacy_packager.undo()
    refused(root, lock, arm_sources.WRONG_DIGEST)
    current = inspect_source(root, native_lock(root))
    assert current.native_digest != legacy.native_digest
    assert current.lock_covers_roles is True
    assert current.roles == legacy.roles
    assert current.effective_roles == legacy.effective_roles
    assert current.groups == legacy.groups


@pytest.mark.unit
def test_relocated_copy_binds_without_trusting_lock_path(tmp_path: Path) -> None:
    left = copy("ship-handover", tmp_path / "left")
    right = copy("ship-handover", tmp_path / "right")
    lock = native_lock(left)
    assert inspect_source(right, lock) == inspect_source(left, lock)
    elsewhere = {**lock, "path": str(tmp_path / "gone"), "source": "elsewhere"}
    assert inspect_source(right, elsewhere) == inspect_source(left, lock)
    renamed = copy("ship-handover", tmp_path, "ship-handover-b")
    refused(renamed, lock, arm_sources.WRONG_NAME)


@pytest.mark.unit
def test_role_text_change_needs_its_own_lock_that_covers_it(tmp_path: Path) -> None:
    base = copy("hello-seats", tmp_path / "base")
    arm = copy("hello-seats", tmp_path / "arm")
    lock = native_lock(base)
    append(arm / "instructions" / "p2.md", "Answer in one line.\n")
    refused(arm, lock, arm_sources.WRONG_DIGEST)
    arm_lock = native_lock(arm)
    refused(base, arm_lock, arm_sources.WRONG_DIGEST)
    left, right = inspect_source(base, lock), inspect_source(arm, arm_lock)
    assert changed(left, right) == set()
    assert left.roles["p1"] == right.roles["p1"]
    assert right.roles["p2"] == raw_role(arm, "p2") != left.roles["p2"]
    assert left.effective_roles["p1"] == right.effective_roles["p1"]
    assert right.effective_roles["p2"] == loaded_role(arm, "p2")
    assert right.effective_roles["p2"] != left.effective_roles["p2"]
    assert right.native_digest == arm_lock["digest"] != left.native_digest
    assert left.lock_covers_roles is True and right.lock_covers_roles is True
    assert right.unpackaged_files == 0


@pytest.mark.unit
def test_legacy_role_text_change_keeps_old_digest_without_proof(
    tmp_path: Path, legacy_packager: pytest.MonkeyPatch
) -> None:
    base = copy("hello-seats", tmp_path / "base")
    arm = copy("hello-seats", tmp_path / "arm")
    lock = native_lock(base)
    append(arm / "instructions" / "p2.md", "Answer in one line.\n")
    left, right = inspect_source(base, lock), inspect_source(arm, lock)
    assert changed(left, right) == set()
    assert left.roles["p1"] == right.roles["p1"]
    assert left.roles["p2"] != right.roles["p2"]
    assert left.effective_roles["p1"] == right.effective_roles["p1"]
    assert left.effective_roles["p2"] != right.effective_roles["p2"]
    assert right.native_digest == left.native_digest
    assert right.lock_covers_roles is False


@pytest.mark.unit
@pytest.mark.parametrize("rewrite", ["crlf", "canary"])
def test_non_content_role_rewrite_needs_its_own_lock_but_keeps_loaded_text(
    tmp_path: Path, rewrite: str
) -> None:
    base = copy("ship-handover", tmp_path / "base")
    arm = copy("ship-handover", tmp_path / "arm")
    lock = native_lock(base)
    rewrite_role(arm / "instructions" / "relief.md", rewrite)
    refused(arm, lock, arm_sources.WRONG_DIGEST)
    arm_lock = native_lock(arm)
    left, right = inspect_source(base, lock), inspect_source(arm, arm_lock)
    assert changed(left, right) == set()
    assert right.roles["relief"] == raw_role(arm, "relief") != left.roles["relief"]
    assert Task(arm).seat_instruction("relief") == Task(base).seat_instruction("relief")
    assert right.effective_roles["relief"] == loaded_role(arm, "relief")
    assert left.effective_roles == right.effective_roles
    assert right.native_digest == arm_lock["digest"] != left.native_digest
    assert right.lock_covers_roles is True


@pytest.mark.unit
@pytest.mark.parametrize("rewrite", ["crlf", "canary"])
def test_legacy_non_content_role_rewrite_keeps_old_digest_without_proof(
    tmp_path: Path, rewrite: str, legacy_packager: pytest.MonkeyPatch
) -> None:
    base = copy("ship-handover", tmp_path / "base")
    arm = copy("ship-handover", tmp_path / "arm")
    lock = native_lock(base)
    rewrite_role(arm / "instructions" / "relief.md", rewrite)
    left, right = inspect_source(base, lock), inspect_source(arm, lock)
    assert changed(left, right) == set()
    assert left.roles["relief"] != right.roles["relief"]
    assert left.effective_roles == right.effective_roles
    assert right.native_digest == left.native_digest
    assert right.lock_covers_roles is False


@pytest.mark.unit
def test_grader_change_needs_new_lock_and_moves_verifier(tmp_path: Path) -> None:
    base = copy("ship-handover", tmp_path / "base")
    arm = copy("ship-handover", tmp_path / "arm")
    lock = native_lock(base)
    append(arm / "tests" / "verify.py", "\n")
    refused(arm, lock, arm_sources.WRONG_DIGEST)
    left, right = inspect_source(base, lock), inspect_source(arm, native_lock(arm))
    assert changed(left, right) == {"verifier"}
    assert left.roles == right.roles
    assert left.native_digest != right.native_digest


@pytest.mark.unit
@pytest.mark.parametrize(
    ("group", "rel"),
    [
        ("environment", "environment/world.py"),
        ("solution", "solution/solve.sh"),
        ("config", "task.toml"),
        ("other", "README.md"),
    ],
)
def test_other_non_role_changes_land_in_their_group(
    tmp_path: Path, group: str, rel: str
) -> None:
    base = copy("ship-handover", tmp_path / "base")
    arm = copy("ship-handover", tmp_path / "arm")
    append(arm / rel, "\n")
    left = inspect_source(base, native_lock(base))
    right = inspect_source(arm, native_lock(arm))
    assert changed(left, right) == {group}
    assert left.roles == right.roles


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rel", "executable"),
    [("instructions/relief.md", True), ("tests/test.sh", False)],
)
def test_mode_only_change_moves_only_executable_modes(
    tmp_path: Path, rel: str, executable: bool
) -> None:
    base = copy("ship-handover", tmp_path / "base")
    arm = copy("ship-handover", tmp_path / "arm")
    lock = native_lock(base)
    chmod(arm / rel, executable)
    left, right = inspect_source(base, lock), inspect_source(arm, lock)
    assert changed(left, right) == {"executable_modes"}
    assert left.roles == right.roles


@pytest.mark.unit
def test_wrong_bindings_are_refused(tmp_path: Path) -> None:
    hello = copy("hello-seats", tmp_path)
    ship = copy("ship-handover", tmp_path)
    lock = native_lock(hello)
    refused(ship, lock, arm_sources.WRONG_NAME)
    refused(hello, {**lock, "name": "harbor/hello-seats"}, arm_sources.WRONG_NAME)
    refused(hello, {**lock, "type": "package"}, arm_sources.NOT_LOCAL)
    refused(hello, {**lock, "git_url": "https://x.invalid/r"}, arm_sources.NOT_LOCAL)
    refused(hello, {**lock, "version": "1.0"}, arm_sources.WRONG_VERSION)
    refused(hello, {**lock, "digest": "sha256:" + "0" * 64}, arm_sources.WRONG_DIGEST)
    refused(hello, {**lock, "digest": "0" * 64}, arm_sources.BAD_LOCK)
    refused(hello, {**lock, "extra": 1}, arm_sources.BAD_LOCK)
    refused(hello, {"name": "hello-seats"}, arm_sources.BAD_LOCK)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rel", "group"),
    [
        ("notes.txt", "other"),
        ("instructions/p3.md", "other"),
        ("tests/__pycache__/verify.cpython-312.pyc", "verifier"),
    ],
)
def test_unregistered_files_are_detected(tmp_path: Path, rel: str, group: str) -> None:
    base = copy("hello-seats", tmp_path / "base")
    arm = copy("hello-seats", tmp_path / "arm")
    lock = native_lock(base)
    (arm / rel).parent.mkdir(exist_ok=True)
    (arm / rel).write_text("unregistered\n")
    left, right = inspect_source(base, lock), inspect_source(arm, lock)
    assert changed(left, right) == {group}
    assert right.unpackaged_files == 1
    assert right.native_digest == left.native_digest


@pytest.mark.unit
@pytest.mark.parametrize("rel", [".DS_Store", "environment/.cache/x", "tests/.env"])
def test_hidden_files_are_refused(tmp_path: Path, rel: str) -> None:
    root = copy("hello-seats", tmp_path)
    lock = native_lock(root)
    (root / rel).parent.mkdir(exist_ok=True)
    (root / rel).write_text("hidden\n")
    refused(root, lock, arm_sources.HIDDEN)


@pytest.mark.unit
def test_roster_defects_are_refused(tmp_path: Path) -> None:
    lock = native_lock(copy("hello-seats", tmp_path / "lock"))
    missing = copy("hello-seats", tmp_path / "missing")
    (missing / "instructions" / "p2.md").unlink()
    refused(missing, lock, arm_sources.MISSING_ROLE)
    folded = copy("hello-seats", tmp_path / "folded")
    append(folded / "task.toml", "\n[agents.P1]\n")
    refused(folded, lock, arm_sources.ALIASED)
    linked = copy("hello-seats", tmp_path / "linked")
    os.link(linked / "instructions" / "p1.md", linked / "instructions" / "p3.md")
    append(linked / "task.toml", "\n[agents.p3]\n")
    refused(linked, lock, arm_sources.ALIASED)
    twice = copy("hello-seats", tmp_path / "twice")
    append(twice / "task.toml", "\n[agents.p1]\n")
    refused(twice, lock, arm_sources.BAD_CONFIG)
    bare = copy("ship-handover", tmp_path / "bare")
    config = bare / "task.toml"
    config.write_text(config.read_text().split("[agents.outgoing]")[0])
    refused(bare, lock, arm_sources.NO_ROSTER)
    absent = copy("hello-seats", tmp_path / "absent")
    (absent / "task.toml").unlink()
    refused(absent, lock, arm_sources.NO_CONFIG)


@pytest.mark.unit
def test_special_and_unbounded_inputs_are_refused(tmp_path: Path) -> None:
    root = copy("hello-seats", tmp_path / "base")
    lock = native_lock(root)
    refused(Path("relative/hello-seats"), lock, arm_sources.BAD_ROOT)
    alias = tmp_path / "alias"
    alias.symlink_to(root)
    refused(alias, lock, arm_sources.BAD_ROOT)
    linked = copy("hello-seats", tmp_path / "linked")
    (linked / "environment" / "link").symlink_to(linked / "README.md")
    refused(linked, lock, arm_sources.BAD_TREE)
    piped = copy("hello-seats", tmp_path / "piped")
    os.mkfifo(piped / "tests" / "pipe")
    refused(piped, lock, arm_sources.BAD_TREE)
    wide = copy("hello-seats", tmp_path / "wide")
    for index in range(300):
        (wide / "environment" / f"f{index}").write_text("x")
    refused(wide, lock, arm_sources.BAD_TREE)
    heavy = copy("hello-seats", tmp_path / "heavy")
    (heavy / "environment" / "blob").write_bytes(
        b"x" * (arm_sources.MAX_TASK_BYTES + 1)
    )
    refused(heavy, lock, arm_sources.BAD_TREE)
    deep = copy("hello-seats", tmp_path / "deep")
    nested = deep.joinpath("environment", *["d"] * 9)
    nested.mkdir(parents=True)
    (nested / "x").write_text("x")
    refused(deep, lock, arm_sources.BAD_TREE)


@pytest.mark.unit
def test_source_moving_during_inspection_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy("hello-seats", tmp_path)
    lock = native_lock(root)
    original = Packager.compute_content_hash

    def touch(task_dir: Path) -> tuple[str, list[Path]]:
        readme = task_dir / "README.md"
        mtime = readme.stat().st_mtime_ns + 1_000_000_000
        os.utime(readme, ns=(mtime, mtime))
        return original(task_dir)

    def grow(task_dir: Path) -> tuple[str, list[Path]]:
        (task_dir / "tests" / "late.sh").write_text("late\n")
        return original(task_dir)

    monkeypatch.setattr(Packager, "compute_content_hash", staticmethod(touch))
    refused(root, lock, arm_sources.MOVED)
    monkeypatch.setattr(Packager, "compute_content_hash", staticmethod(grow))
    refused(root, lock, arm_sources.DRIFT)


@pytest.mark.unit
def test_refusals_never_name_the_root(tmp_path: Path) -> None:
    root = copy("hello-seats", tmp_path)
    (root / "instructions" / "p1.md").unlink()
    with pytest.raises(ValueError) as error:
        inspect_source(root, native_lock(copy("hello-seats", tmp_path / "lock")))
    assert str(tmp_path) not in str(error.value)
    assert "p1" not in str(error.value)
