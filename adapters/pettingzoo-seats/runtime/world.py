"""File-mailbox world server. A joint step is committed only after every active seat answers."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from engine import World
from wire import canonical_digest, read_json, write_json_atomic

POLL = 0.05
READY = Path("/app/world.ready")
ACTION_KEYS = frozenset({"step", "view_sha256", "action"})


def _spec(spec: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(spec, (str, Path)):
        spec = read_json(Path(spec))
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    return spec


def _dirs(exchange: Path, roles: list[str]) -> None:
    for role in roles:
        for name in ("in", "out"):
            (exchange / role / name).mkdir(parents=True, exist_ok=True)
        for rel in ("in/request.json", "in/done.json", "out/action.json"):
            (exchange / role / rel).unlink(missing_ok=True)


def _action(path: Path) -> dict[str, Any] | None:
    try:
        msg = read_json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid action file: {exc}") from exc
    if not isinstance(msg, dict) or set(msg) != ACTION_KEYS:
        raise ValueError("action file must contain step, view_sha256, and action")
    return msg


def _legal(view: dict[str, Any], action: object) -> bool:
    legal = view.get("legal_actions")
    return (
        type(action) is int
        and isinstance(legal, list)
        and any(type(item) is int and item == action for item in legal)
    )


def _collect(
    exchange: Path,
    pending: dict[str, dict[str, Any]],
    digests: dict[str, str],
    step: int,
    deadline: float,
) -> dict[str, int]:
    needed, found = set(pending), {}
    while needed:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting at step {step}")
        for role in list(needed):
            msg = _action(exchange / role / "out" / "action.json")
            if msg is None or (type(msg["step"]) is int and msg["step"] < step):
                continue
            if type(msg["step"]) is not int or msg["step"] != step:
                raise ValueError(f"{role} sent step {msg['step']!r}, expected {step}")
            if msg["view_sha256"] != digests[role]:
                raise ValueError(f"{role} view digest mismatch at step {step}")
            if not _legal(pending[role], msg["action"]):
                raise ValueError(f"{role} action is not legal at step {step}")
            found[role] = msg["action"]
            needed.remove(role)
        if needed:
            time.sleep(POLL)
    return found


def _serve(
    world: World,
    spec: dict[str, Any],
    exchange: Path,
    result_path: Path,
    timeout: float,
) -> dict[str, Any]:
    if (
        isinstance(timeout, bool)
        or type(timeout) not in (int, float)
        or not timeout > 0
    ):
        raise ValueError("timeout must be a positive number")
    roles, deadline, step = list(spec["roles"]), time.monotonic() + float(timeout), 0
    _dirs(exchange, roles)
    while pending := world.pending():
        step += 1
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out before step {step}")
        digests = {role: canonical_digest(view) for role, view in pending.items()}
        for role, view in pending.items():
            write_json_atomic(
                exchange / role / "in" / "request.json",
                {"step": step, "view": view, "view_sha256": digests[role]},
            )
        for role in roles:
            if role not in pending:
                (exchange / role / "in" / "request.json").unlink(missing_ok=True)
        world.advance(_collect(exchange, pending, digests, step, deadline))
    result = world.finish()
    write_json_atomic(result_path, result)
    for role in roles:
        write_json_atomic(exchange / role / "in" / "done.json", {"done": True})
    return result


def run(
    spec: dict[str, Any] | str | Path,
    exchange: str | Path,
    result_path: str | Path,
    timeout: float,
) -> dict[str, Any]:
    """Drive one world to completion. Does not write the health file or stay alive."""
    loaded = _spec(spec)
    world = World(loaded)
    try:
        return _serve(world, loaded, Path(exchange), Path(result_path), timeout)
    finally:
        world.close()


def main() -> int:
    READY.unlink(missing_ok=True)
    world: World | None = None
    try:
        spec, exchange = _spec("/app/spec.json"), Path("/exchange")
        _dirs(exchange, list(spec["roles"]))
        world = World(spec)
        READY.parent.mkdir(parents=True, exist_ok=True)
        tmp = READY.with_name(".world.ready.tmp")
        tmp.write_text("ready\n", encoding="ascii")
        tmp.replace(READY)
        _serve(
            world,
            spec,
            exchange,
            Path("/logs/world/pettingzoo.json"),
            float(os.environ.get("TIMEOUT", "600")),
        )
    except Exception as exc:
        READY.unlink(missing_ok=True)
        print(exc, file=sys.stderr)
        return 1
    finally:
        if world is not None:
            world.close()
    while True:
        signal.pause()


if __name__ == "__main__":
    raise SystemExit(main())
