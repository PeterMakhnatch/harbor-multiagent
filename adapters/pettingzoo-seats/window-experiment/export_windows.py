"""Export strict context receipts for one completed window native trial.

Reads the exact native result ID, the enrolled participants, the validated role metadata,
the per-seat actor audit, the world-accepted decisions, and each seat's private
``window_frames.jsonl``. Emits one ``seatlab.window-receipt/v1`` line per role.

Does not import Harbor or PettingZoo. A receipt states what the wrapper submitted to its
executable; the world episode tape remains the authority for world acceptance.
"""

from __future__ import annotations

import argparse
import errno
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from seatlab.tape import TapeError, canonical, decode, digest, write_lines
from seatlab.windows import actor_window

RECEIPT_SCHEMA = "seatlab.window-receipt/v1"
WINDOW_SCHEMA = "seatlab.actor-window/v1"
SEAT_IMPORT = "window_seat:WindowProgramSeat"
SEAT_NAME = "window-program-seat"
MAX_DEPTH = 16
MAX_LINE_BYTES = 4 * 1024 * 1024

ROLE = re.compile(r"p[0-9]")
DOC_FIELDS = frozenset(
    {"schema", "case_sha256", "roles", "steps", "returns", "terminated", "truncated"}
)
STEP_FIELDS = frozenset({"step", "decisions"})
DECISION_FIELDS = frozenset({"view", "view_sha256", "action"})
AUDIT_FIELDS = frozenset({"step", "view_sha256", "action"})
META_FIELDS = frozenset({"schema", "role", "program_calls", "stage"})
FRAME_FIELDS = frozenset(
    {"depth", "turn", "view_sha256", "action_sha256", "context_sha256", "context_bytes"}
)


def _fail(msg: str) -> Any:
    raise ValueError(msg)


def _obj(data: bytes, where: str) -> dict[str, Any]:
    try:
        value = decode(data)
    except TapeError as exc:
        raise ValueError(f"{where}: invalid JSON: {exc}") from exc
    return (
        cast(dict[str, Any], value)
        if isinstance(value, dict)
        else _fail(f"{where}: expected an object")
    )


def _uuid(value: object, where: str) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            parsed = UUID(value)
            if str(parsed) == value and parsed.int != 0:
                return value
        except ValueError:
            pass
    return _fail(f"{where}: expected a canonical nonzero UUID")


