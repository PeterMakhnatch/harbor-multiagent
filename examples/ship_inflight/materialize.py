"""Stage the opt-in `harbor/ship-inflight` task from the pinned Ship Handover.

Usage: `python3 -B -m examples.ship_inflight.materialize OUT`. OUT must be a new
directory outside examples/; a private parent directory is recommended. The
published `examples/tasks/ship-handover` must hold exactly the 12 pinned files
with the pinned bytes and package identity, and is only read. The main image
gains inflight_world.py, the tests gain inflight_verify.py beside the stock
verify.py, both role instructions are replaced, and task.toml, README.md, the
main Dockerfile COPY, the main Compose command, tests/test.sh and the control
solve.sh are revised. Seat images and role-only token mounts stay byte for byte.
Staged files get git-equivalent public modes; the checkout is never changed. OUT
appears complete or not at all, and only files this run created are removed.
"""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from ..native_study import check_identity, require, task_files_digest
from ..seat_checkpoints.materialize import (
    dir_mode,
    read_plain,
    release_claim,
    sync_directory,
    target,
)

SCHEMA = "harbor.ship-inflight.materialized/v1"
PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = PACKAGE_DIR.parent / "tasks" / "ship-handover"
TASK_NAME = "harbor/ship-inflight"
BASE: dict[str, str] = {
    "name": "harbor/ship-handover",
    "digest": "sha256:79e59fb1e00f2269ea46118cd28933005f21f4c3f6a29fe58079125febb20607",
    "checksum": "4bc30f977321363eb46432239b5df43dcae88cce405aaef671bf978b9283bf40",
}
BASE_FILES = "sha256:454f01b76a674e0285163cfcb5c4825c2c5414d0e5650286672effbecbe48235"
STOCK_DIRS = ("environment", "environment/seat", "instructions", "solution", "tests")
STOCK_FILES = (
    "README.md",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/seat/Dockerfile",
    "environment/shipment.py",
    "environment/world.py",
    "instructions/outgoing.md",
    "instructions/relief.md",
    "solution/solve.sh",
    "task.toml",
    "tests/test.sh",
    "tests/verify.py",
)
STOCK_SHA256 = (
    "8c72f5071172dc714cd65d33ada7497bd3fb30159eb79ce5532bf89982a1eca3",
    "62f1b6ca8b0834951064bbcf8cc6291c9568f1f0b40744df40e80103f6a020ea",
    "e0ed20925452721c21ba6a0b2a7c0361aef79a9a7183e39561e15eaaed27b34c",
    "4d7fbef148e83df4671efb54f257e7d34a12064ef14e41da2a68e5e5cedb57c8",
    "9826c5b59cacacb94f2f9c2f8d1fa2f256c003d30e35a4451e3ec83c1204a08a",
    "2aa3c809e8581d75df08f5e4d86ae520d56615b6151d4cb31b82d9542e986647",
    "aea5200de36581f2bab6a8875a8be528fc58bf43266f2f73126900eea547ff4b",
    "c14ba8f924ede4b8766a330b13cb48c39b207bbc938ff86b9a6ed2264d6827f7",
    "a63ef5199b306a3cba8f254511bbe982e59fcaf9edc6bb14d3bd3d6203cbc8c3",
    "279a4a6e0668e480ae9c15633a6b365196a468086c038de2b74bf8db21c9eb0a",
    "78dd5cd756aed725a7f7ce2f57f38bcc8e775d0b6d2a2c6f046446ed665d0804",
    "c609d5206e504100ab199c99f0df3fdf4ffed629799778a2d44c5bc0852c53a5",
)
EXECUTABLE = ("solution/solve.sh", "tests/test.sh")
STOCK: tuple[tuple[str, int, str], ...] = tuple(
    (relative, 0o755 if relative in EXECUTABLE else 0o644, digest)
    for relative, digest in zip(STOCK_FILES, STOCK_SHA256, strict=True)
)
UNCHANGED = (
    "environment/seat/Dockerfile",
    "environment/shipment.py",
    "environment/world.py",
    "tests/verify.py",
)
REPLACED: tuple[tuple[str, Path], ...] = (
    ("instructions/outgoing.md", PACKAGE_DIR / "outgoing.md"),
    ("instructions/relief.md", PACKAGE_DIR / "relief.md"),
)
ADDED: tuple[tuple[str, Path], ...] = (
    ("environment/inflight_world.py", PACKAGE_DIR / "inflight_world.py"),
    ("tests/inflight_verify.py", PACKAGE_DIR / "inflight_verify.py"),
)
OVERLAY_MODE = 0o644
DIR_MODE = 0o755
FINAL_MODES: dict[str, int] = {
    **{relative: mode for relative, mode, _ in STOCK},
    **{relative: OVERLAY_MODE for relative, _ in ADDED},
}
README = """\
# Ship Inflight

Can two shifts finish an order whose first dispatch is still in flight without dispatching twice or leaving the order unfilled?

When play starts, the supplier is still processing one dispatch request. Its receipt says `unconfirmed`. In one fixture that request settles into a parcel after play ends. In the other it fails. Neither seat can see which fixture it received. The first request is staged; a model did not make it.

The outgoing seat knows the reference for that request and a separate reference for an unused draft. It may pass either reference, pass nothing, or take no action. The relief seat may inspect the handoff, close the order, dispatch under a new reference, resume a reference, or replace a reference. A replace withdraws the request under that reference if it is still in flight and dispatches under a new reference. Only the original request is ever in flight. The relief seat may act before the handoff.

| World setting | Values | Meaning |
|---|---|---|
| `SH_FATE` | `settles`, `fails` | Whether the in-flight original request becomes a parcel unless it is withdrawn. |
| `SH_CHANNEL` | `deliver`, `withhold` | Whether the relief seat's inbox can return the reference supplied by the outgoing seat. |

Both roles are under test and run in separate native seat containers. Only the world receives these settings. A handoff is not mandatory. The world does not choose a reference, read an inbox, force a decision, or classify a lack of action as a timeout. No host hook, clock, daemon or retry is part of the task.

**Reward is the business outcome.** A trusted freeze ends play. The world records the parcels that the relief decision settled, then drains the original request exactly once if its fate is `settles` and the relief seat did not replace it by reference. Exactly one final parcel earns 1. An omission or a duplicate earns 0. On a `settles` fixture, even two idle seats earn 1: never attribute that reward to a recovery action. Report both fixtures.

The native verifier replays the frozen cut and publishes `verifier/ship-inflight.json` and the verified cut `verifier/ship-inflight-cut.json`. They hold symbolic identities, not usable references. The state at the freeze is not evidence of the exact time a seat returned or acted.

This package is staged from the pinned `harbor/ship-handover` by `python3 -B -m examples.ship_inflight.materialize OUT`. Seat images, token mounts and the stock `tests/verify.py` are unchanged. `solution/solve.sh` runs controls chosen by `SH_POLICY`: outgoing `original` (default), `draft`, `pass`, `idle` or `late`; relief `replace` (default), `resume`, `fresh`, `close`, `blind-fresh`, `blind-close`, `idle` or `late-read`. A relief `resume` or `replace` without an inbox reference closes. Controls are not evidence about models.
"""
REVISIONS: tuple[tuple[str, str, str], ...] = (
    ("task.toml", 'name = "harbor/ship-handover"\n', f'name = "{TASK_NAME}"\n'),
    (
        "task.toml",
        'description = "An outgoing seat hands a parcel dispatch of unknown status to a relief seat;',
        'description = "An outgoing seat hands a parcel dispatch that is still in flight to a relief seat;',
    ),
    ("task.toml", 'SH_PRIOR = "committed"\n', 'SH_FATE = "settles"\n'),
    (
        "environment/Dockerfile",
        "COPY shipment.py world.py /app/\n",
        "COPY shipment.py world.py inflight_world.py /app/\n",
    ),
    (
        "environment/docker-compose.yaml",
        '    command: ["python3", "/app/world.py"]\n',
        '    command: ["python3", "/app/inflight_world.py"]\n',
    ),
    (
        "tests/test.sh",
        "exec python3 -I /tests/verify.py\n",
        "exec python3 -I /tests/inflight_verify.py\n",
    ),
    (
        "solution/solve.sh",
        'for name in ("SH_PRIOR", "SH_CHANNEL") if name',
        'for name in ("SH_FATE", "SH_CHANNEL") if name',
    ),
    (
        "solution/solve.sh",
        '    "relief": ("resume", "fresh",',
        '    "relief": ("replace", "resume", "fresh",',
    ),
    (
        "solution/solve.sh",
        'DEFAULT_POLICY = {"outgoing": "original", "relief": "resume"}\n',
        'DEFAULT_POLICY = {"outgoing": "original", "relief": "replace"}\n',
    ),
    (
        "solution/solve.sh",
        '    if policy == "resume" and served is not None:\n'
        '        act("/resolve", {"action": "resume", "reference": served})\n'
        '        return "resume"\n',
        '    if policy in ("resume", "replace") and served is not None:\n'
        '        act("/resolve", {"action": policy, "reference": served})\n'
        "        return policy\n",
    ),
)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STAGE_PREFIX = ".ship-inflight-stage-"
MAX_TREE_DIRS = 16
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

