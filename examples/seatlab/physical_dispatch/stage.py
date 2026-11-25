import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from ...native_study import MAX_TASK_BYTES, MAX_TASK_ENTRIES, require
from ...seat_checkpoints.materialize import dir_mode, read_plain, sync_directory, target
from ...ship_inflight.materialize import publish

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = PACKAGE_DIR.parent.parent
SOURCE_DIR = EXAMPLES_DIR / "tasks" / "exhibit-relay"
TASK_NAME = "harbor/exhibit-relay-physical"
SCHEMA = "harbor.exhibit-relay-physical.materialized/v1"
BASE = {
    "name": "harbor/exhibit-relay",
    "digest": "sha256:fd75b8cecf9ce446a4e8d3e772dabecafa9506e0d8c6f0ea30b483cacd008b00",
    "checksum": "360200d8f30a79dbc532e4610ed2da31496c65f395b8e72fbc61e246efb02353",
}
BASE_FILES = "sha256:345ee7e9b1ef621ec1bc5c9e048ca3e09490ef5e18e610a0ee862f4f6154c8f2"
STOCK_DIRS = (
    "environment",
    "environment/exhibit_relay",
    "environment/seat",
    "instructions",
    "solution",
    "tests",
)
STOCK_FILES = (
    "AUTHORED.md",
    "PAIRING.md",
    "PROTOCOL.md",
    "README.md",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/exhibit_relay/__init__.py",
    "environment/exhibit_relay/cases.py",
    "environment/exhibit_relay/diagnostics.py",
    "environment/exhibit_relay/engine.py",
    "environment/exhibit_relay/frontier.py",
    "environment/exhibit_relay/grading.py",
    "environment/exhibit_relay/notes.py",
    "environment/seat/Dockerfile",
    "environment/seat/relay.py",
    "environment/serve.py",
    "instructions/depot.md",
    "instructions/loader.md",
    "instructions/registrar.md",
    "solution/policies.py",
    "solution/reference.py",
    "solution/solve.sh",
    "task.toml",
    "tests/test.sh",
    "tests/verify.py",
)
ADDED = "environment/exhibit_relay/physical_cases.py"
REVISIONS = (
    ("task.toml", 'name = "harbor/exhibit-relay"\n', f'name = "{TASK_NAME}"\n'),
    (
        "task.toml",
        'description = "Coordinate a museum exhibit dispatch using limited records across three private roles."\n',
        'description = "Coordinate a museum exhibit dispatch across three private roles under a controlled physical bay map."\n',
    ),
    (
        "task.toml",
        'EXHIBIT_LAYOUT = "draw"\n',
        'EXHIBIT_LAYOUT = "draw"\nEXHIBIT_BAY_MAP = "original"\n',
    ),
    (
        "environment/docker-compose.yaml",
        '      EXHIBIT_LAYOUT: "${EXHIBIT_LAYOUT:-draw}"\n',
        (
            '      EXHIBIT_LAYOUT: "${EXHIBIT_LAYOUT:-draw}"\n'
            '      EXHIBIT_BAY_MAP: "${EXHIBIT_BAY_MAP:-original}"\n'
        ),
    ),
    (
        "environment/serve.py",
        "from exhibit_relay.engine import Session\n",
        (
            "from exhibit_relay.engine import Session\n"
            "from exhibit_relay.physical_cases import select\n"
        ),
    ),
    (
        "environment/serve.py",
        "    session = Session(make_case(seed, profile, layout=layout), mode)\n",
        (
            '    map_name = os.environ.get("EXHIBIT_BAY_MAP", "original")\n'
            '    if (profile, mode, layout) != ("fork", "linked", "draw"):\n'
            '        raise ValueError("Physical dispatch requires fork/linked/draw")\n'
            '    if map_name not in ("original", "swapped"):\n'
            '        raise ValueError("Invalid physical bay map")\n'
            "    session = Session(select(make_case(seed, profile, layout=layout), map_name), mode)\n"
        ),
    ),
)

File = tuple[bytes, int]


def read_tree(
    root: Path, names: tuple[str, ...]
) -> tuple[dict[str, int], dict[str, File]]:
    directories = {"": dir_mode(root)}
    files: dict[str, File] = {}
    pending = [""]
    entries = 0
    budget = MAX_TASK_BYTES
    for _ in range(len(STOCK_DIRS) + 1):
        if not pending:
            break
        prefix = pending.pop()
        with os.scandir(root / prefix) as listing:
            for entry in listing:
                entries += 1
                require(entries <= MAX_TASK_ENTRIES, "too many task entries")
                relative = prefix + entry.name
                if relative in STOCK_DIRS:
                    require(
                        entry.is_dir(follow_symlinks=False),
                        f"invalid directory {relative}",
                    )
                    directories[relative] = dir_mode(root / relative)
                    pending.append(relative + "/")
                else:
                    require(relative in names, f"unexpected task entry {relative}")
                    require(
                        entry.is_file(follow_symlinks=False), f"invalid file {relative}"
                    )
                    data, mode = read_plain(root / relative)
                    budget -= len(data)
                    require(budget >= 0, "task exceeds byte budget")
                    files[relative] = (data, mode)
    require(not pending, "incomplete directory walk")
    require(set(directories) == {"", *STOCK_DIRS}, "task directories differ")
    require(set(files) == set(names), "task files differ")
    return directories, files


