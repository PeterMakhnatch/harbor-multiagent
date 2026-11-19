"""Cross-check the four registered program cells. Not a score.

python program_compare.py PROGRAM_ROOT NATIVE_ROOT --out FRESH_DIR
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from seatlab.tape import TapeError, canonical
from seatlab.world_order import read_world_order

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from export import project  # noqa: E402


SCHEMA = "seatlab.pz-program-compare/v1"
JS, BEAT = HERE / "programs" / "copy-or-high.mjs", HERE / "programs" / "beat.py"
CELLS = (
    ("program-mixed", "rps-copy-beat", {"p0": "copy"}),
    ("program-both", "rps-copy-beat", {"p0": "copy", "p1": "beat"}),
    ("program-second", "connect-lh", {"p1": "high"}),
    ("program-invalid", None, {}),
)
EXTERNAL = {
    "program-mixed": ("p0",),
    "program-both": ("p0", "p1"),
    "program-second": ("p1",),
    "program-invalid": ("p0",),
}
REFS = {
    "rps-copy-beat": ("rps_v2", {"p0": "rps-copy", "p1": "rps-beat-last"}),
    "connect-lh": ("connect_four_v3", {"p0": "low", "p1": "high"}),
}


def _uuid(value: object) -> str | None:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return value if parsed and str(parsed) == value and parsed.int else None


def _safe(name: object) -> bool:
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\\" not in name
    )


def _items(root: Path, schema: str) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("plan") from exc
    cells = raw.get("cells") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != schema
        or not isinstance(cells, list)
    ):
        raise ValueError("plan")
    out: dict[str, dict[str, Any]] = {}
    for item in cells:
        cid = item.get("id") if isinstance(item, dict) else None
        name = item.get("trial_name") if isinstance(item, dict) else None
        if not isinstance(cid, str) or cid in out or not _safe(name):
            raise ValueError("plan")
        out[cid] = item
    return out


def _ok_config(root: Path, cid: str, name: str) -> bool:
    path = root / "configs" / f"{cid}.json"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(doc, dict) and doc.get("trial_name") == name


def _runtime(given: Path | None, *fallbacks: str) -> Path | None:
    if given is not None and not given.is_absolute():
        raise ValueError("runtime")
    paths = [given] if given else [Path(p) for p in fallbacks if p]
    if given is None:
        paths = [
            Path(item if os.path.isabs(item) else shutil.which(item) or "")
            for item in fallbacks
        ]
    found = next(
        (path for path in paths if path.is_file() and os.access(path, os.X_OK)), None
    )
    if given is not None and found is None:
        raise ValueError("runtime")
    return found


def _meta(blob: object) -> dict[str, Any] | None:
    if not isinstance(blob, dict):
        return None
    if isinstance(blob.get("metadata"), dict):
        return blob["metadata"]
    extra, agent = blob.get("extra"), blob.get("agent")
    if isinstance(extra, dict):
        return extra["metadata"] if isinstance(extra.get("metadata"), dict) else extra
    return _meta(agent.get("extra")) if isinstance(agent, dict) else None


def _calls(seat: dict[str, Any], trial: Path, role: str) -> int | None:
    found = _meta(seat.get("agent_result")) or _meta(seat.get("trajectory"))
    path = trial / "seats" / role / "agent" / "trajectory.json"
    if found is None and _safe(role) and path.is_file() and not path.is_symlink():
        try:
            found = _meta(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError):
            found = None
    calls = found.get("program_calls") if isinstance(found, dict) else None
    return calls if type(calls) is int else None


def _json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _rows(order: Any) -> list[dict[str, dict[str, Any]]]:
    cursor, rows = dict.fromkeys(order.episodes, 0), []
    for index, group in enumerate(order.steps, 1):
        row = {}
        for role in group:
            frame = order.episodes[role]["frames"][cursor[role]]
            cursor[role] += 1
            observed = frame.get("observed") if isinstance(frame, dict) else None
            row[role] = {
                "view": frame.get("view"),
                "digest": frame.get("view_sha256"),
                "action": observed.get("action")
                if isinstance(observed, dict)
                else None,
                "step": index,
            }
        rows.append(row)
    return rows


def _project(trial: Path, dest: Path) -> tuple[Any, dict[str, Any]] | None:
    try:
        project(trial, dest)
        order = read_world_order(dest / "episodes.jsonl", dest / "order.json")
        outcomes = json.loads((dest / "outcomes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TapeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(outcomes, dict)
        or outcomes.get("schema") != "seatlab.pz-outcomes/v1"
    ):
        return None
    return order, outcomes


def _cross(
    left: list, right: list
) -> tuple[dict[str, Any] | None, dict[str, int], dict[str, int]]:
    compared, agreed, first = {}, {}, None
    for index in range(min(len(left), len(right))):
        if tuple(left[index]) != tuple(right[index]):
            return {"step": index + 1, "role": None, "kind": "group"}, compared, agreed
        for role in left[index]:
            got, exp = left[index][role], right[index][role]
            view_ok = isinstance(got["digest"], str) and got["digest"] == exp["digest"]
            act_ok = (
                type(got["action"]) is type(exp["action"])
                and got["action"] == exp["action"]
            )
            compared[role] = compared.get(role, 0) + 1
            agreed[role] = agreed.get(role, 0) + int(view_ok and act_ok)
            if first is None and not (view_ok and act_ok):
                first = {
                    "step": index + 1,
                    "role": role,
                    "kind": "view" if not view_ok else "action",
                }
    if first is None and len(left) != len(right):
        first = {"step": min(len(left), len(right)) + 1, "role": None, "kind": "length"}
    return first, compared, agreed


def _run(argv: list[str], view: dict[str, Any]) -> int | None:
    home = tempfile.TemporaryDirectory(prefix="pz-home-")
    work = tempfile.TemporaryDirectory(prefix="pz-cwd-")
    try:
        proc = subprocess.run(
            argv,
            input=canonical(view) + b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=work.name,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "HOME": home.name,
                "TMPDIR": home.name,
            },
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, TapeError):
        return None
    finally:
        home.cleanup()
        work.cleanup()
    if proc.returncode or len(proc.stdout) > 4096:
        return None
    try:
        body = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    action = (
        body.get("action")
        if isinstance(body, dict) and set(body) == {"action"}
        else None
    )
    return action if type(action) is int else None


def _argv(mode: str, node: Path | None, python: Path | None) -> list[str] | None:
    if mode == "beat" and python and BEAT.is_file():
        return [str(python), str(BEAT)]
    if mode in ("copy", "high") and node and JS.is_file():
        return [str(node), str(JS), mode]
    return None


def _roles(result: dict[str, Any], trial: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in result.get("seat_results") or []:
        role = row.get("seat") if isinstance(row, dict) else None
        if not _safe(role) or role in out:
            continue
        info, timing = row.get("agent_info"), row.get("agent_execution")
        name = info.get("name") if isinstance(info, dict) else None
        stamp = timing.get("finished_at") if isinstance(timing, dict) else None
        out[role] = {
            "participant": name if _safe(name) else None,
            "executed": bool(stamp) if isinstance(timing, dict) else None,
            "observed_calls": _calls(row, trial, role),
            "comparisons": None,
            "agreements": None,
            "replay_calls": None,
            "replay_agreements": None,
        }
    return out


def _maps(left: object, right: object, flag: bool) -> bool:
    if (
        not isinstance(left, dict)
        or not isinstance(right, dict)
        or set(left) != set(right)
    ):
        return False
    for key, value in left.items():
        other = right[key]
        if flag and (type(value) is not bool or value is not other):
            return False
        if not flag and (
            type(value) is bool or type(value) is not type(other) or value != other
        ):
            return False
    return True


def _finish(
    row: dict[str, Any], order: Any, outcomes: dict[str, Any], other: tuple | None
) -> None:
    row["returns"] = (
        outcomes.get("returns") if isinstance(outcomes.get("returns"), dict) else None
    )
    frames = _rows(order)
    if other is None:
        row.update(
            status="checked",
            first_mismatch={"step": 0, "role": None, "kind": "accepted"},
        )
        return
    ref_order, ref_out = other
    row["case_equal"] = order.case_sha256 == ref_order.case_sha256
    if not row["case_equal"]:
        row.update(
            status="checked",
            first_mismatch={"step": 0, "role": None, "kind": "case"},
            _frames=frames,
        )
        return
    first, compared, agreed = _cross(frames, _rows(ref_order))
    row["returns_equal"] = _maps(outcomes.get("returns"), ref_out.get("returns"), False)
    row["terminated_equal"] = _maps(
        outcomes.get("terminated"), ref_out.get("terminated"), True
    )
    row["truncated_equal"] = _maps(
        outcomes.get("truncated"), ref_out.get("truncated"), True
    )
    flags = (
        (row["returns_equal"], "return"),
        (row["terminated_equal"], "terminated"),
        (row["truncated_equal"], "truncated"),
    )
    for equal, kind in flags:
        if first is None and not equal:
            first = {"step": len(frames) + 1, "role": None, "kind": kind}
    for role, count in compared.items():
        if role in row["roles"]:
            row["roles"][role]["comparisons"] = count
            row["roles"][role]["agreements"] = agreed.get(role, 0)
    row.update(status="checked", first_mismatch=first, _frames=frames)


def _cell(
    roots: tuple[Path, Path], work: Path, spec: tuple, plans: tuple[dict, dict]
) -> dict[str, Any]:
    program_root, native_root = roots
    cid, ref, modes = spec
    program, native = plans
    name, trial = (
        program[cid]["trial_name"],
        program_root / "trials" / program[cid]["trial_name"],
    )
    claim = program_root / "attempts" / cid
    row: dict[str, Any] = {
        "id": cid,
        "status": "absent",
        "attempted": claim.is_dir() and not claim.is_symlink(),
        "native_trial_id": None,
        "reference": ref,
        "reference_trial_id": None,
        "completion": None,
        "returns": None,
        "case_equal": None,
        "returns_equal": None,
        "terminated_equal": None,
        "truncated_equal": None,
        "roles": {},
        "first_mismatch": None,
        "exception_type": None,
        "_modes": modes,
        "_frames": None,
    }
    if not (
        _ok_config(program_root, cid, name)
        and trial.is_dir()
        and not trial.is_symlink()
    ):
        row["status"] = "failed" if trial.exists() else "absent"
        return row
    loaded = _json(trial / "result.json")
    if loaded is None:
        row["status"] = "failed"
        return row
    row["roles"] = _roles(loaded, trial)
    row["native_trial_id"] = _uuid(loaded.get("id"))
    rewards = (
        (loaded.get("verifier_result") or {}).get("rewards")
        if isinstance(loaded.get("verifier_result"), dict)
        else None
    )
    score = rewards.get("reward") if isinstance(rewards, dict) else None
    row["completion"] = (
        score
        if type(score) is not bool
        and isinstance(score, (int, float))
        and score == score
        else None
    )
    info = loaded.get("exception_info")
    kind = info.get("exception_type") if isinstance(info, dict) else None
    row["exception_type"] = kind if _safe(kind) else None
    if info is not None or loaded.get("reward_valid") is not True:
        row["status"] = "failed"
        return row
    opened = _project(trial, work / cid)
    if opened is None:
        row["status"] = "failed"
        return row
    if ref is None:
        _finish(row, *opened, None)
        return row
    ref_trial = native_root / "trials" / native[ref]["trial_name"]
    ready = (
        _ok_config(native_root, ref, native[ref]["trial_name"])
        and ref_trial.is_dir()
        and not ref_trial.is_symlink()
    )
    ref_doc = _json(ref_trial / "result.json") if ready else None
    row["reference_trial_id"] = _uuid(ref_doc.get("id")) if ref_doc else None
    other = _project(ref_trial, work / f"ref-{cid}") if ready else None
    if other is None:
        order, outcomes = opened
        row["returns"] = (
            outcomes.get("returns")
            if isinstance(outcomes.get("returns"), dict)
            else None
        )
        row["_frames"] = _rows(order)
        row["status"] = "absent" if not ready else "failed"
        return row
    _finish(row, *opened, other)
    return row


def _reuse(rows: list[dict[str, Any]]) -> None:
    ids = [row["native_trial_id"] for row in rows if row["native_trial_id"]]
    dupes = {item for item in ids if ids.count(item) > 1}
    refs = {row["reference_trial_id"] for row in rows if row["reference_trial_id"]}
    for row in rows:
        tid = row["native_trial_id"]
        hit = tid and (tid in dupes or tid == row["reference_trial_id"] or tid in refs)
        if not hit:
            continue
        row.update(
            status="reused",
            case_equal=None,
            returns_equal=None,
            terminated_equal=None,
            truncated_equal=None,
            first_mismatch=None,
            _frames=None,
        )
        for role in row["roles"].values():
            role["comparisons"] = role["agreements"] = None


def _replay(row: dict[str, Any], node: Path | None, python: Path | None) -> int:
    frames, modes, calls = row.pop("_frames"), row.pop("_modes"), 0
    if not isinstance(frames, list):
        return 0
    for role, mode in modes.items():
        argv, seat = _argv(mode, node, python), row["roles"].get(role)
        if seat is None or argv is None:
            if row["first_mismatch"] is None:
                row["first_mismatch"] = {"step": 0, "role": role, "kind": "runtime"}
            continue
        agreed = seen = 0
        for step in frames:
            item = step.get(role)
            if not isinstance(item, dict) or not isinstance(item.get("view"), dict):
                continue
            seen, calls = seen + 1, calls + 1
            got = _run(argv, item["view"])
            same = type(got) is int and got == item["action"]
            agreed += int(same)
            if row["first_mismatch"] is None and not same:
                row["first_mismatch"] = {
                    "step": item["step"],
                    "role": role,
                    "kind": "replay",
                }
        seat["replay_calls"], seat["replay_agreements"] = seen, agreed
    return calls


def _text(value: str) -> str | None:
    if "\\" in value or value.startswith("/"):
        return None
    if "/" not in value:
        return value
    head, _, tail = value.rpartition("/")
    if head and "/" not in head and tail.startswith("v") and tail[1:].isdigit():
        return value
    return None


def _seal(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _seal(item)
            for key, item in value.items()
            if isinstance(key, str) and _text(key) is not None
        }
    if isinstance(value, list):
        return [_seal(item) for item in value]
    if isinstance(value, str):
        return _text(value)
    if value is None or type(value) in (bool, int, float):
        return value
    return None


def compare(
    program_root: Path,
    native_root: Path,
    out_dir: Path,
    *,
    node: Path | None = None,
    python: Path | None = None,
) -> dict[str, Any]:
    """Write a path-free cross-check. Disagreement is a result, not a reward."""
    if os.path.lexists(out_dir):
        raise FileExistsError(out_dir)
    if not out_dir.parent.is_dir():
        raise ValueError("output parent")
    program, native = (
        _items(program_root, "seatlab.pz-program-plan/v1"),
        _items(native_root, "seatlab.pz-plan/v1"),
    )
    want = {cid: ref for cid, ref, _modes in CELLS}
    if set(program) != set(want) or any(
        program[cid].get("reference") != ref for cid, ref in want.items()
    ):
        raise ValueError("plan")
    for cid, (game, policies) in REFS.items():
        item = native.get(cid)
        if item is None or item.get("game") != game or item.get("policies") != policies:
            raise ValueError("plan")
    node_bin, python_bin = (
        _runtime(node, "node"),
        _runtime(python, sys.executable, "python3"),
    )
    work = Path(tempfile.mkdtemp(prefix="pz-program-compare-"))
    try:
        rows = [
            _cell((program_root, native_root), work, spec, (program, native))
            for spec in CELLS
        ]
        _reuse(rows)
        replayed = sum(_replay(row, node_bin, python_bin) for row in rows)
        ids, known, seen, unknown = set(), 0, False, False
        for row in rows:
            ids.update(
                item
                for item in (row["native_trial_id"], row["reference_trial_id"])
                if item
            )
            for role in EXTERNAL[row["id"]]:
                seat = row["roles"].get(role)
                if seat is None:
                    continue
                seen, calls = True, seat["observed_calls"]
                known += calls if type(calls) is int else 0
                unknown = unknown or type(calls) is not int
        report = {
            "schema": SCHEMA,
            "denominator": len(CELLS),
            "unique_native_ids": len(ids),
            "observed_external_calls": known if seen and not unknown else None,
            "replay_calls": replayed,
            "cells": rows,
        }
        report = _seal(report)
        staging = Path(
            tempfile.mkdtemp(dir=out_dir.parent, prefix=".pz-program-compare-")
        )
        try:
            (staging / "index.json").write_bytes(canonical(report) + b"\n")
            os.rename(staging, out_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program_root", type=Path)
    parser.add_argument("native_root", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--python", type=Path)
    args = parser.parse_args(argv)
    report = compare(
        args.program_root,
        args.native_root,
        args.out,
        node=args.node,
        python=args.python,
    )
    sys.stdout.buffer.write(canonical(report) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
