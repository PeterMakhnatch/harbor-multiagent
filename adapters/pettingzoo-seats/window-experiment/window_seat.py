"""Program seat whose fresh executable receives a bounded private actor window.

The policy input is ``seatlab.windows.actor_window(current, history, depth)``: the raw
current view plus this seat's own last ``depth`` raw views and the actions it submitted.
It carries no world step, clock, seed, trial ID, or other role's record.

Each successful executable call appends one digest-only frame to a NEW private
``window_frames.jsonl`` in the host ``logs_dir``. A frame records what this wrapper sent
and received, not world acceptance; the world episode tape remains the authority.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, override

_ADAPTER = str(Path(__file__).resolve().parent.parent)
if _ADAPTER not in sys.path:
    sys.path.insert(0, _ADAPTER)

from program_seat import ProgramSeat, canonical_bytes  # noqa: E402
from seatlab.tape import canonical, digest  # noqa: E402
from seatlab.windows import actor_window  # noqa: E402

FRAMES = "window_frames.jsonl"
WINDOW_SCHEMA = "seatlab.actor-window/v1"
MAX_DEPTH = 16
_HISTORY_ITEM = frozenset({"view", "action"})


def check_depth(depth: object) -> int:
    """Reject anything but a real int in 0..16; ``bool`` is not a depth."""
    if type(depth) is not int or not 0 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be an int from 0 to {MAX_DEPTH}")
    return depth


def _open_new(path: Path) -> int:
    """Create the receipt file exclusively; never truncate or reuse one."""
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o644)
    except FileExistsError:
        raise ValueError(
            f"{FRAMES} already exists; refusing to reuse a receipt"
        ) from None


def _write_all(fd: int, data: bytes) -> None:
    chunk = memoryview(data)
    while chunk:
        chunk = chunk[os.write(fd, chunk) :]
    os.fsync(fd)


def _window_item(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _HISTORY_ITEM:
        raise ValueError(f"{label} must contain only view and action")
    view, action = value["view"], value["action"]
    if type(view) is not dict or type(action) is not int:
        raise ValueError(f"{label} must hold an object view and an int action")
    if view.get("schema") == WINDOW_SCHEMA:
        raise ValueError(f"{label} must not nest a policy envelope")
    return {"view": view, "action": action}


class WindowProgramSeat(ProgramSeat):
    """One fresh process per decision; input is this seat's own bounded actor window."""

    @staticmethod
    @override
    def name() -> str:
        return "window-program-seat"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        depth: int,
        **kwargs: Any,
    ) -> None:
        self._depth = check_depth(depth)
        self._history: list[dict[str, Any]] = []
        self._turn = 0
        super().__init__(logs_dir, model_name, **kwargs)
        Path(self.logs_dir).mkdir(parents=True, exist_ok=True)
        self._frames = _open_new(Path(self.logs_dir) / FRAMES)

    @classmethod
    @override
    def preflight(cls, kwargs: dict[str, Any] | None = None, env: Any = None) -> None:
        options = dict(kwargs or {})
        check_depth(options.get("depth"))
        super().preflight(options, env)

    def _envelope(self, view: dict[str, Any], current_sha: str) -> dict[str, Any]:
        history = self._history[-self._depth :] if self._depth else []
        sent = [{"view": item["view"], "action": item["action"]} for item in history]
        window = actor_window(current=view, history=sent, depth=self._depth)
        if type(window) is not dict or set(window) != {"schema", "current", "history"}:
            raise ValueError("actor_window returned a non-canonical envelope")
        if window["schema"] != WINDOW_SCHEMA:
            raise ValueError("actor_window returned the wrong schema")
        if digest(window["current"]) != current_sha:
            raise ValueError("actor_window changed or mutated the current view")
        returned = window["history"]
        if type(returned) is not list or len(returned) > self._depth:
            raise ValueError("actor_window returned too much history")
        checked = [_window_item(item, "history item") for item in returned]
        if [digest(item) for item in checked] != [digest(item) for item in sent]:
            raise ValueError("actor_window did not return the supplied history tail")
        return {
            "schema": WINDOW_SCHEMA,
            "current": window["current"],
            "history": checked,
        }

    def _program(self, view: dict[str, Any]) -> int:
        # Hash the raw view before any library call, then check the exact bytes sent.
        view_sha = digest(view)
        window = self._envelope(view, view_sha)
        payload = canonical(window)
        if payload != canonical_bytes(window):
            raise ValueError("actor window canonical bytes disagree")
        action = super()._program(window)
        turn = self._turn + 1
        frame = {
            "depth": self._depth,
            "turn": turn,
            "view_sha256": view_sha,
            "action_sha256": digest({"action": action}),
            "context_sha256": digest(window),
            "context_bytes": len(payload),
        }
        _write_all(self._frames, canonical(frame) + b"\n")
        self._turn = turn
        if self._depth:
            self._history.append({"view": view, "action": action})
            del self._history[: -self._depth]
        return action
