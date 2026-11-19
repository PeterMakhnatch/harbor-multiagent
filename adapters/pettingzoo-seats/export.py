"""Project one completed native PettingZoo Harbor trial into SeatLab artifacts.
Does not import Harbor or PettingZoo. Digests check byte equality only.
"""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from seatlab.author import accepted_frame, episode_from_frames
from seatlab.episodes import Episode
from seatlab.tape import TapeError, canonical, decode, digest, write_lines
from seatlab.world_order import WORLD_ORDER_SCHEMA

_ROLE, _DOC = (
    re.compile(r"p[0-9]"),
    frozenset(
        {
            "schema",
            "case_sha256",
            "roles",
            "steps",
            "returns",
            "terminated",
            "truncated",
        }
    ),
)
_STEP, _DEC, _AUDIT = (
    frozenset({"step", "decisions"}),
    frozenset({"view", "view_sha256", "action"}),
    frozenset({"step", "view_sha256", "action"}),
)


def _fail(msg: str) -> Any:
    raise ValueError(msg)


def _obj(data: bytes, where: str) -> dict[str, Any]:
    try:
        val = decode(data)
    except TapeError as exc:
        raise ValueError(f"{where}: invalid JSON: {exc}") from exc
    return (
        cast(dict[str, Any], val)
        if isinstance(val, dict)
        else _fail(f"{where}: expected object")
    )


def _uuid(val: object, where: str) -> str:
    if isinstance(val, str) and len(val) == 36:
        try:
            parsed = UUID(val)
            if str(parsed) == val and parsed.int != 0:
                return val
        except ValueError:
            pass
    return _fail(f"{where}: expected a canonical nonzero UUID")


def _num(val: object, where: str) -> int | float:
    if (
        isinstance(val, bool)
        or not isinstance(val, (int, float))
        or (isinstance(val, float) and not math.isfinite(val))
    ):
        return _fail(f"{where}: expected a finite number")
    return val


def _read(root: Path, rel: Path) -> bytes:
    cur = root.resolve()
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            _fail(f"symlink is not allowed: {rel.as_posix()}")
    if not cur.is_file() or cur.resolve() != root.resolve() / rel:
        _fail(f"missing required file {rel.as_posix()}")
    return cur.read_bytes()


def _check_result(trial: Path, roles: dict[str, str]) -> str:
    res = _obj(_read(trial, Path("result.json")), "result.json")
    tid = _uuid(res.get("id"), "result.json id")
    if res.get("exception_info") is not None or res.get("reward_valid") is not True:
        _fail("result.json has an exception or reward_valid is not true")
    if not isinstance(res.get("finished_at"), str) or not res["finished_at"]:
        _fail("result.json missing finished_at")
    seats = res.get("seat_results")
    got = (
        {e.get("seat") if isinstance(e, dict) else None for e in seats}
        if isinstance(seats, list)
        else set()
    )
    if got != set(roles) or len(seats) != len(roles):
        _fail("seat_results must contain exactly the world roles")
    for entry in seats:
        seat, ex = entry.get("seat"), entry.get("agent_execution")
        if (
            entry.get("under_test") is not True
            or entry.get("exception_info") is not None
        ):
            _fail(f"seat {seat} must be under_test with no exception")
        if not isinstance(ex, dict) or not isinstance(ex.get("finished_at"), str):
            _fail(f"seat {seat} missing agent_execution.finished_at")
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
    return tid


def _check_audit(trial: Path, role: str, expected: list[tuple[int, str, int]]) -> None:
    rel = Path("seats") / role / "agent" / "pz_actions.jsonl"
    if not (trial / rel).is_file():
        _fail(f"missing actor audit {rel.as_posix()}; refusing projection")
    observed: list[tuple[int, str, int]] = []
    for n, line in enumerate(_read(trial, rel).decode().splitlines(), 1):
        row = (
            _obj(line.encode(), f"{rel}:{n}")
            if line.strip()
            else _fail(f"{rel}:{n}: blank")
        )
        if (
            row.keys() != _AUDIT
            or type(row["step"]) is not int
            or type(row["action"]) is not int
            or not isinstance(row["view_sha256"], str)
        ):
            _fail(f"{rel}:{n}: invalid audit row")
        observed.append((row["step"], row["view_sha256"], row["action"]))
    if observed != expected:
        _fail(f"seat {role} pz_actions.jsonl disagrees with accepted decisions")


