"""Run one real-model Kit Check trial using the local Codex subscription."""

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_text_turn import PROVIDER, check_subscription
from kit_check_codex_agent import CodexKitAgent, ScriptedKitReviewer
from kit_check_controls import ARTIFACT, HERE, ROLES, TASK


def _finished(timing: dict[str, Any] | None) -> str | None:
    return timing.get("finished_at") if timing else None


def _duration(timing: dict[str, Any] | None) -> float | None:
    if not timing or not timing.get("started_at") or not timing.get("finished_at"):
        return None
    start = datetime.fromisoformat(timing["started_at"])
    finish = datetime.fromisoformat(timing["finished_at"])
    return (finish - start).total_seconds() if finish >= start else None


def _freeze_before_verifier(
    seats: list[dict[str, Any]],
    *,
    play: dict[str, Any] | None,
    verifier: dict[str, Any],
) -> bool | None:
    freezes = [seat.get("freeze") for seat in seats]
    if not any(freezes):
        return None
    if len(seats) != len(ROLES) or {seat.get("seat") for seat in seats} != ROLES.keys():
        return False
    if any(not freeze or freeze.get("exception_info") for freeze in freezes):
        return False
    ended = [_finished(seat.get("agent_execution")) for seat in seats]
    stopped = [_finished(freeze) for freeze in freezes]
    started = verifier.get("started_at")
    if not started:
        return None
    play_finished = _finished(play)
    if not play_finished or not all(ended) or not all(stopped):
        return False
    play_end = datetime.fromisoformat(play_finished)
    ends = [datetime.fromisoformat(stamp) for stamp in ended if stamp is not None]
    stops = [datetime.fromisoformat(stamp) for stamp in stopped if stamp is not None]
    verifier_start = datetime.fromisoformat(started)
    return max(ends) <= play_end <= min(stops) and max(stops) <= verifier_start