def _num(value: object, where: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        return _fail(f"{where}: expected a finite number")
    return value


def _read(root: Path, rel: Path) -> bytes:
    root = root.resolve()
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            _fail(f"symlink is not allowed: {rel.as_posix()}")
    if not cur.is_file() or cur.resolve() != root / rel:
        _fail(f"missing required file {rel.as_posix()}")
    return cur.read_bytes()


def _lines(data: bytes, where: str) -> list[bytes]:
    if b"\r" in data:
        _fail(f"{where}: carriage returns are not allowed")
    if not data.endswith(b"\n"):
        _fail(f"{where}: must end with one newline")
    out: list[bytes] = []
    for number, line in enumerate(data.split(b"\n")[:-1], 1):
        if not line:
            _fail(f"{where}:{number}: blank line")
        if len(line) > MAX_LINE_BYTES:
            _fail(f"{where}:{number}: line exceeds the byte limit")
        out.append(line)
    return out


def _strict_line(line: bytes, where: str) -> dict[str, Any]:
    row = _obj(line, where)
    if canonical(row) != line:
        _fail(f"{where}: line is not canonical JSON")
    return row


def _check_result(trial: Path) -> tuple[dict[str, Any], str]:
    res = _obj(_read(trial, Path("result.json")), "result.json")
    tid = _uuid(res.get("id"), "result.json id")
    if res.get("exception_info") is not None or res.get("reward_valid") is not True:
        _fail("result.json has an exception or reward_valid is not true")
    if not isinstance(res.get("finished_at"), str) or not res["finished_at"]:
        _fail("result.json missing finished_at")
    rewards = (res.get("verifier_result") or {}).get("rewards")
    score = (
        rewards.get("reward")
        if isinstance(rewards, dict) and set(rewards) == {"reward"}
        else None
    )
    if _num(score, "completion") not in (1, 1.0):
        _fail("protocol completion score must be the sole reward and equal 1")
    txt = float(_read(trial, Path("verifier/reward.txt")).decode().strip())
    if not math.isclose(txt, 1.0, rel_tol=0, abs_tol=1e-12):
        _fail("verifier/reward.txt disagrees with completion score 1")
    return res, tid


def _depth(value: object, where: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_DEPTH:
        _fail(f"{where}: depth must be an int from 0 to {MAX_DEPTH}")
    return value


def _participants(document: dict[str, Any], where: str) -> dict[str, tuple[str, int]]:
    config = document.get("config")
    if config is None:
        config = document
    if not isinstance(config, dict):
        return _fail(f"{where}: config must be an object")
    raw = config.get("participants")
    if not isinstance(raw, list) or not raw:
        return _fail(f"{where}: participants must be a nonempty array")
    found: dict[str, tuple[str, int]] = {}
    for index, entry in enumerate(raw):
        label = f"{where} participant {index}"
        if not isinstance(entry, dict):
            _fail(f"{label}: expected an object")
        seat = entry.get("seat")
        if not isinstance(seat, str) or ROLE.fullmatch(seat) is None:
            _fail(f"{label}: invalid seat")
        if seat in found:
            _fail(f"{label}: duplicate seat {seat}")
        if entry.get("import_path") != SEAT_IMPORT:
            _fail(f"{label}: must enroll {SEAT_IMPORT}")
        if entry.get("model_name") is not None:
            _fail(f"{label}: model_name must be null")
        kwargs = entry.get("kwargs")
        if not isinstance(kwargs, dict):
            _fail(f"{label}: kwargs must be an object")
        argv = kwargs.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(part, str) or not part for part in argv)
        ):
            _fail(f"{label}: kwargs.argv must be a nonempty list of strings")
        found[seat] = (
            SEAT_IMPORT,
            _depth(kwargs.get("depth"), f"{label} kwargs.depth"),
        )
    return found


def _enrolled(
    result: dict[str, Any], config: dict[str, Any]
) -> dict[str, tuple[str, int]]:
    from_result = _participants(result, "result.json")
    from_config = _participants(config, "config.json")
    if from_result != from_config:
        _fail("config.json and result.json enroll different window seats")
    return from_result