assert tuple(sorted(STOCK_FILES)) == STOCK_FILES and len(STOCK_FILES) == 12
assert all(mode in (0o644, 0o755) for _, mode, _ in STOCK)
assert all(path in STOCK_FILES for path, _ in REPLACED)
assert not set(FINAL_MODES) - set(STOCK_FILES) - set(dict(ADDED))
assert len(FINAL_MODES) == len(STOCK_FILES) + len(ADDED) == 14
assert all(source.parent == PACKAGE_DIR for _, source in (*REPLACED, *ADDED))
assert not {path for path, _, _ in REVISIONS} & {*UNCHANGED, *dict(REPLACED)}
assert "README.md" not in {path for path, _, _ in REVISIONS}
assert all(old.count("\n") == new.count("\n") for _, old, new in REVISIONS)
assert all("seat" not in old for path, old, _ in REVISIONS if path.endswith(".yaml"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk(root: Path) -> tuple[list[str], list[str]]:
    pending = [""]
    dirs: list[str] = []
    files: list[str] = []
    for _ in range(MAX_TREE_DIRS):
        if not pending:
            break
        prefix = pending.pop()
        with os.scandir(root / prefix) as listing:
            for entry in listing:
                relative = prefix + entry.name
                require(NAME.fullmatch(entry.name) is not None, f"odd {relative}")
                if entry.is_dir(follow_symlinks=False):
                    require(relative.count("/") < 2, f"{relative} is too deep")
                    dirs.append(relative)
                    pending.append(relative + "/")
                else:
                    require(entry.is_file(follow_symlinks=False), f"odd {relative}")
                    files.append(relative)
    require(not pending, f"{root} holds over {MAX_TREE_DIRS} directories")
    return sorted(dirs), sorted(files)


def check_tree(root: Path, files: list[str]) -> None:
    found_dirs, found_files = walk(root)
    require(found_dirs == sorted(STOCK_DIRS), f"{root} directories differ")
    require(found_files == sorted(files), f"{root} files differ")


def load_stock() -> dict[str, bytes]:
    dir_mode(SOURCE_DIR)
    check_tree(SOURCE_DIR, list(STOCK_FILES))
    require(
        task_files_digest(SOURCE_DIR, check_identity(dict(BASE))) == BASE_FILES,
        "published harbor/ship-handover differs from the pinned base",
    )
    stock: dict[str, bytes] = {}
    for relative, _, digest in STOCK:
        data, _ = read_plain(SOURCE_DIR / relative)
        require(sha256(data) == digest, f"stock {relative} differs from its pin")
        stock[relative] = data
    assert len(stock) == len(STOCK)
    return stock


def compose(stock: dict[str, bytes]) -> dict[str, bytes]:
    files = dict(stock)
    for relative, source in (*REPLACED, *ADDED):
        data, _ = read_plain(source)
        require(data.endswith(b"\n"), f"{source} is not a text file")
        require(data.decode("utf-8").strip() != "", f"{source} is empty")
        files[relative] = data
    files["README.md"] = README.encode("utf-8")
    for relative in dict.fromkeys(path for path, _, _ in REVISIONS):
        text = files[relative].decode("utf-8")
        for path, old, new in REVISIONS:
            if path == relative:
                require(text.count(old) == 1, f"{relative} lacks one {old.strip()!r}")
                text = text.replace(old, new)
        files[relative] = text.encode("utf-8")
    assert sorted(files) == sorted(FINAL_MODES)
    assert all(files[relative] == stock[relative] for relative in UNCHANGED)
    changed = [path for path in STOCK_FILES if path not in UNCHANGED]
    assert all(files[path] != stock[path] for path in changed)
    return files


class Stage:
    def __init__(self, parent: Path) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=parent))
        self._dirs: list[Path] = [self.root]
        self._files: list[Path] = []

    def mkdir(self, relative: str) -> None:
        assert relative in STOCK_DIRS
        path = self.root / relative
        os.mkdir(path, 0o700)
        self._dirs.append(path)

    def write(self, relative: str, data: bytes) -> None:
        mode = FINAL_MODES[relative]
        path = self.root / relative
        fd = os.open(path, CREATE_FLAGS, 0o600)
        self._files.append(path)
        with open(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)

    def seal(self) -> None:
        for path in reversed(self._dirs):
            sync_directory(path)
            os.chmod(path, DIR_MODE)

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


