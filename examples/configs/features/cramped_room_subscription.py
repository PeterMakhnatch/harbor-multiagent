"""Run one fresh Cramped Room team on a local Codex subscription."""

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_text_turn import PROVIDER, check_subscription
from cramped_room_codex_agent import KitchenPilotAgent, prepare_exchange

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, RetryConfig
from harbor.models.trial.config import EnvironmentConfig, Participant, TaskConfig
from harbor.models.trial.result import SeatResult
from harbor.publisher.packager import Packager

_ROLES = ("chef0", "chef1")
_TASK_DIGEST = "sha256:7e88f9753e9abb56f27c870338d4069dee0b80681da7e7f11898da1c5716a6d0"
_WORLD_LOG = "artifacts/logs/world/kitchen.jsonl"
_TICKS = 38


def _stamp(timing: dict[str, Any], key: str) -> datetime | None:
    value = timing.get(key)
    return datetime.fromisoformat(value) if value else None


def _seconds(timing: dict[str, Any]) -> float | None:
    start = _stamp(timing, "started_at")
    end = _stamp(timing, "finished_at")
    return (end - start).total_seconds() if start and end and end >= start else None


def _receipt(job_dir: Path, mode: str) -> dict[str, Any]:
    (path,) = job_dir.glob("*/result.json")
    native = json.loads(path.read_bytes())
    lock = json.loads(path.with_name("lock.json").read_bytes())
    job_lock = json.loads((job_dir / "lock.json").read_bytes())
    seats = native.get("seat_results") or []
    play = native.get("agent_execution") or {}
    play_end = _stamp(play, "finished_at")
    verifier_start = _stamp(native.get("verifier") or {}, "started_at")
    freeze_ends = []
    rows = []
    for seat in seats:
        execution = seat.get("agent_execution") or {}
        seat_end = _stamp(execution, "finished_at")
        freeze = seat.get("freeze") or {}
        freeze_end = _stamp(freeze, "finished_at")
        if (
            seat_end
            and play_end
            and freeze_end
            and verifier_start
            and seat_end <= play_end <= freeze_end <= verifier_start
            and not freeze.get("exception_info")
            and type(freeze.get("stopped")) is int
            and freeze["stopped"] >= 0
        ):
            freeze_ends.append(freeze_end)
        usage = seat.get("agent_result") or {}
        metadata = usage.get("metadata") or {}
        rows.append(
            {
                "seat": seat["seat"],
                "model": (seat.get("agent_info") or {}).get("model_info"),
                "model_auth": metadata.get("model_auth"),
                "execution_seconds": _seconds(execution),
                "model_turn_seconds": metadata.get("model_turn_seconds"),
                "phase_turns": metadata.get("phase_turns"),
                "peer_wait_seconds": metadata.get("peer_wait_seconds"),
                "peer_plan_seen": metadata.get("peer_plan_seen"),
                "checkpoints": metadata.get("checkpoints"),
                "repair_peer_plan_seen": metadata.get("repair_peer_plan_seen"),
                "input_tokens": usage.get("n_input_tokens"),
                "cached_input_tokens": usage.get("n_cache_tokens"),
                "output_tokens": usage.get("n_output_tokens"),
                "cost_usd": usage.get("cost_usd"),
                "error": (seat.get("exception_info") or {}).get("exception_type"),
                "freeze": freeze or None,
            }
        )
    all_frozen = len(freeze_ends) == len(rows) == len(_ROLES) and {
        row["seat"] for row in rows
    } == set(_ROLES)
    world_path = path.parent / _WORLD_LOG
    transitions = (
        [json.loads(line) for line in world_path.read_bytes().splitlines()]
        if world_path.is_file()
        else []
    )
    return {
        "schema": "harbor-cramped-room-subscription/v2",
        "trial_id": native["id"],
        "mode": mode,
        "runtime_commit": job_lock["harbor"]["git_commit_hash"],
        "task_digest": lock["task"]["digest"],
        "trial_error": (native.get("exception_info") or {}).get("exception_type"),
        "valid_reward": native["reward_valid"],
        "reward": ((native.get("verifier_result") or {}).get("rewards") or {}).get(
            "reward"
        ),
        "world_ticks": len(transitions),
        "world_ticks_complete": [row["tick"] for row in transitions]
        == list(range(1, _TICKS + 1)),
        "native_points": sum(row["reward"] for row in transitions)
        if transitions
        else None,
        "play_seconds": _seconds(play),
        "all_seats_frozen_before_verifier": all_frozen,
        "last_freeze_after_play_seconds": (
            (max(freeze_ends) - play_end).total_seconds()
            if all_frozen and play_end
            else None
        ),
        "seats": rows,
    }


async def _run(task: Path, output: Path, mode: str, model: str) -> dict[str, Any]:
    prepare_exchange()
    job = await Job.create(
        JobConfig(
            job_name="kitchen",
            jobs_dir=output,
            n_attempts=1,
            n_concurrent_trials=1,
            quiet=True,
            retry=RetryConfig(max_retries=0),
            environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
            participants=[
                Participant(
                    seat=role,
                    model_name=f"{PROVIDER}/{model}",
                    import_path=KitchenPilotAgent.import_path(),
                    kwargs={"role": role, "mode": mode}
                    if role == "chef0"
                    else {"role": role},
                )
                for role in _ROLES
            ],
            tasks=[TaskConfig(path=task)],
        )
    )
    await job.run()
    return _receipt(output / "kitchen", mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("observe", "repair"))
    parser.add_argument(
        "output", type=Path, help="New directory for the trial and receipt"
    )
    parser.add_argument(
        "--task", required=True, type=Path, help="Pinned Cramped Room task directory"
    )
    parser.add_argument("--model", required=True, help="Native Codex model ID")
    args = parser.parse_args()
    if "freeze" not in SeatResult.model_fields:
        parser.error("Use a Harbor runtime that records native seat freezes")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", args.model):
        parser.error("Pass a native Codex model ID without a provider prefix")
    task = args.task.resolve()
    if (
        not task.is_dir()
        or f"sha256:{Packager.compute_content_hash(task)[0]}" != _TASK_DIGEST
    ):
        parser.error("Use the pinned Cramped Room parity task from fork PR #46")
    output = args.output.resolve()
    if output.exists():
        parser.error("Choose a new output directory")
    os.environ["HARBOR_TELEMETRY"] = "0"
    check_subscription()
    output.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(_run(task, output, args.mode, args.model))
    receipt = output / "receipt.json"
    receipt.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(receipt)
    if (
        not result["valid_reward"]
        or result["reward"] is None
        or result["trial_error"]
        or result["task_digest"] != _TASK_DIGEST
        or not result["world_ticks_complete"]
        or not result["all_seats_frozen_before_verifier"]
        or any(seat["error"] for seat in result["seats"])
    ):
        parser.exit(
            1,
            "No valid trial with 38 world ticks and both seats frozen before grading\n",
        )


if __name__ == "__main__":
    main()
