"""Stage the opt-in `harbor/shift-board-checkpoint` task from the frozen Shift Board.

Usage: `python3 -BS -m examples.seat_checkpoints.materialize OUT`. Python API:
`materialize(target(OUT))` returns the printed record. OUT must be a new directory
outside examples/. The published `examples/tasks/shift-board` must match the
compiled-in `task_files_digest` of the frozen identity BASE, and the staged stock
copy is checked against it again. Only the task name, the artifacts list, the main
Dockerfile COPY operands and the main Compose command change; board_world.py and
mark.py join environment/. OUT appears complete or not at all, nothing existing is
overwritten, and only files this run created are ever removed.
"""

import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from ..native_study import check_identity, require, task_files_digest

SCHEMA = "harbor.seat-cut.materialized/v1"
PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = PACKAGE_DIR.parent
SOURCE_DIR = EXAMPLES_DIR / "tasks" / "shift-board"
TASK_NAME = "harbor/shift-board-checkpoint"
BASE: dict[str, str] = {
    "name": "harbor/shift-board",
    "digest": "sha256:c2bd100c5bfb4b69887e338947b370fa16413ea0f51cf70f34934e2d7c8fd289",
    "checksum": "648b6de6ebf27da2404274c46a286f1ec15e49ea0f91bdb11307d79e4fe738cd",
}
BASE_FILES = "sha256:2abaf24f67836936f83e7e70388a39e82235a0c6879f8d44f891a854d610f4c1"
STOCK_DIRS = ("environment", "environment/seat", "instructions", "solution", "tests")
STOCK_FILES = (
    "README.md",
    "environment/Dockerfile",
    "environment/board.py",
    "environment/docker-compose.yaml",
    "environment/seat/Dockerfile",
    "environment/world.py",
    "instructions/dispatcher.md",
    "instructions/planner.md",
    "solution/solve.sh",
    "task.toml",
    "tests/test.sh",
    "tests/verify.py",
)
ADDED = (
    ("environment/board_world.py", "board_world.py"),
    ("environment/mark.py", "mark.py"),
)
REVISIONS: tuple[tuple[str, str, str], ...] = (
    (
        "task.toml",
        'name = "harbor/shift-board"\n',
        f'name = "{TASK_NAME}"\n',
    ),
    (
        "task.toml",
        'artifacts = ["/logs/world/shift-board-id.json"]\n',
        'artifacts = ["/logs/world/shift-board-id.json", "/logs/world/seat-cut.json"]\n',
    ),
    (
        "environment/Dockerfile",
        "COPY board.py world.py /app/\n",
        "COPY board.py world.py board_world.py mark.py /app/\n",
    ),
    (
        "environment/docker-compose.yaml",
        '    command: ["python3", "/app/world.py"]\n',
        '    command: ["python3", "/app/board_world.py"]\n',
    ),
)
OUT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_FILE_BYTES = 1 << 20
MAX_TREE_ENTRIES = 64
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

assert tuple(sorted(STOCK_FILES)) == STOCK_FILES
assert all(path in STOCK_FILES for path, _, _ in REVISIONS)


