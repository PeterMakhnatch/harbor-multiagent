"""Pin the calendar adapter source for a trusted native study.

Keep the dataset, Harbor installation, trial outputs, and Python caches outside
this capsule. Set PYTHONDONTWRITEBYTECODE=1 when running the pinned study.
Record the SHA-256 printed by verify separately as the study's source pin.
"""

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path

_ADAPTER = Path("adapters/masbench-scheduling")
_STUDY = _ADAPTER / "study.py"
_TEMPLATE = _ADAPTER / "template"
_MANIFEST = Path("manifest.json")
_SCHEMA = "seatlab.calendar-source-capsule/v1"

# These are the unchanged files copied by materialize.py, not the generated
# case or per-role instructions. Keep the pinned inventory explicit.
_FIXED = tuple(
    map(
        Path,
        (
            "environment/Dockerfile",
            "environment/calendar_world.py",
            "environment/docker-compose.yaml",
            "environment/player/Dockerfile",
            "solution/client.py",
            "solution/solve.sh",
            "task.toml",
            "tests/test.sh",
            "tests/verify.py",
        ),
    )
)
_DYNAMIC = (
    Path("environment/case.json"),
    *(Path(f"instructions/m{seat}.md") for seat in range(1, 6)),
)
_SOURCE = (
    _ADAPTER / "cases.py",
    _ADAPTER / "materialize.py",
    _STUDY,
    _TEMPLATE / "instructions/common.md",
    _TEMPLATE / "instructions/scheduler.md",
    *(_TEMPLATE / path for path in _FIXED),
)


def _parents(paths: tuple[Path, ...]) -> set[Path]:
    return {parent for path in paths for parent in path.parents if parent != Path(".")}


def _require_dir(path: Path) -> None:
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"expected a directory, not a symlink or other entry: {path}")


def _check_tree(
    root: Path, required: tuple[Path, ...], optional: tuple[Path, ...] = ()
) -> None:
    _require_dir(root)
    directories = _parents(required + optional)
    files = set(required + optional)
    missing = set(required)

    def scan(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"symlink in source tree: {relative}")
            if relative in directories and stat.S_ISDIR(mode):
                scan(path)
            elif relative in files and stat.S_ISREG(mode):
                missing.discard(relative)
            else:
                raise ValueError(f"unexpected entry in source tree: {relative}")

    scan(root)
    if missing:
        raise ValueError(f"missing source file: {min(missing)}")


def _mode(path: Path) -> int:
    return 0o755 if path.suffix == ".sh" else 0o644


def _read_file(root: Path, relative: Path) -> bytes:
    path = root / relative
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"expected a regular file: {relative}")
    if stat.S_IMODE(mode) != _mode(relative):
        raise ValueError(f"unexpected mode for {relative}: {stat.S_IMODE(mode):04o}")
    return path.read_bytes()


def _write_file(root: Path, relative: Path, data: bytes) -> None:
    path = root / relative
    path.write_bytes(data)
    path.chmod(_mode(relative))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(digests: dict[Path, str]) -> bytes:
    document = {
        "schema": _SCHEMA,
        "files": {
            path.as_posix(): {"mode": f"{_mode(path):04o}", "sha256": digest}
            for path, digest in digests.items()
        },
    }
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("ascii")


def _create(out: Path) -> Path:
    source = Path(__file__).resolve().parents[2]
    directories = sorted(_parents(_SOURCE))
    _require_dir(source)
    for relative in directories:
        _require_dir(source / relative)

    # mkdir rejects every kind of existing destination, including dangling links.
    out.mkdir()
    try:
        out.chmod(0o755)
        for relative in directories:
            path = out / relative
            path.mkdir()
            path.chmod(0o755)
        digests = {}
        for relative in _SOURCE:
            data = _read_file(source, relative)
            _write_file(out, relative, data)
            digests[relative] = _sha(data)
        _write_file(out, _MANIFEST, _manifest(digests))
    except BaseException:
        shutil.rmtree(out)
        raise
    return out / _STUDY


def _verify(root: Path) -> tuple[dict[Path, str], str]:
    # Reject everything outside the exact inventory, including imports that
    # could shadow a pinned module and bytecode under __pycache__.
    _check_tree(root, (*_SOURCE, _MANIFEST))
    digests = {path: _sha(_read_file(root, path)) for path in _SOURCE}
    manifest = _read_file(root, _MANIFEST)
    # Reconstruct the canonical manifest from the tree, not paths supplied by it.
    if manifest != _manifest(digests):
        raise ValueError("manifest does not match the pinned source bytes")
    return digests, _sha(manifest)


def _check_cell(capsule: Path, task: Path) -> None:
    """Check the fixed files; native evidence must bind the generated inputs."""
    digests, _ = _verify(capsule)
    _check_tree(task, _FIXED, _DYNAMIC)
    for relative in _FIXED:
        if _sha(_read_file(task, relative)) != digests[_TEMPLATE / relative]:
            raise ValueError(f"source differs from capsule: {relative}")


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="copy source into a new capsule")
    create.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser(
        "verify", help="verify source and print manifest SHA-256"
    )
    verify.add_argument("root", type=Path)
    cell = commands.add_parser(
        "check-cell",
        help="compare fixed task source, not generated case or instructions",
    )
    cell.add_argument("capsule", type=Path)
    cell.add_argument("generated_task", type=Path)
    try:
        args = parser.parse_args()
        if args.command == "create":
            print(_create(args.out))
        elif args.command == "verify":
            _, manifest_sha = _verify(args.root)
            print(manifest_sha)
        else:
            _check_cell(args.capsule, args.generated_task)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    _main()
