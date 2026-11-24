"""Stage the local `harbor/ship-decision-receipt` task from the pinned Ship Inflight files."""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import override

from ...native_study import DIGEST, as_record, as_text, check_identity, require
from ...seat_checkpoints.materialize import dir_mode, read_plain, sync_directory, target
from ...ship_inflight import materialize as inflight

SCHEMA = "harbor.ship-decision-receipt.materialized/v1"
TREE_SCHEMA = "harbor.ship-decision-receipt.tree/v1"
TASK_NAME = "harbor/ship-decision-receipt"
PACKAGE_DIR = Path(__file__).resolve().parent
CHECKOUT = PACKAGE_DIR.parents[2]
PIN_PATH = CHECKOUT / "examples" / "ship_inflight" / "samples" / "task-pin.json"
PIN_SHA256 = "f659c32e82742ccd399ef453ee0018fcba7459d54485bec077fc4de77f6fe4dc"
PIN_SCHEMA = "harbor.ship-inflight.source/v1"
PIN_FIELDS = frozenset({"schema", "question", "task", "task_files", "files"})
BASE: dict[str, str] = {
    "name": "harbor/ship-inflight",
    "digest": "sha256:9a712fac1b44e4b70c91cb717b9b2cb2cbc2a3a43198839cc4f75be38c1c713e",
    "checksum": "411796f5aee862b63b0c3a9c1a6a43149813422db5d715d8634ab865f09f8bda",
}
BASE_FILES = "sha256:cb5294288f62b79fe69c780c2e5287bfef0dbb7b05fb3e17b03f1ebbd4d0d94c"
BASE_MODES: dict[str, int] = dict(inflight.FINAL_MODES)
UNCHANGED = (
    "environment/inflight_world.py",
    "environment/seat/Dockerfile",
    "environment/shipment.py",
    "environment/world.py",
    "instructions/outgoing.md",
    "solution/solve.sh",
    "tests/inflight_verify.py",
    "tests/verify.py",
)
ADDED: tuple[tuple[str, Path], ...] = (
    ("environment/decision_world.py", PACKAGE_DIR / "decision_world.py"),
    ("tests/decision_verify.py", PACKAGE_DIR / "decision_verify.py"),
)
OVERLAY_MODE = 0o644
FINAL_MODES: dict[str, int] = {
    **BASE_MODES,
    **{relative: OVERLAY_MODE for relative, _ in ADDED},
}
CITE_SENTENCE = (
    " A `/resolve` body may also carry an optional `cite` field set to a `reference`"
    " that `GET /inbox` already returned to you; it is accepted with any action and"
    " does not change the score."
)
README = """\
# Ship Decision Receipt

This is the local task `harbor/ship-decision-receipt`. Its former base is the pinned `harbor/ship-inflight`. The desk, the two fixtures (`SH_FATE`, `SH_CHANNEL`), the seats, the outgoing instruction, the controls and the reward are the same as in the base.

The only new choice belongs to the relief seat. Its `POST /resolve` body may carry an optional `cite`: the exact `reference` that its own `GET /inbox` returned before that request. The world accepts a citation with any decision, including `close` and `fresh`. It checks and records the citation in the same step that commits the decision. A request whose citation the inbox did not return is rejected and changes nothing, so the relief seat can still decide. Without `cite`, `/resolve` behaves as in the base.

The frozen cut records `cited_at_decision`: `original`, `draft` or `null`, never a usable reference. It tells which returned reference the relief seat named when it committed. It is not evidence that the seat read the handoff before it decided, that it acknowledged the note, or that the reference shaped the decision. `null` is not evidence that the seat ignored the note.

**Reward is the business outcome, unchanged.** Exactly one final parcel earns 1. An omission or a duplicate earns 0. The grade never reads the citation. On a `settles` fixture, even two idle seats earn 1. Report both fixtures.

The evidence path differs from the base. The native verifier runs `tests/decision_verify.py` beside the stock `tests/verify.py` and `tests/inflight_verify.py`. It publishes `verifier/ship-decision-receipt.json` and the verified cut `verifier/ship-decision-receipt-cut.json` with schema `harbor.ship-decision-receipt.cut/v1`, not `verifier/ship-inflight.json`. It refuses `harbor.ship-inflight.cut/v1` cuts. The 16 frozen `harbor/ship-inflight` scripted cuts have no citation field and are not evidence about citations.

This package is staged by `python3.12 -BS -m examples.seatlab.decision_receipt.materialize OUT`. The main image adds `decision_world.py`, and the main Compose command runs it. `tests/test.sh` runs `tests/decision_verify.py`. The relief instruction gains one sentence about `cite`. Seat images, token mounts, the outgoing instruction, `world.py`, `shipment.py`, `inflight_world.py`, both base verifiers and `solution/solve.sh` are unchanged. `solution/solve.sh` keeps the base controls chosen by `SH_POLICY` and never sends a citation. Controls are not evidence about models.
"""
REVISIONS: tuple[tuple[str, str, str], ...] = (
    ("task.toml", f'name = "{BASE["name"]}"\n', f'name = "{TASK_NAME}"\n'),
    (
        "environment/Dockerfile",
        "COPY shipment.py world.py inflight_world.py /app/\n",
        "COPY shipment.py world.py inflight_world.py decision_world.py /app/\n",
    ),
    (
        "environment/docker-compose.yaml",
        '    command: ["python3", "/app/inflight_world.py"]\n',
        '    command: ["python3", "/app/decision_world.py"]\n',
    ),
    (
        "tests/test.sh",
        "exec python3 -I /tests/inflight_verify.py\n",
        "exec python3 -I /tests/decision_verify.py\n",
    ),
    (
        "instructions/relief.md",
        " You can send it only once.\n",
        " You can send it only once." + CITE_SENTENCE + "\n",
    ),
)