def _load_world(trial: Path) -> tuple[dict[str, str], dict[str, list]]:
    doc = _obj(
        _read(trial, Path("artifacts/logs/world/pettingzoo.json")), "pettingzoo.json"
    )
    if doc.keys() != DOC_FIELDS or doc["schema"] != "seatlab.pz-native/v1":
        _fail("pettingzoo.json must be a seatlab.pz-native/v1 object")
    raw_roles, steps = doc["roles"], doc["steps"]
    if (
        not isinstance(raw_roles, dict)
        or not 2 <= len(raw_roles) <= 8
        or any(
            ROLE.fullmatch(key) is None or not isinstance(agent, str)
            for key, agent in raw_roles.items()
        )
    ):
        _fail("world roles must map 2-8 Harbor roles pN to agent names")
    roles = cast(dict[str, str], raw_roles)
    if not isinstance(steps, list) or not steps:
        _fail("pettingzoo.json steps must be a nonempty array")
    decisions: dict[str, list[tuple[int, dict[str, Any], int]]] = {
        role: [] for role in roles
    }
    for index, raw in enumerate(steps, 1):
        block = raw.get("decisions") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or raw.keys() != STEP_FIELDS
            or type(raw["step"]) is not int
            or raw["step"] != index
            or not isinstance(block, dict)
            or not block
        ):
            _fail(f"world step {index}: invalid step object")
        for role, item in block.items():
            if role not in roles or not isinstance(item, dict):
                _fail(f"world step {index}: invalid decision for {role!r}")
            if item.keys() != DECISION_FIELDS:
                _fail(f"world step {index}: {role} decision has wrong fields")
            view, sha, action = item["view"], item["view_sha256"], item["action"]
            if (
                not isinstance(view, dict)
                or digest(view) != sha
                or type(action) is not int
            ):
                _fail(f"world step {index}: {role} view/ack/action mismatch")
            legal = view.get("legal_actions")
            if (
                not isinstance(legal, list)
                or any(type(a) is not int for a in legal)
                or action not in legal
            ):
                _fail(f"world step {index}: {role} action is not legal in its view")
            decisions[role].append((index, view, action))
    for role, items in decisions.items():
        if not items:
            _fail(f"world role {role} has no accepted decision")
    for name in ("returns", "terminated", "truncated"):
        block = doc[name]
        if not isinstance(block, dict) or set(block) != set(roles):
            _fail(f"{name} must be keyed by every world role")
        for role, value in block.items():
            if name == "returns":
                _num(value, f"returns[{role}]")
            elif type(value) is not bool:
                _fail(f"{name}[{role}] must be boolean")
    if not all(doc["terminated"][role] or doc["truncated"][role] for role in roles):
        _fail("a role has no recorded final state")
    return roles, decisions


def _seats(
    result: dict[str, Any], roles: dict[str, str], counts: dict[str, int]
) -> None:
    raw = result.get("seat_results")
    if not isinstance(raw, list):
        _fail("result.json missing seat_results")
    seats = {entry.get("seat") if isinstance(entry, dict) else None for entry in raw}
    if seats != set(roles) or len(raw) != len(roles):
        _fail("seat_results must contain exactly the world roles")
    for entry in raw:
        seat = entry.get("seat")
        if (
            entry.get("under_test") is not True
            or entry.get("exception_info") is not None
        ):
            _fail(f"seat {seat} must be under_test with no exception")
        execution = entry.get("agent_execution")
        if not isinstance(execution, dict) or not isinstance(
            execution.get("finished_at"), str
        ):
            _fail(f"seat {seat} missing agent_execution.finished_at")
        info = entry.get("agent_info")
        if not isinstance(info, dict) or info.get("name") != SEAT_NAME:
            _fail(f"seat {seat} did not record the window seat agent")
        agent_result = entry.get("agent_result")
        meta = agent_result.get("metadata") if isinstance(agent_result, dict) else None
        if not isinstance(meta, dict) or meta.keys() != META_FIELDS:
            _fail(f"seat {seat} missing validated program-seat metadata")
        if meta["schema"] != "seatlab.program-seat/v1":
            _fail(f"seat {seat} metadata schema is not the program-seat schema")
        if meta["role"] != seat:
            _fail(f"seat {seat} metadata role disagrees with its seat")
        if meta["stage"] != "done":
            _fail(f"seat {seat} did not finish its program loop")
        if (
            type(meta["program_calls"]) is not int
            or meta["program_calls"] != counts[seat]
        ):
            _fail(f"seat {seat} program_calls disagrees with accepted decisions")


def _audit(trial: Path, role: str, expected: list[tuple[int, str, int]]) -> None:
    rel = Path("seats") / role / "agent" / "pz_actions.jsonl"
    observed: list[tuple[int, str, int]] = []
    for number, line in enumerate(_lines(_read(trial, rel), rel.as_posix()), 1):
        row = _strict_line(line, f"{rel}:{number}")
        if (
            row.keys() != AUDIT_FIELDS
            or type(row["step"]) is not int
            or type(row["action"]) is not int
            or not isinstance(row["view_sha256"], str)
        ):
            _fail(f"{rel}:{number}: invalid audit row")
        observed.append((row["step"], row["view_sha256"], row["action"]))
    if observed != expected:
        _fail(f"seat {role} pz_actions.jsonl disagrees with accepted decisions")