def _choice_policy(world: dict[str, Any] | None) -> dict[str, Any] | None:
    if world is None:
        return None
    cases = world["cases"]
    choices = [str(case["choice"]) for case in cases]
    observations = [case.get("observation") or {} for case in cases]
    counts = {kit: choices.count(kit) for kit in ("K1", "K2", "K3", "K4")}
    policy = "other"
    cycle = None
    if choices and all(choice == choices[0] for choice in choices):
        policy = "constant"
    else:
        for size in range(1, len(choices)):
            if len(choices) % size:
                continue
            block = choices[:size]
            if block * (len(choices) // size) == choices:
                policy = "repeating_cycle"
                cycle = "".join(block)
                break
    reports_hidden = all(
        observation.get("left") is None and observation.get("right") is None
        for observation in observations
    )
    uses_visible_intersection = all(
        isinstance(observation.get("left"), list)
        and isinstance(observation.get("right"), list)
        and len(set(observation["left"]).intersection(observation["right"])) == 1
        and case["choice"]
        in set(observation["left"]).intersection(observation["right"])
        for case, observation in zip(cases, observations, strict=True)
    )
    if uses_visible_intersection:
        policy = "unique_intersection"
    return {
        "counts": counts,
        "policy": policy,
        "cycle": cycle,
        "reports_hidden": reports_hidden,
        "matches_scripted_k1_fallback": bool(choices) and set(choices) == {"K1"},
        "matches_recorded_answer": all(
            case["choice"] == case["correct"] for case in cases
        ),
    }


def _summarize(job_dir: Path, mode: str) -> dict[str, Any]:
    (path,) = sorted(job_dir.glob("*/result.json"))
    trial = json.loads(path.read_bytes())
    lock = json.loads(path.with_name("lock.json").read_bytes())
    seats = trial["seat_results"]
    if (
        len(seats) != len(ROLES)
        or {seat["seat"]: seat["under_test"] for seat in seats} != ROLES
    ):
        raise ValueError("The result does not contain the expected seats")
    world_path = path.parent / ARTIFACT
    world = json.loads(world_path.read_bytes()) if world_path.is_file() else None
    if world is not None and world["mode"] != mode:
        raise ValueError("The world has a different visibility mode")
    verifier_result = trial.get("verifier_result") or {}
    rows = []
    for seat in seats:
        usage = seat.get("agent_result") or {}
        metadata = usage.get("metadata") or {}
        agent_info = seat.get("agent_info") or {}
        rows.append(
            {
                "seat": seat["seat"],
                "under_test": seat["under_test"],
                "agent": agent_info.get("name"),
                "model": agent_info.get("model_info"),
                "setup_seconds": _duration(seat.get("agent_setup")),
                "execution_seconds": _duration(seat.get("agent_execution")),
                "model_turn_seconds": metadata.get(
                    "model_turn_seconds", metadata.get("codex_turn_seconds")
                ),
                "auth": metadata.get("model_auth", metadata.get("codex_auth")),
                "input_tokens": usage.get("n_input_tokens"),
                "cached_input_tokens": usage.get("n_cache_tokens"),
                "output_tokens": usage.get("n_output_tokens"),
                "cost_usd": usage.get("cost_usd"),
                "calculated_cost_usd": metadata.get("calculated_cost_usd"),
                "error": (seat.get("exception_info") or {}).get("exception_type"),
                "freeze": seat.get("freeze"),
            }
        )
    return {
        "mode": mode,
        "trial_id": trial["id"],
        "task_digest": lock["task"]["digest"],
        "valid_reward": trial["reward_valid"],
        "reward": (verifier_result.get("rewards") or {}).get("reward"),
        "cases": len(world["cases"]) if world is not None else None,
        "builder_choices": _choice_policy(world),
        "trial_error": (trial.get("exception_info") or {}).get("exception_type"),
        "play_seconds": _duration(trial.get("agent_execution")),
        "all_seats_frozen_before_verifier": _freeze_before_verifier(
            seats,
            play=trial.get("agent_execution"),
            verifier=trial.get("verifier") or {},
        ),
        "seats": rows,
    }


async def _run(
    directory: Path, mode: str, model: str, *, scripted_reviewers: bool
) -> dict[str, Any]:
    from harbor.job import Job
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.trial.config import EnvironmentConfig, Participant, TaskConfig

    directory.mkdir(parents=True, exist_ok=False)
    participants = [
        Participant(
            seat=seat,
            import_path=ScriptedKitReviewer.import_path(),
            kwargs={"role": seat},
        )
        if scripted_reviewers and not under_test
        else Participant(
            seat=seat,
            model_name=f"{PROVIDER}/{model}",
            import_path=CodexKitAgent.import_path(),
            kwargs={"role": seat},
        )
        for seat, under_test in ROLES.items()
    ]
    job = await Job.create(
        JobConfig(
            job_name=mode,
            jobs_dir=directory,
            n_attempts=1,
            n_concurrent_trials=1,
            quiet=True,
            retry=RetryConfig(max_retries=0),
            environment=EnvironmentConfig(
                type=EnvironmentType.DOCKER,
                delete=True,
                extra_docker_compose=[HERE / f"kit-check-{mode}.override.yaml"],
            ),
            participants=participants,
            tasks=[TaskConfig(path=TASK)],
        )
    )
    await job.run()
    return _summarize(directory / mode, mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("visible", "silent"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--model", required=True, help="Native Codex model ID")
    parser.add_argument(
        "--scripted-reviewers",
        action="store_true",
        help="Use the task's fixed policies for the reviewers, not the model",
    )
    parser.add_argument("--require-freeze", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", args.model):
        parser.error("Pass a native Codex model ID without a provider prefix")
    os.environ["HARBOR_TELEMETRY"] = "0"
    if args.require_freeze:
        from harbor.models.trial.result import SeatResult

        if "freeze" not in SeatResult.model_fields:
            parser.error("This Harbor checkout cannot record seat freezes")
    directory = args.directory.resolve()
    check_subscription()
    receipt = asyncio.run(
        _run(
            directory, args.mode, args.model, scripted_reviewers=args.scripted_reviewers
        )
    )
    output = directory / "receipt.json"
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(output)
    if not receipt["valid_reward"] or any(seat["error"] for seat in receipt["seats"]):
        parser.exit(1, "No valid trial with all seats completed\n")
    if args.require_freeze and receipt["all_seats_frozen_before_verifier"] is not True:
        parser.exit(
            1, "The result does not confirm that all seats froze before grading\n"
        )


if __name__ == "__main__":
    main()