def stock() -> tuple[dict[str, int], dict[str, File]]:
    directories, files = read_tree(SOURCE_DIR, STOCK_FILES)
    for name in directories:
        directories[name] = 0o755
    header = f"{BASE['name']}\0{BASE['digest']}\0{BASE['checksum']}\n"
    digest = hashlib.sha256(header.encode("ascii"))
    for relative, (data, mode) in sorted(files.items()):
        checksum = hashlib.sha256(data).hexdigest()
        digest.update(f"{relative}\0{checksum}\n".encode("ascii"))
        executable = relative in ("tests/test.sh", "solution/solve.sh")
        require(
            bool(mode & 0o111) == executable,
            f"stock executable bit differs: {relative}",
        )
        files[relative] = (data, 0o755 if executable else 0o644)
    require("sha256:" + digest.hexdigest() == BASE_FILES, "stock task differs from pin")
    return directories, files


def expected() -> tuple[dict[str, int], dict[str, File]]:
    directories, files = stock()
    directories[""] = 0o700
    for relative, old, new in REVISIONS:
        data, mode = files[relative]
        text = data.decode("utf-8")
        require(text.count(old) == 1, f"stock revision differs: {relative}")
        files[relative] = (text.replace(old, new).encode("utf-8"), mode)
    for relative, source in (("README.md", "TASK-GUIDE.md"), (ADDED, "worlds.py")):
        data, _ = read_plain(PACKAGE_DIR / source)
        require(
            bool(data.decode("utf-8").strip()) and data.endswith(b"\n"),
            f"invalid {source}",
        )
        mode = files[relative][1] if relative in files else 0o644
        files[relative] = (data, mode)
    return directories, files


def verify(root: Path, directories: dict[str, int], files: dict[str, File]) -> str:
    actual_dirs, actual_files = read_tree(root, tuple(files))
    require(actual_dirs == directories, "staged directory modes differ")
    digest = hashlib.sha256(b"exhibit-relay-physical/v1\n")
    for relative, mode in sorted(directories.items()):
        digest.update(f"directory\0{relative}\0{mode:o}\n".encode("ascii"))
    for relative, (data, mode) in sorted(files.items()):
        require(
            actual_files[relative] == (data, mode), f"staged file differs: {relative}"
        )
        checksum = hashlib.sha256(data).hexdigest()
        digest.update(f"file\0{relative}\0{mode:o}\0{checksum}\n".encode("ascii"))
    return "sha256:" + digest.hexdigest()


def check_stage(task: Path) -> str:
    return verify(task.expanduser().absolute(), *expected())


def fill(
    root: Path,
    directories: dict[str, int],
    files: dict[str, File],
    created_dirs: list[Path],
    created_files: list[Path],
) -> None:
    for relative in sorted(STOCK_DIRS):
        path = root / relative
        path.mkdir(mode=0o700)
        created_dirs.append(path)
    for relative, (data, mode) in sorted(files.items()):
        path = root / relative
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created_files.append(path)
        with open(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
    for relative, mode in sorted(directories.items(), reverse=True):
        path = root / relative
        path.chmod(mode)
        sync_directory(path)


def materialize(out: Path) -> dict[str, Any]:
    out = target(str(out))
    require(
        not out.is_relative_to(EXAMPLES_DIR.parent), "OUT must be outside the checkout"
    )
    directories, files = expected()
    root = Path(tempfile.mkdtemp(prefix=".exhibit-physical-stage-", dir=out.parent))
    created_dirs = [root]
    created_files: list[Path] = []
    published = False
    try:
        fill(root, directories, files, created_dirs, created_files)
        stage_digest = verify(root, directories, files)
        publish(root, out)
        published = True
    finally:
        if not published:
            for path in created_dirs:
                path.chmod(0o700)
            for path in reversed(created_files):
                path.unlink()
            for path in reversed(created_dirs):
                path.rmdir()
    sync_directory(out.parent)
    return {
        "schema": SCHEMA,
        "task": TASK_NAME,
        "path": str(out),
        "base": dict(BASE),
        "base_files": BASE_FILES,
        "stage_digest": stage_digest,
        "files": {
            relative: "sha256:" + hashlib.sha256(data).hexdigest()
            for relative, (data, _) in sorted(files.items())
        },
    }
