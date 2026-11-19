"""Fixed seat helper. Reads this seat's mailbox and commits one checked action."""

from __future__ import annotations
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

INBOX = Path("/in")
ACTION_PATH = Path("/out/action.json")
AUDIT_PATH = Path("/logs/agent/pz_actions.jsonl")
COMMIT_PATH = Path("/tmp/pz-program-seat/commit.json")
REQUEST_KEYS = frozenset({"step", "view", "view_sha256"})
COMMIT_KEYS = frozenset({"step", "view_sha256", "action"})


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _emit(value: object) -> None:
    sys.stdout.buffer.write(_canonical(value) + b"\n")


def _map(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise SystemExit(2)
    mapped: dict[str, Any] = {}
    for key, item in cast(dict[Any, Any], value).items():
        if type(key) is not str:
            raise SystemExit(2)
        mapped[key] = item
    return mapped


def _request() -> dict[str, Any] | None:
    path = INBOX / "request.json"
    if not path.is_file():
        return None
    msg = _map(_read(path))
    step, view, digest = msg.get("step"), msg.get("view"), msg.get("view_sha256")
    if (
        set(msg) != REQUEST_KEYS
        or type(step) is not int
        or not isinstance(view, dict)
        or type(digest) is not str
    ):
        raise SystemExit(2)
    view_map = _map(view)
    if _digest(view_map) != digest:
        raise SystemExit(2)
    return {"step": step, "view": view_map, "view_sha256": digest}


def next_frame(after: int) -> None:
    if (INBOX / "done.json").is_file():
        _emit({"done": True})
        return
    msg = _request()
    if msg is None or msg["step"] <= after:
        raise SystemExit(3)
    _emit({"step": msg["step"], "view": msg["view"], "view_sha256": msg["view_sha256"]})


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        tmp.replace(path)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def commit() -> None:
    if (INBOX / "done.json").is_file():
        raise SystemExit(2)
    current = _request()
    if current is None:
        raise SystemExit(2)
    payload = _map(_read(COMMIT_PATH))
    action = payload.get("action")
    view = current.get("view")
    legal = _map(view).get("legal_actions") if type(view) is dict else None
    if (
        set(payload) != COMMIT_KEYS
        or payload.get("step") != current.get("step")
        or payload.get("view_sha256") != current.get("view_sha256")
        or type(action) is not int
        or not isinstance(legal, list)
        or not any(item == action for item in legal)
    ):
        raise SystemExit(2)
    record = {
        "step": current["step"],
        "view_sha256": current["view_sha256"],
        "action": action,
    }
    _atomic(ACTION_PATH, _canonical(record) + b"\n")
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("ab") as handle:
        handle.write(_canonical(record) + b"\n")
        handle.flush()


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "next" and sys.argv[2].isdigit():
        next_frame(int(sys.argv[2]))
        return
    if sys.argv[1:] == ["commit"]:
        commit()
        return
    raise SystemExit(2)


if __name__ == "__main__":
    main()
