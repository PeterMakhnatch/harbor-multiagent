import argparse
import hashlib
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..native_study import (
    native,
    record_bytes,
    require,
    task_files_digest,
    trial_request,
    write_exclusive,
)
from ..seat_handoffs.native_records import read_record_with_digest
from ..ship_inflight.materialize import BASE, BASE_FILES
from .study import (
    MAX_REGISTRATION_BYTES,
    TASK_DIR,
    validate_registration,
    world_env,
)

REPO = Path(__file__).resolve().parents[2]
TASK_ROOT = "examples/tasks/ship-handover"
REGISTRATIONS = REPO / "examples" / "ship_handover" / "samples" / "two-worlds"
RECOVERY = "stock-recovery.json"
SENDER_ONLY = "stock-sender-only.json"
PINS = {
    RECOVERY: "3f510417686868e18113c6c7e2eaeea4368aaf568d02961b241061420aaf18d9",
    SENDER_ONLY: "588cb320bd3b8a91a11d0bf9e4f64ddc258805cae32dc7ab1b44794ab0004b5b",
}
ARMS = {
    "recovery-deliver.json": (RECOVERY, "sh-7bb5c021c6bf483e", "deliver"),
    "sender-only-deliver.json": (SENDER_ONLY, "sh-d4d4898bd68bd519", "deliver"),
    "recovery-withhold.json": (RECOVERY, "sh-38d4cd13d3cd3f50", "withhold"),
}
PRIOR = "committed"
LOCK_MODE = 0o444

OUT_EXISTS = "output must be a new directory under an existing parent"
HARBOR_MISSING = "arm inputs need the installed Harbor fork"
UNPINNED = "registrations or stock package differ from the released pins"
LOCK_FAILED = "native locks could not be built from the pinned inputs"
WRITE_FAILED = "arm locks were not written; this run's partial files were removed"

assert TASK_DIR == REPO / TASK_ROOT
assert {registration for registration, _, _ in ARMS.values()} == set(PINS)


def _target(out: str | os.PathLike[str]) -> Path:
    path = Path(out).absolute()
    try:
        require(path.name not in ("", ".", ".."), OUT_EXISTS)
        target = path.parent.resolve(strict=True) / path.name
    except OSError:
        raise ValueError(OUT_EXISTS) from None
    require(not os.path.lexists(target), OUT_EXISTS)
    return target


def _registration(name: str) -> dict[str, Any]:
    try:
        found = read_record_with_digest(REGISTRATIONS / name, MAX_REGISTRATION_BYTES)
        if found is None or found[1] != PINS[name]:
            raise ValueError(UNPINNED)
        registration = validate_registration(found[0])
    except (ValueError, OSError):
        raise ValueError(UNPINNED) from None
    require(registration["task"] == BASE, UNPINNED)
    return registration


def _require_stock() -> None:
    try:
        files = task_files_digest(TASK_DIR, dict(BASE))
    except (ValueError, OSError):
        raise ValueError(UNPINNED) from None
    require(files == BASE_FILES, UNPINNED)


def _lock(
    harbor: Any, registration: dict[str, Any], ticket: str, channel: str
) -> dict[str, Any]:
    cells = [cell for cell in registration["cells"] if cell["ticket"] == ticket]
    require(len(cells) == 1, UNPINNED)
    cell = cells[0]
    require(cell["prior"] == PRIOR and cell["channel"] == channel, UNPINNED)
    team = registration["teams"][cell["team"]]
    try:
        request = trial_request(
            harbor, TASK_DIR, REPO / "trials", ticket, team, world_env(cell)
        )
        config = harbor.TrialConfig.model_validate(
            request | {"task": {"path": TASK_ROOT}}
        )
        local = SimpleNamespace(
            path=TASK_DIR, content_hash=None, resolved_git_commit_id=None
        )
        lock = harbor.build_trial_lock(trial_config=config, task_download_result=local)
    except (ValueError, OSError):
        raise ValueError(LOCK_FAILED) from None
    require(lock.task.digest == BASE["digest"], UNPINNED)
    return lock.model_dump(mode="json", exclude_none=True)


def _payloads() -> dict[str, dict[str, Any]]:
    try:
        harbor = native()
        from harbor.models.job.lock import build_trial_lock
    except (ImportError, SystemExit):
        raise ValueError(HARBOR_MISSING) from None
    harbor.build_trial_lock = build_trial_lock
    registrations = {name: _registration(name) for name in PINS}
    _require_stock()
    payloads = {
        name: _lock(harbor, registrations[registration], ticket, channel)
        for name, (registration, ticket, channel) in ARMS.items()
    }
    _require_stock()
    return payloads


def _publish(target: Path, payloads: dict[str, dict[str, Any]]) -> None:
    try:
        os.mkdir(target)
    except OSError:
        raise ValueError(OUT_EXISTS) from None
    written: list[Path] = []
    try:
        for name, payload in payloads.items():
            write_exclusive(target / name, payload, LOCK_MODE)
            written.append(target / name)
    except (OSError, ValueError):
        raise ValueError(WRITE_FAILED) from None
    finally:
        if len(written) != len(payloads):
            for path in written:
                path.unlink(missing_ok=True)
            with suppress(OSError):
                target.rmdir()


def prepare(out: str | os.PathLike[str]) -> dict[str, dict[str, str]]:
    target = _target(out)
    payloads = _payloads()
    _publish(target, payloads)
    digests = {name: hashlib.sha256(record_bytes(p)) for name, p in payloads.items()}
    return {
        name: {"task_root": TASK_ROOT, "sha256": digest.hexdigest()}
        for name, digest in digests.items()
    }


def main(argv: list[str] | None = None) -> int:
    root = argparse.ArgumentParser(
        prog="python -m examples.ship_handover.arm_inputs",
        description=f"Write native TrialLock v2 files {', '.join(ARMS)}.",
    )
    root.add_argument("out", help="new directory for the 3 locks; never overwritten")
    args = root.parse_args(argv)
    try:
        prepared = prepare(args.out)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    public = {name: {"task_root": TASK_ROOT} for name in prepared}
    print(json.dumps(public, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