def _load_world(trial: Path) -> tuple[dict[str, Any], dict[str, str], list[list[str]]]:
    doc = _obj(
        _read(trial, Path("artifacts/logs/world/pettingzoo.json")), "pettingzoo.json"
    )
    if doc.keys() != _DOC or doc["schema"] != "seatlab.pz-native/v1":
        _fail("pettingzoo.json must be a seatlab.pz-native/v1 object")
    raw_roles, steps = doc["roles"], doc["steps"]
    bad_roles = (
        not isinstance(raw_roles, dict)
        or not 2 <= len(raw_roles) <= 8
        or any(
            _ROLE.fullmatch(k) is None or not isinstance(v, str)
            for k, v in raw_roles.items()
        )
    )
    if bad_roles:
        _fail("world roles must map 2-8 Harbor roles pN to agent names")
    roles = cast(dict[str, str], raw_roles)
    if not isinstance(steps, list) or not steps:
        _fail("pettingzoo.json steps must be a nonempty array")
    groups: list[list[str]] = []
    counts = dict.fromkeys(roles, 0)
    for index, raw in enumerate(steps, 1):
        decisions = raw.get("decisions") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or raw.keys() != _STEP
            or type(raw["step"]) is not int
            or raw["step"] != index
            or not isinstance(decisions, dict)
            or not decisions
        ):
            _fail(f"world step {index}: invalid step object")
        group: list[str] = []
        for role, item in decisions.items():
            if role not in roles or not isinstance(item, dict) or item.keys() != _DEC:
                _fail(f"world step {index}: invalid decision for {role!r}")
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
            counts[role] += 1
            group.append(role)
        groups.append(sorted(group))
    if any(n < 1 for n in counts.values()):
        _fail("every world role must have an accepted decision")
    for name in ("returns", "terminated", "truncated"):
        block = doc[name]
        if not isinstance(block, dict) or set(block) != set(roles):
            _fail(f"{name} must be keyed by every world role")
        for role, val in block.items():
            if name == "returns":
                _num(val, f"returns[{role}]")
            elif type(val) is not bool:
                _fail(f"{name}[{role}] must be boolean")
    if not all(doc["terminated"][r] or doc["truncated"][r] for r in roles):
        _fail("a role has no recorded final state")
    return doc, roles, groups


def project(trial: Path, out_dir: Path) -> dict[str, Any]:
    """Validate one native trial and publish episodes, order, and outcomes."""
    if os.path.lexists(out_dir):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), out_dir)
    if not out_dir.parent.is_dir():
        _fail(f"output parent does not exist: {out_dir.parent}")
    doc, roles, groups = _load_world(trial)
    tid, case_sha = _check_result(trial, roles), doc["case_sha256"]
    if not isinstance(case_sha, str):
        _fail("invalid case_sha256")
    frames: dict[str, list[Any]] = {r: [] for r in roles}
    audits: dict[str, list[tuple[int, str, int]]] = {r: [] for r in roles}
    for raw in cast(list[dict[str, Any]], doc["steps"]):
        for role, item in cast(dict[str, Any], raw["decisions"]).items():
            frames[role].append(
                accepted_frame(
                    turn=len(frames[role]) + 1,
                    view=item["view"],
                    acknowledged_sha256=item["view_sha256"],
                    action=item["action"],
                )
            )
            audits[role].append((raw["step"], item["view_sha256"], item["action"]))
    for role, expected in audits.items():
        _check_audit(trial, role, expected)
    episodes: list[Episode] = [
        episode_from_frames(
            id=f"pz:{tid}:{role}",
            case_sha256=case_sha,
            role=role,
            native_trial_id=tid,
            closed=True,
            frames=frames[role],
        )
        for role in sorted(roles)
    ]
    order = {
        "schema": WORLD_ORDER_SCHEMA,
        "case_sha256": case_sha,
        "native_trial_id": tid,
        "episodes": {ep["role"]: ep["episode_sha256"] for ep in episodes},
        "steps": groups,
    }
    outcomes = {
        "schema": "seatlab.pz-outcomes/v1",
        "native_trial_id": tid,
        "case_sha256": case_sha,
        "completion": 1,
        "returns": {r: doc["returns"][r] for r in sorted(roles)},
        "terminated": {r: doc["terminated"][r] for r in sorted(roles)},
        "truncated": {r: doc["truncated"][r] for r in sorted(roles)},
    }
    staging = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=".pz-export-"))
    try:
        write_lines(staging / "episodes.jsonl", episodes)
        (staging / "order.json").write_bytes(canonical(order) + b"\n")
        (staging / "outcomes.json").write_bytes(canonical(outcomes) + b"\n")
        if os.path.lexists(out_dir):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), out_dir)
        os.rename(staging, out_dir)
    except Exception:
        for child in staging.iterdir():
            child.unlink(missing_ok=True)
        staging.rmdir()
        raise
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path, metavar="NATIVE_TRIAL")
    parser.add_argument("out", type=Path, metavar="NEW_OUTPUT_DIR")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(project(args.trial, args.out), sort_keys=True))
    except (ValueError, OSError, TapeError) as exc:
        print(f"export.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
