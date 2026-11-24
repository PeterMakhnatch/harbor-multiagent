from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, NoReturn, override

from .seat_stream import SEAT, Record

if TYPE_CHECKING:
    from .arm_runtime import Runtime
    from .arm_sources import Source

SCHEMA = "seatlab.workbench.arm-isolation/v1"
WORLD_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
PIN = re.compile(r"sha256:[0-9a-f]{64}")
MODES = ("role_instruction", "participant", "world_setting")
CAVEAT = (
    "This checks two supplied local task trees and their recorded trial inputs. It does not observe model service, "
    "host gates, mutable images, schedules, file ownership, effective container settings or what a seat actually saw. "
    "An isolated input is not a behavioral result or permission to spend."
)


class Arm(NamedTuple):
    source: Source
    runtime: Runtime


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid isolate arguments. Use isolate --help.\n")


def _digest(value: object) -> str:
    data = json.dumps(
        value, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("ascii")
    return (
        "sha256:"
        + hashlib.sha256(b"seatlab.workbench.prepared-input/v1\0" + data).hexdigest()
    )


def _pin(arm: Arm) -> str:
    s, r = arm
    return _digest(
        (
            s.seats,
            s.native_digest,
            s.lock_covers_roles,
            s.roles,
            s.effective_roles,
            s.groups,
            r.task["name"],
            r.participants,
            r.env,
            r.groups,
        )
    )


def _changed(left: dict[str, str], right: dict[str, str]) -> set[str]:
    return {k for k in left.keys() | right.keys() if left.get(k) != right.get(k)}


def _arm(task: Path, lock: Path) -> Arm:
    from .arm_runtime import inspect_lock
    from .arm_sources import inspect_source

    runtime = inspect_lock(lock)
    source = inspect_source(task.absolute(), runtime.task)
    if source.seats != runtime.seats or len(source.seats) < 2:
        raise ValueError("the local task and trial lock must declare the same team")
    return Arm(source, runtime)


def _component(
    mode: str, name: str, target: str, left: dict[str, str], right: dict[str, str]
) -> tuple[bool, bool, int]:
    changed = _changed(left, right)
    if mode != name:
        return False, False, len(changed)
    present = target in left and target in right
    return present, present and target in changed, len(changed - {target})


def compare(
    left: Arm, right: Arm, mode: str, target: str, expected: str | None
) -> Record:
    assert mode in MODES
    src, dst = left.source, right.source
    lrt, rrt = left.runtime, right.runtime
    pins = {"left": _pin(left), "right": _pin(right)}
    pair = _digest((SCHEMA, mode, target, pins))
    pin_matches = expected is None or expected == pair
    pieces = {
        "role_instruction": (src.roles, dst.roles),
        "participant": (lrt.participants, rrt.participants),
        "world_setting": (lrt.env, rrt.env),
    }
    other: dict[str, int] = {}
    present = changed = False
    for name, (before, after) in pieces.items():
        p, c, n = _component(mode, name, target, before, after)
        if name == mode:
            present, changed = p, c
        if n:
            other[name] = n
    loaded = _changed(src.effective_roles, dst.effective_roles)
    unexplained = loaded - _changed(src.roles, dst.roles)
    if unexplained:
        other["loaded_instruction_without_file_change"] = len(unexplained)
    for prefix, a, b in (
        ("source", src.groups, dst.groups),
        ("runtime", lrt.groups, rrt.groups),
    ):
        for key in _changed(a, b):
            other[f"{prefix}.{key}"] = 1
    if src.seats != dst.seats:
        other["roster"] = 1
    if lrt.task["name"] != rrt.task["name"]:
        other["task_name"] = 1
    native_changed = src.native_digest != dst.native_digest
    coverage = {"left": src.lock_covers_roles, "right": dst.lock_covers_roles}
    reasons: list[str] = []
    if not present:
        reasons.append("target_not_explicit_in_both_arms")
    elif not changed:
        reasons.append("target_unchanged")
    if mode == "role_instruction" and present and changed and target not in loaded:
        reasons.append("native_loaded_instruction_unchanged")
    if other:
        reasons.append("additional_inputs_differ")
    if mode == "role_instruction" and (
        not all(coverage.values()) or not native_changed
    ):
        reasons.append("role_change_not_bound_by_both_native_locks")
    if mode != "role_instruction" and native_changed:
        reasons.append("native_task_digest_changed")
    if not pin_matches:
        reasons.append("different_from_expected_comparison")
    return {
        "schema": SCHEMA,
        "status": "not_isolated" if reasons else "isolated",
        "target": {
            "kind": mode,
            "name": target,
            "present_in_both": present,
            "changed": changed,
            "native_loaded_text_changed": target in loaded
            if mode == "role_instruction" and present
            else None,
        },
        "other_changes": dict(sorted(other.items())),
        "native_task_digest_changed": native_changed,
        "native_lock_covers_roles": coverage,
        "unpackaged_non_role_files": {
            "left": src.unpackaged_files,
            "right": dst.unpackaged_files,
        },
        "inputs": pins,
        "trial_lock_records": {"left": lrt.record_key, "right": rrt.record_key},
        "comparison": pair,
        "matches_expected": None if expected is None else pin_matches,
        "reasons": reasons,
        "caveat": CAVEAT,
    }


def _parse(args: list[str]) -> argparse.Namespace:
    parser = Parser(
        prog="python -m examples.seatlab.workbench isolate",
        allow_abbrev=False,
        description="Check that a prepared multi-agent comparison changes exactly one declared input. Reads local files only.",
    )
    for name in ("left_task", "left_lock", "right_task", "right_lock"):
        parser.add_argument(name, type=Path, metavar=name.upper())
    group = parser.add_mutually_exclusive_group(required=True)
    for name in MODES:
        group.add_argument(
            "--" + name.replace("_", "-"),
            metavar="KEY" if name == "world_setting" else "SEAT",
        )
    parser.add_argument(
        "--expected-comparison",
        metavar="SHA256",
        help="also require the previously recorded, ordered pair of prepared inputs",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the machine-readable result"
    )
    return parser.parse_args(args)


def _text(result: Record) -> str:
    target, covered = result["target"], result["native_lock_covers_roles"]
    reasons = ", ".join(result["reasons"]) or "none"
    other = (
        ", ".join(f"{key}={n}" for key, n in result["other_changes"].items()) or "none"
    )
    return "\n".join(
        (
            f"{result['status']}: {target['kind']} {target['name']}",
            f"Target differs: {target['changed']}. Additional changes: {other}.",
            f"Native lock includes declared role files: left {covered['left']}, right {covered['right']}.",
            f"Reasons: {reasons}. Comparison: {result['comparison']}",
            CAVEAT,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 9 or any(len(os.fsencode(arg)) > 4096 for arg in args):
        print("Invalid isolate arguments. Use isolate --help.", file=sys.stderr)
        return 2
    opts = _parse(args)
    mode, target = next(
        (m, getattr(opts, m)) for m in MODES if getattr(opts, m) is not None
    )
    pattern = WORLD_KEY if mode == "world_setting" else SEAT
    if not pattern.fullmatch(target) or (
        opts.expected_comparison is not None
        and not PIN.fullmatch(opts.expected_comparison)
    ):
        print("Invalid target or comparison digest.", file=sys.stderr)
        return 2
    arms: list[Arm] = []
    for side, task, lock in (
        ("left", opts.left_task, opts.left_lock),
        ("right", opts.right_task, opts.right_lock),
    ):
        try:
            arms.append(_arm(task, lock))
        except ImportError:
            print(
                "Isolation needs the installed Harbor fork. Use its Python environment, without -S.",
                file=sys.stderr,
            )
            return 2
        except (ValueError, OSError):
            print(
                f"Cannot verify the {side} local task, lock or roster. No comparison.",
                file=sys.stderr,
            )
            return 2
    result = compare(arms[0], arms[1], mode, target, opts.expected_comparison)
    print(
        json.dumps(result, sort_keys=True, separators=(",", ":"))
        if opts.json
        else _text(result)
    )
    return 0 if result["status"] == "isolated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