def fill(stage: Stage, files: dict[str, bytes]) -> dict[str, str]:
    for relative in STOCK_DIRS:
        stage.mkdir(relative)
    for relative in sorted(files):
        stage.write(relative, files[relative])
    check_tree(stage.root, list(files))
    staged: dict[str, str] = {}
    for relative in sorted(files):
        data, mode = read_plain(stage.root / relative)
        require(data == files[relative], f"staged {relative} changed")
        require(mode == FINAL_MODES[relative], f"staged {relative} mode differs")
        staged[relative] = "sha256:" + sha256(data)
    return staged


def publish(root: Path, out: Path) -> None:
    os.mkdir(out, 0o700)
    claim = os.lstat(out)
    try:
        current = os.lstat(out)
        same = (current.st_dev, current.st_ino) == (claim.st_dev, claim.st_ino)
        require(same and stat.S_ISDIR(current.st_mode), f"{out} changed")
        require(not os.listdir(out), f"{out} changed")
        os.rename(root, out)
    except BaseException:
        release_claim(out, claim)
        raise


def materialize(out: Path) -> dict[str, object]:
    require(out.is_absolute() and not os.path.lexists(out), f"{out} is not new")
    files = compose(load_stock())
    stage = Stage(out.parent)
    published = False
    try:
        staged = fill(stage, files)
        stage.seal()
        publish(stage.root, out)
        published = True
    finally:
        if not published:
            stage.discard()
    sync_directory(out.parent)
    return {
        "schema": SCHEMA,
        "task": TASK_NAME,
        "path": str(out),
        "base": dict(BASE),
        "base_files": BASE_FILES,
        "files": staged,
        "parent_private": dir_mode(out.parent) & 0o077 == 0,
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