def read_plain(path: Path) -> tuple[bytes, int]:
    with open(os.open(path, READ_FLAGS), "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode), f"{path} is not a regular file")
        data = handle.read(MAX_FILE_BYTES + 1)
    require(len(data) <= MAX_FILE_BYTES, f"{path} exceeds {MAX_FILE_BYTES} bytes")
    return data, stat.S_IMODE(info.st_mode)


def dir_mode(path: Path) -> int:
    info = os.lstat(path)
    require(stat.S_ISDIR(info.st_mode), f"{path} is not a plain directory")
    return stat.S_IMODE(info.st_mode)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def target(text: str) -> Path:
    require(isinstance(text, str) and text != "", "OUT is empty")
    raw = Path(text) if Path(text).is_absolute() else Path.cwd() / text
    require(OUT_NAME.fullmatch(raw.name) is not None, "OUT must end in a plain name")
    parent = raw.parent.resolve(strict=True)
    dir_mode(parent)
    out = parent / raw.name
    require(not out.is_relative_to(EXAMPLES_DIR), "OUT must lie outside examples/")
    require(not os.path.lexists(out), f"{out} already exists")
    return out


class Stage:
    def __init__(self, parent: Path) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=".seat-cut-stage-", dir=parent))
        self._dirs: list[Path] = [self.root]
        self._files: list[Path] = []
        self._modes: list[tuple[Path, int]] = []

    def mkdir(self, relative: str, mode: int) -> None:
        path = self.root / relative
        os.mkdir(path, 0o700)
        self._dirs.append(path)
        self._modes.append((path, mode))

    def write(self, relative: str, data: bytes, mode: int) -> None:
        assert relative in STOCK_FILES or relative in dict(ADDED)
        path = self.root / relative
        fd = os.open(path, CREATE_FLAGS, 0o600)
        self._files.append(path)
        with open(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)

    def rewrite(self, relative: str, data: bytes) -> None:
        path = self.root / relative
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        os.unlink(path)
        self._files.remove(path)
        self.write(relative, data, mode)

    def seal(self, root_mode: int) -> None:
        for path, mode in reversed(self._modes):
            sync_directory(path)
            os.chmod(path, mode)
        sync_directory(self.root)
        os.chmod(self.root, root_mode)

    def discard(self) -> None:
        for path in self._dirs:
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        for path in reversed(self._files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for path in reversed(self._dirs):
            try:
                path.rmdir()
            except OSError:
                pass


def copy_stock(stage: Stage) -> None:
    for relative in STOCK_DIRS:
        stage.mkdir(relative, dir_mode(SOURCE_DIR / relative))
    for relative in STOCK_FILES:
        data, mode = read_plain(SOURCE_DIR / relative)
        stage.write(relative, data, mode)


def revise(stage: Stage) -> None:
    for relative in dict.fromkeys(path for path, _, _ in REVISIONS):
        data, _ = read_plain(stage.root / relative)
        text = data.decode("utf-8")
        for path, old, new in REVISIONS:
            if path == relative:
                require(text.count(old) == 1, f"{relative} lacks one {old.strip()!r}")
                text = text.replace(old, new)
        stage.rewrite(relative, text.encode("utf-8"))


def add(stage: Stage) -> None:
    mode = stat.S_IMODE(os.lstat(stage.root / "environment" / "world.py").st_mode)
    for relative, name in ADDED:
        data, _ = read_plain(PACKAGE_DIR / name)
        stage.write(relative, data, mode)


def check_tree(root: Path) -> None:
    pending = [("", 0)]
    dirs: list[str] = []
    files: list[str] = []
    for _ in range(MAX_TREE_ENTRIES):
        if not pending:
            break
        prefix, depth = pending.pop()
        require(depth <= 2, f"staged {prefix} is too deep")
        with os.scandir(root / prefix) as listing:
            for entry in listing:
                relative = prefix + entry.name
                require(
                    OUT_NAME.fullmatch(entry.name) is not None, f"staged {relative}"
                )
                require(entry.name != "__pycache__", f"staged {relative}")
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(relative)
                    pending.append((relative + "/", depth + 1))
                else:
                    require(entry.is_file(follow_symlinks=False), f"staged {relative}")
                    files.append(relative)
    require(not pending, f"staged tree exceeds {MAX_TREE_ENTRIES} directories")
    require(sorted(dirs) == sorted(STOCK_DIRS), "staged directories differ")
    expected = sorted((*STOCK_FILES, *dict(ADDED)))
    require(sorted(files) == expected, "staged files differ")


def publish(stage: Stage, out: Path) -> None:
    os.mkdir(out, 0o700)
    claim = os.lstat(out)
    try:
        current = os.lstat(out)
        same = (current.st_dev, current.st_ino) == (claim.st_dev, claim.st_ino)
        require(same and stat.S_ISDIR(current.st_mode), f"{out} changed")
        require(not os.listdir(out), f"{out} changed")
        os.rename(stage.root, out)
    except BaseException:
        release_claim(out, claim)
        raise


def release_claim(out: Path, claim: os.stat_result) -> None:
    try:
        current = os.lstat(out)
        if (current.st_dev, current.st_ino) == (claim.st_dev, claim.st_ino):
            out.rmdir()
    except OSError:
        pass


def materialize(out: Path) -> dict[str, object]:
    require(out.is_absolute() and not os.path.lexists(out), f"{out} is not new")
    base = check_identity(dict(BASE))
    require(
        task_files_digest(SOURCE_DIR, base) == BASE_FILES,
        "published harbor/shift-board differs from the frozen base",
    )
    stage = Stage(out.parent)
    published = False
    try:
        copy_stock(stage)
        require(
            task_files_digest(stage.root, base) == BASE_FILES,
            "staged stock copy differs from the frozen base",
        )
        revise(stage)
        add(stage)
        check_tree(stage.root)
        stage.seal(dir_mode(SOURCE_DIR))
        publish(stage, out)
        published = True
    finally:
        if not published:
            stage.discard()
    sync_directory(out.parent)
    return {
        "schema": SCHEMA,
        "task": TASK_NAME,
        "path": str(out),
        "base": base,
        "base_files": BASE_FILES,
    }


def main(argv: list[str]) -> int:
    try:
        require(len(argv) == 1, "usage: materialize OUT")
        record = materialize(target(argv[0]))
    except (ValueError, OSError) as error:
        sys.stderr.write(f"materialize: {error}\n")
        return 1
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
