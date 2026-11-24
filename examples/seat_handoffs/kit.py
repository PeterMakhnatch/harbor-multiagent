import argparse
import errno
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

SCHEMA = "harbor.handoff-kit/v1"
PACKAGE = "handoff_v1"
MEMBERS = ("__init__.py", "core.py", "producer.py")
SOURCE = Path(__file__).parent
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 64
PRIVATE_DIR, PUBLIC_DIR, PRIVATE_FILE, PUBLIC_FILE = 0o700, 0o755, 0o600, 0o644
OPEN_DEST = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC
OPEN_DIR = OPEN_DEST | os.O_NOFOLLOW
OPEN_FILE = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
CREATE_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
EXIT_CODES = {"match": 0, "absent": 1, "incomplete": 1, "drift": 1}

Kit = dict[str, bytes]
Json = dict[str, Any]


class KitError(Exception):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_digest(hashes: dict[str, str]) -> str:
    assert set(hashes) == set(MEMBERS)
    listing = "".join(f"{hashes[name]}  {name}\n" for name in MEMBERS)
    return _sha256(listing.encode())


def _kind(dir_fd: int, name: str) -> str:
    try:
        mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"


def _read(dir_fd: int, name: str) -> tuple[str, bytes | None, int | None]:
    kind = _kind(dir_fd, name)
    if kind != "file":
        return ("not_file" if kind in ("dir", "other") else kind), None, None
    try:
        fd = os.open(name, OPEN_FILE, dir_fd=dir_fd)
    except FileNotFoundError:
        return "missing", None, None
    except OSError as error:
        if error.errno != errno.ELOOP:
            raise
        return "symlink", None, None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return "not_file", None, None
        data = b""
        while len(data) <= MAX_MEMBER_BYTES:
            chunk = os.read(fd, MAX_MEMBER_BYTES + 1 - len(data))
            if not chunk:
                return "file", data, stat.S_IMODE(info.st_mode)
            data += chunk
        return "too_large", None, None
    finally:
        os.close(fd)


def _open_dir(path: Path | str, flags: int, dir_fd: int | None = None) -> int:
    fd = os.open(path, flags, dir_fd=dir_fd)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
    return fd


def load_kit(source: Path) -> Kit:
    try:
        source_fd = _open_dir(source, OPEN_DEST)
    except OSError as error:
        raise KitError(f"cannot open canonical source {source}: {error.strerror}")
    try:
        kit: Kit = {"__init__.py": b""}
        for name in MEMBERS[1:]:
            state, data, _ = _read(source_fd, name)
            if data is None:
                raise KitError(f"canonical {source / name} is {state}")
            kit[name] = data
    finally:
        os.close(source_fd)
    return kit


def _member(name: str, expected: bytes, package_fd: int | None) -> Json:
    unchecked: tuple[str, bytes | None, int | None] = ("unchecked", None, None)
    state, found, mode = unchecked if package_fd is None else _read(package_fd, name)
    if found is not None:
        same = found == expected
        state = "mismatch" if not same else "match" if mode == PUBLIC_FILE else "mode"
    return {
        "name": name,
        "state": state,
        "expected_sha256": _sha256(expected),
        "expected_bytes": len(expected),
        "found_sha256": None if found is None else _sha256(found),
        "found_bytes": None if found is None else len(found),
        "found_mode": None if mode is None else f"{mode:04o}",
    }


def _unexpected(package_fd: int) -> list[str]:
    names: list[str] = []
    with os.scandir(package_fd) as entries:
        for count, entry in enumerate(entries):
            if count >= MAX_ENTRIES:
                raise KitError(f"{PACKAGE} has more than {MAX_ENTRIES} entries")
            pycache = entry.name == "__pycache__"
            if pycache and entry.is_dir(follow_symlinks=False):
                continue
            if entry.name not in MEMBERS:
                names.append(entry.name)
    return sorted(names)


def _inspect(package_fd: int | None, kit: Kit) -> Json:
    mode = None if package_fd is None else stat.S_IMODE(os.fstat(package_fd).st_mode)
    members = [_member(name, kit[name], package_fd) for name in MEMBERS]
    found = {member["name"]: member["found_sha256"] for member in members}
    return {
        "package_mode": None if mode is None else f"{mode:04o}",
        "package_sha256": package_digest({n: _sha256(kit[n]) for n in MEMBERS}),
        "found_package_sha256": package_digest(found) if all(found.values()) else None,
        "members": members,
        "unexpected": [] if package_fd is None else _unexpected(package_fd),
    }


