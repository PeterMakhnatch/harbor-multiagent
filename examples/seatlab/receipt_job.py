"""Prepare one stock multi-seat Harbor JobConfig from a prepared receipt-board dir."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import tomllib
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

PLAN_SCHEMA = "seatlab.receipt-job-plan/v1"
ARMS: tuple[str, ...] = ("full", "first")
SEATS: tuple[str, ...] = ("p1", "p2")
JOB_PREFIX = "receipt-board-job"
CONFIG_FILENAME = "job-config.json"
PLAN_FILENAME = "job-plan.json"

_REQUIRED_FILES = (
    "task.toml",
    "environment/case.json",
    "environment/mode.json",
    "environment/world.py",
    "environment/docker-compose.yaml",
    "solution/solve.sh",
    "tests/test.sh",
    *(f"instructions/{seat}.md" for seat in SEATS),
)


def _sha256_json(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _strict_json(path: Path) -> Any:
    def _no_dups(pairs: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key {key!r} in {path.name}")
            out[key] = value
        return out

    def _no_const(token: str) -> object:
        raise ValueError(f"non-finite JSON constant {token!r} in {path.name}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_no_dups,
        parse_constant=_no_const,
    )


def _safe_under(root: Path, relative: str, *, expect_dir: bool) -> Path:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink not allowed: {relative}")
    resolved = current.resolve()
    if resolved != root / relative:
        raise ValueError(f"path escapes root: {relative}")
    if expect_dir and not resolved.is_dir():
        raise ValueError(f"missing directory: {relative}")
    if not expect_dir and not resolved.is_file():
        raise ValueError(f"missing file: {relative}")
    return resolved


def _tree_sha256(task_dir: Path) -> str:
    files = sorted(
        (p for p in task_dir.rglob("*") if p.is_file() or p.is_symlink()),
        key=lambda p: p.relative_to(task_dir).as_posix(),
    )
    digest = hashlib.sha256()
    for path in files:
        if path.is_symlink():
            raise ValueError(f"symlink not allowed inside task: {path.name}")
        rel = path.relative_to(task_dir).as_posix()
        file_hex = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{rel}\0{file_hex}\n".encode("utf-8"))
    return digest.hexdigest()


def _inspect_arm(prepared_root: Path, arm: str) -> tuple[Path, dict[str, Any]]:
    task_dir = _safe_under(prepared_root, f"{arm}/task", expect_dir=True)
    for rel in _REQUIRED_FILES:
        _safe_under(task_dir, rel, expect_dir=False)

    toml_data = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    agents = toml_data.get("agents")
    if not isinstance(agents, dict) or set(agents) != set(SEATS):
        raise ValueError(f"arm {arm!r} task.toml must declare exact seats {SEATS}")
    task_table = toml_data.get("task")
    task_name = (
        task_table["name"]
        if isinstance(task_table, dict) and isinstance(task_table.get("name"), str)
        else task_dir.name
    )

    mode = _strict_json(task_dir / "environment/mode.json")
    if mode != arm:
        raise ValueError(f"arm {arm!r} mode.json must be {arm!r}, got {mode!r}")
    case = _strict_json(task_dir / "environment/case.json")
    if not isinstance(case, dict) or set(case) != {"options"}:
        raise ValueError(f"arm {arm!r} case.json must contain only 'options'")
    options = case["options"]
    if not isinstance(options, dict) or set(options) != set(SEATS):
        raise ValueError(f"arm {arm!r} case.json options must match seats {SEATS}")

    views: dict[str, dict[str, Any]] = {}
    view_sha256: dict[str, str] = {}
    expected_actions: dict[str, str] = {}
    for seat in SEATS:
        values = options[seat]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item for item in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError(f"arm {arm!r} has invalid options for seat {seat!r}")
        choices = cast(list[str], values)
        visible = choices if arm == "full" else choices[:1]
        view = {"role": seat, "options": visible}
        views[seat] = view
        view_sha256[seat] = _sha256_json(view)
        expected_actions[seat] = visible[-1]

    return task_dir, {
        "task_name": task_name,
        "case_sha256": _sha256_json(case),
        "views": views,
        "view_sha256": view_sha256,
        "expected_actions": expected_actions,
    }


def prepare_job(prepared: Path, out: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Build one fresh stock multi-seat JobConfig and registered job plan."""
    prepared_raw = prepared.expanduser()
    if prepared_raw.is_symlink() or not prepared_raw.is_dir():
        raise ValueError(
            f"prepared directory must be an existing directory: {prepared}"
        )
    prepared_root = prepared_raw.resolve()

    out_raw = out.expanduser()
    if out_raw.exists() or out_raw.is_symlink():
        raise FileExistsError(f"output directory already exists: {out_raw}")
    if not out_raw.parent.is_dir():
        raise FileNotFoundError(f"output parent does not exist: {out_raw.parent}")
    out_root = out_raw.resolve()
    if out_root == prepared_root or prepared_root in out_root.parents:
        raise ValueError("output directory must be outside the prepared directory")

    inspected = [
        (slot, arm, *_inspect_arm(prepared_root, arm))
        for slot, arm in enumerate(ARMS, 1)
    ]
    if len({meta["case_sha256"] for _, _, _, meta in inspected}) != 1:
        raise ValueError("full and first tasks must share identical case.json")
    source_digests = [_tree_sha256(source_task) for _, _, source_task, _ in inspected]
    if len(set(source_digests)) != len(ARMS):
        raise ValueError("full and first task variants must have distinct digests")

    job_name = f"{JOB_PREFIX}-{uuid4().hex[:12]}"
    out_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    tasks_dir = out_root / "tasks"
    jobs_dir = out_root / "jobs"
    tasks_dir.mkdir(mode=0o755)
    jobs_dir.mkdir(mode=0o755)

    task_entries: list[dict[str, str]] = []
    slots: list[dict[str, Any]] = []
    for (slot, arm, source_task, meta), task_sha256 in zip(
        inspected, source_digests, strict=True
    ):
        variant_slug = f"receipt-board-{arm}"
        dest_task = tasks_dir / variant_slug
        shutil.copytree(source_task, dest_task, symlinks=False)
        task_entries.append({"path": str(dest_task.resolve()), "source": variant_slug})
        slots.append(
            {
                "expected_slot": slot,
                "arm": arm,
                "variant_slug": variant_slug,
                "task_sha256": task_sha256,
                "task_name": meta["task_name"],
                "mode": arm,
                "case_sha256": meta["case_sha256"],
                "seats": list(SEATS),
                "expected_views": meta["views"],
                "expected_view_sha256": meta["view_sha256"],
                "expected_actions": meta["expected_actions"],
                "expected_team_reward": 1,
            }
        )

    participants = [{"seat": seat, "name": "oracle"} for seat in SEATS]
    job_config = {
        "schema_version": 1,
        "job_name": job_name,
        "jobs_dir": str(jobs_dir.resolve()),
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "environment": {"type": "docker", "force_build": True, "delete": True},
        "participants": participants,
        "tasks": task_entries,
    }
    plan = {
        "schema": PLAN_SCHEMA,
        "job_name": job_name,
        "expected_trials": len(slots),
        "seats": list(SEATS),
        "participants": participants,
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "max_retries": 0,
        "slots": slots,
    }

    config_path = out_root / CONFIG_FILENAME
    plan_path = out_root / PLAN_FILENAME
    config_path.write_text(json.dumps(job_config, indent=2) + "\n", encoding="utf-8")
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return config_path, plan_path, plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared", type=Path, required=True, help="Prepared receipt-board directory"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Fresh output directory"
    )
    args = parser.parse_args(argv)
    try:
        config_path, plan_path, plan = prepare_job(args.prepared, args.out)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"receipt_job: {exc}\n")

    job_dir = args.out.expanduser().resolve() / "jobs" / plan["job_name"]
    print(shlex.join(["harbor", "run", "-c", str(config_path)]))
    print(f"Expected job directory: {job_dir}")
    print(f"Registered job plan: {plan_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