assert len(BASE_MODES) == 14 and BASE_MODES is not inflight.FINAL_MODES
assert len(FINAL_MODES) == len(BASE_MODES) + len(ADDED) == 16
assert not set(dict(ADDED)) & set(BASE_MODES)
assert all(source.parent == PACKAGE_DIR for _, source in ADDED)
assert all(path in BASE_MODES for path in UNCHANGED)
assert not {path for path, _, _ in REVISIONS} & {*UNCHANGED, "README.md"}
assert all(old.count("\n") == new.count("\n") for _, old, new in REVISIONS)
assert all("seat" not in old for path, old, _ in REVISIONS if path.endswith(".yaml"))
assert PACKAGE_DIR.parent.parent == CHECKOUT / "examples"


def manifest_digest(task: dict[str, str], files: dict[str, bytes]) -> str:
    identity = check_identity(dict(task))
    header = f"{identity['name']}\0{identity['digest']}\0{identity['checksum']}\n"
    outer = hashlib.sha256(header.encode("ascii"))
    for relative in sorted(files):
        line = f"{relative}\0{inflight.sha256(files[relative])}\n"
        outer.update(line.encode("ascii"))
    return as_text("sha256:" + outer.hexdigest(), "manifest digest", DIGEST)


def tree_digest(modes: dict[str, int], digests: dict[str, str]) -> str:
    require(sorted(modes) == sorted(digests), "tree inventory differs")
    outer = hashlib.sha256(f"{TREE_SCHEMA}\n".encode("ascii"))
    for relative in sorted(digests):
        line = f"{relative}\0{modes[relative]:04o}\0{digests[relative]}\n"
        outer.update(line.encode("ascii"))
    return "sha256:" + outer.hexdigest()


def load_base() -> dict[str, bytes]:
    raw, _ = read_plain(PIN_PATH)
    require(inflight.sha256(raw) == PIN_SHA256, "ship-inflight task pin differs")
    pin = as_record(json.loads(raw.decode("utf-8")), "task pin", PIN_FIELDS)
    require(pin["schema"] == PIN_SCHEMA, "ship-inflight task pin schema differs")
    require(check_identity(pin["task"]) == BASE, "pinned task identity differs")
    require(pin["task_files"] == BASE_FILES, "pinned task_files differs")
    files = inflight.compose(inflight.load_stock())
    require(sorted(files) == sorted(BASE_MODES), "ship-inflight inventory differs")
    pinned = as_record(pin["files"], "task pin files")
    found = {path: "sha256:" + inflight.sha256(data) for path, data in files.items()}
    require(pinned == found, "composed ship-inflight files differ from the task pin")
    require(
        manifest_digest(BASE, files) == BASE_FILES, "ship-inflight manifest differs"
    )
    return files


def compose(base: dict[str, bytes]) -> dict[str, bytes]:
    files = dict(base)
    for relative, source in ADDED:
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
    assert all(files[path] == base[path] for path in UNCHANGED)
    changed = [path for path in BASE_MODES if path not in UNCHANGED]
    assert all(files[path] != base[path] for path in changed)
    return files


class Stage(inflight.Stage):
    @override
    def write(self, relative: str, data: bytes) -> None:
        mode = FINAL_MODES[relative]
        path = self.root / relative
        fd = os.open(path, inflight.CREATE_FLAGS, 0o600)
        self._files.append(path)
        with open(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)


def fill(stage: Stage, files: dict[str, bytes]) -> dict[str, str]:
    for relative in inflight.STOCK_DIRS:
        stage.mkdir(relative)
    for relative in sorted(files):
        stage.write(relative, files[relative])
    inflight.check_tree(stage.root, list(files))
    staged: dict[str, str] = {}
    for relative in sorted(files):
        data, mode = read_plain(stage.root / relative)
        require(data == files[relative], f"staged {relative} changed")
        require(mode == FINAL_MODES[relative], f"staged {relative} mode differs")
        staged[relative] = "sha256:" + inflight.sha256(data)
    return staged


def materialize(out: Path) -> dict[str, object]:
    require(out.is_absolute() and not os.path.lexists(out), f"{out} is not new")
    require(
        not out.parent.resolve(strict=True).is_relative_to(CHECKOUT),
        "OUT must lie outside the checkout",
    )
    files = compose(load_base())
    stage = Stage(out.parent)
    published = False
    try:
        staged = fill(stage, files)
        stage.seal()
        inflight.publish(stage.root, out)
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
        "base_pin_sha256": PIN_SHA256,
        "stock": dict(inflight.BASE),
        "stock_files": inflight.BASE_FILES,
        "files": {
            relative: {"mode": f"{FINAL_MODES[relative]:04o}", "sha256": digest}
            for relative, digest in staged.items()
        },
        "tree": tree_digest(FINAL_MODES, staged),
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
