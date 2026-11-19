"""Calendar source capsule behavior without Docker or source case data."""

import hashlib
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = Path("adapters/masbench-scheduling")
SNAPSHOT = ADAPTER_ROOT / "snapshot.py"
STUDY = ADAPTER / "study.py"
TEMPLATE = ADAPTER / "template"
FIXED = (
    "task.toml",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/calendar_world.py",
    "environment/player/Dockerfile",
    "solution/solve.sh",
    "solution/client.py",
    "tests/test.sh",
    "tests/verify.py",
)
SOURCES = (
    STUDY,
    ADAPTER / "cases.py",
    ADAPTER / "materialize.py",
    TEMPLATE / "instructions/common.md",
    TEMPLATE / "instructions/scheduler.md",
    *(TEMPLATE / relative for relative in FIXED),
)


def _run(
    cwd: Path, *args: str | Path, script: Path = SNAPSHOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(script), *(str(arg) for arg in args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        umask=0o077,
    )


def _succeeds(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr


@pytest.fixture
def source_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    for relative in (*SOURCES, ADAPTER / "snapshot.py"):
        source = ADAPTER_ROOT / relative.relative_to(ADAPTER)
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
    return checkout


@pytest.fixture
def capsule(tmp_path: Path) -> Path:
    root = tmp_path / "capsule"
    _succeeds(_run(tmp_path, "create", "--out", root))
    return root


@pytest.fixture
def generated_task(tmp_path: Path, capsule: Path) -> Path:
    task = tmp_path / "generated-task"
    for relative in FIXED:
        path = task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(capsule / TEMPLATE / relative, path)
    (task / "environment/case.json").write_text('{"mode":"full"}', encoding="ascii")
    instructions = task / "instructions"
    instructions.mkdir()
    for seat in range(1, 6):
        (instructions / f"m{seat}.md").write_text(f"Full report, seat {seat}\n")
    return task


def test_create_selects_only_approved_files_and_is_reproducible(
    tmp_path: Path, source_checkout: Path
) -> None:
    # The source checkout may contain caches, local results, or new files that
    # have not been approved. It is not the process working directory.
    (source_checkout / ".env").write_text("SOURCE_SECRET=not-copied\n")
    (source_checkout / TEMPLATE / "tests/__init__.py").write_text("not copied\n")
    cache = source_checkout / TEMPLATE / "tests/__pycache__"
    cache.mkdir()
    (cache / "verify.cpython-312.pyc").write_bytes(b"not copied")
    source_script = source_checkout / ADAPTER / SNAPSHOT.name
    root = tmp_path / "capsule"
    repeat = tmp_path / "repeat"

    create = _run(tmp_path, "create", "--out", root, script=source_script)
    _succeeds(create)
    assert Path(create.stdout.strip()) == root / STUDY
    _succeeds(_run(tmp_path, "create", "--out", repeat, script=source_script))
    assert {path.relative_to(root) for path in root.rglob("*") if path.is_file()} == {
        *SOURCES,
        Path("manifest.json"),
    }
    manifest = (root / "manifest.json").read_bytes()
    assert manifest == (repeat / "manifest.json").read_bytes()
    expected = {}
    for relative in SOURCES:
        frozen = root / relative
        assert frozen.read_bytes() == (source_checkout / relative).read_bytes()
        mode = 0o755 if relative.suffix == ".sh" else 0o644
        assert stat.S_IMODE(frozen.stat().st_mode) == mode
        expected[relative.as_posix()] = {
            "sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
            "mode": f"{mode:04o}",
        }
    assert json.loads(manifest)["files"] == expected

    # Changing the live checkout after creation cannot edit files in the capsule.
    (source_checkout / STUDY).write_bytes(b"different implementation\n")
    pin = _run(tmp_path, "verify", root)
    _succeeds(pin)
    assert pin.stdout.strip() == hashlib.sha256(manifest).hexdigest()
    _succeeds(_run(tmp_path, "verify", repeat))


def test_create_does_not_replace_destination(tmp_path: Path, capsule: Path) -> None:
    manifest = (capsule / "manifest.json").read_bytes()
    assert _run(tmp_path, "create", "--out", capsule).returncode != 0
    assert (capsule / "manifest.json").read_bytes() == manifest

    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")
    assert _run(tmp_path, "create", "--out", dangling).returncode != 0
    assert dangling.is_symlink()


def test_create_rejects_symlinked_source(tmp_path: Path, source_checkout: Path) -> None:
    world = TEMPLATE / "environment/calendar_world.py"
    path = source_checkout / world
    path.unlink()
    path.symlink_to(ADAPTER_ROOT / world.relative_to(ADAPTER))
    root = tmp_path / "unused"
    create = _run(
        tmp_path,
        "create",
        "--out",
        root,
        script=source_checkout / ADAPTER / SNAPSHOT.name,
    )
    assert create.returncode != 0
    assert not root.exists()


@pytest.mark.parametrize(
    "change",
    ["edit", "permissions", "delete", "symlink", "extra_source", "cache", "manifest"],
)
def test_verify_rejects_drift(tmp_path: Path, capsule: Path, change: str) -> None:
    world = capsule / TEMPLATE / "environment/calendar_world.py"
    if change == "edit":
        world.write_bytes(world.read_bytes() + b"\n# drift\n")
    elif change == "permissions":
        world.chmod(0o755)
    elif change == "delete":
        world.unlink()
    elif change == "symlink":
        world.unlink()
        world.symlink_to(ADAPTER_ROOT / "template/environment/calendar_world.py")
    elif change == "extra_source":
        (world.parent / "extra.py").touch(mode=0o755)
    elif change == "cache":
        cache = world.parent / "__pycache__"
        cache.mkdir()
        (cache / "calendar_world.cpython-312.pyc").touch()
    else:
        (capsule / "manifest.json").write_bytes(b'{"files": {"../study.py": {}}}\n')
    assert _run(tmp_path, "verify", capsule).returncode != 0


def test_check_cell_ignores_generated_data_and_instructions(
    tmp_path: Path, capsule: Path, generated_task: Path
) -> None:
    _succeeds(_run(tmp_path, "check-cell", capsule, generated_task))
    (generated_task / "environment/case.json").write_text('{"mode":"first8"}')
    (generated_task / "instructions/m1.md").write_text("First 8 starts only\n")
    _succeeds(_run(tmp_path, "check-cell", capsule, generated_task))


@pytest.mark.parametrize("change", ["edit", "permissions", "delete", "extra_source"])
def test_check_cell_rejects_fixed_source_drift(
    tmp_path: Path, capsule: Path, generated_task: Path, change: str
) -> None:
    script = generated_task / "solution/solve.sh"
    if change == "edit":
        script.write_bytes(script.read_bytes() + b"\n# old implementation\n")
    elif change == "permissions":
        script.chmod(0o644)
    elif change == "delete":
        script.unlink()
    else:
        (script.parent / "extra.sh").touch(mode=0o755)
    assert _run(tmp_path, "check-cell", capsule, generated_task).returncode != 0