def _frames(
    trial: Path,
    role: str,
    depth: int,
    decisions: list[tuple[int, dict[str, Any], int]],
) -> list[dict[str, Any]]:
    rel = Path("seats") / role / "agent" / "window_frames.jsonl"
    lines = _lines(_read(trial, rel), rel.as_posix())
    if len(lines) != len(decisions):
        _fail(f"seat {role} window frames do not cover every accepted decision")
    out: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for index, (line, (_, view, action)) in enumerate(zip(lines, decisions), 1):
        row = _strict_line(line, f"{rel}:{index}")
        if row.keys() != FRAME_FIELDS:
            _fail(f"{rel}:{index}: frame has the wrong fields")
        if type(row["depth"]) is not int or row["depth"] != depth:
            _fail(f"{rel}:{index}: frame depth disagrees with enrollment")
        if type(row["turn"]) is not int or row["turn"] != index:
            _fail(f"{rel}:{index}: turn must be the role-local decision index")
        if row["view_sha256"] != digest(view):
            _fail(f"{rel}:{index}: view digest disagrees with the accepted view")
        if row["action_sha256"] != digest({"action": action}):
            _fail(f"{rel}:{index}: action digest disagrees with the accepted action")
        sent = [{"view": item["view"], "action": item["action"]} for item in history]
        window = actor_window(current=view, history=sent, depth=depth)
        if type(window) is not dict or window.get("schema") != WINDOW_SCHEMA:
            _fail(f"{rel}:{index}: actor_window returned a non-canonical envelope")
        payload = canonical(window)
        if type(row["context_bytes"]) is not int:
            _fail(f"{rel}:{index}: context_bytes must be an int")
        if row["context_sha256"] != digest(window):
            _fail(f"{rel}:{index}: receipt context disagrees with the accepted history")
        if row["context_bytes"] != len(payload):
            _fail(f"{rel}:{index}: receipt context bytes disagree with the input")
        out.append(
            {
                "turn": index,
                "view_sha256": row["view_sha256"],
                "action_sha256": row["action_sha256"],
                "context_sha256": row["context_sha256"],
                "context_bytes": row["context_bytes"],
            }
        )
        if depth:
            history.append({"view": view, "action": action})
            del history[:-depth]
    return out


def export_windows(trial: Path, out: Path) -> None:
    """Validate one completed window trial and write strict per-role receipts."""
    trial = trial.resolve()
    if not trial.is_dir():
        _fail(f"not a trial directory: {trial.name}")
    if os.path.lexists(out):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(out))
    if not out.parent.is_dir():
        _fail(f"output parent does not exist: {out.parent}")
    result, tid = _check_result(trial)
    config = _obj(_read(trial, Path("config.json")), "config.json")
    enrolled = _enrolled(result, config)
    roles, decisions = _load_world(trial)
    if set(enrolled) != set(roles):
        _fail("enrolled participants must be exactly the world roles")
    counts = {role: len(items) for role, items in decisions.items()}
    _seats(result, roles, counts)
    receipts: list[dict[str, Any]] = []
    for role in sorted(roles):
        expected = [
            (step, digest(view), action) for step, view, action in decisions[role]
        ]
        _audit(trial, role, expected)
        depth = enrolled[role][1]
        frames = _frames(trial, role, depth, decisions[role])
        receipts.append(
            {
                "schema": RECEIPT_SCHEMA,
                "native_trial_id": tid,
                "role": role,
                "depth": depth,
                "frames": frames,
            }
        )
    write_lines(out, receipts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path, metavar="NATIVE_TRIAL")
    parser.add_argument("--out", type=Path, required=True, metavar="NEW_RECEIPTS.jsonl")
    args = parser.parse_args(argv)
    try:
        export_windows(args.trial, args.out)
    except (ValueError, OSError, TapeError) as exc:
        print(f"export_windows.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