def _complete(report: Json) -> bool:
    exact = all(member["state"] == "match" for member in report["members"])
    return exact and not report["unexpected"]


def _report(dest_fd: int, kit: Kit, action: str) -> Json:
    kind = _kind(dest_fd, PACKAGE)
    if kind != "dir":
        state = {"missing": "absent", "symlink": "symlink"}.get(kind, "not_dir")
        status = "absent" if state == "absent" else "drift"
        report = _inspect(None, kit)
        return report | {"status": status, "action": action, "package_state": state}
    package_fd = _open_dir(PACKAGE, OPEN_DIR, dest_fd)
    try:
        report = _inspect(package_fd, kit)
    finally:
        os.close(package_fd)
    state, status = "present", "drift"
    if report["package_mode"] == f"{PRIVATE_DIR:04o}":
        state, status = "private", "incomplete"
    elif report["package_mode"] == f"{PUBLIC_DIR:04o}" and _complete(report):
        status = "match"
    return report | {"status": status, "action": action, "package_state": state}


def _write(package_fd: int, name: str, data: bytes) -> None:
    fd = os.open(name, CREATE_FILE, PRIVATE_FILE, dir_fd=package_fd)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fchmod(fd, PUBLIC_FILE)
        os.fsync(fd)
    finally:
        os.close(fd)


def _fill(dest_fd: int, kit: Kit) -> None:
    package_fd = _open_dir(PACKAGE, OPEN_DIR, dest_fd)
    try:
        os.fchmod(package_fd, PRIVATE_DIR)
        for name in MEMBERS:
            _write(package_fd, name, kit[name])
        if not _complete(_inspect(package_fd, kit)):
            raise KitError("written members do not verify")
        os.fchmod(package_fd, PUBLIC_DIR)
        os.fsync(package_fd)
    finally:
        os.close(package_fd)
    os.fsync(dest_fd)


def _open_dest(dest: Path) -> int:
    try:
        return _open_dir(dest, OPEN_DEST)
    except OSError as error:
        raise KitError(f"build context {dest} is unusable: {error.strerror}")


def check(dest: Path, kit: Kit) -> Json:
    dest_fd = _open_dest(dest)
    try:
        return _report(dest_fd, kit, "checked")
    finally:
        os.close(dest_fd)


def install(dest: Path, kit: Kit) -> Json:
    dest_fd = _open_dest(dest)
    try:
        try:
            os.mkdir(PACKAGE, PRIVATE_DIR, dir_fd=dest_fd)
        except FileExistsError:
            report = _report(dest_fd, kit, "refused")
            if report["status"] == "match":
                report["action"] = "already_installed"
            return report
        try:
            _fill(dest_fd, kit)
        except (KitError, OSError) as error:
            raise KitError(f"install interrupted, {PACKAGE} left incomplete: {error}")
        return _report(dest_fd, kit, "installed")
    finally:
        os.close(dest_fd)


def _text(report: Json) -> list[str]:
    lines = [
        (
            f"{report['status']}: {report['dest']!a} {PACKAGE} "
            f"({report['action']}, package {report['package_state']})"
        ),
        f"package sha256 {report['package_sha256']}",
        f"found   sha256 {report['found_package_sha256'] or '-'}",
    ]
    for member in report["members"]:
        lines.append(
            f"  {member['state']:<9} {member['name']:<11} {member['expected_sha256']}"
            f" found {member['found_sha256'] or '-'} mode {member['found_mode'] or '-'}"
        )
    lines.extend(f"  unexpected {name!a}" for name in report["unexpected"])
    return lines


def _parser() -> argparse.ArgumentParser:
    module = "examples.seat_handoffs.kit"
    parser = argparse.ArgumentParser(prog=f"python3.12 -BS -m {module}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "check"):
        command = commands.add_parser(name)
        command.add_argument("dest", type=Path, help="existing trusted build context")
        command.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None, source: Path = SOURCE) -> int:
    args = _parser().parse_args(argv)
    head = {"schema": SCHEMA, "command": args.command, "dest": str(args.dest)}
    try:
        kit = load_kit(source)
        run = install if args.command == "install" else check
        report = head | run(args.dest, kit)
    except (KitError, OSError) as error:
        message = str(error)
        if args.json:
            print(json.dumps(head | {"status": "error", "error": message}))
        else:
            print(f"error: {message!a}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else "\n".join(_text(report)))
    return EXIT_CODES[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
