"""Run or replay the Kit Check paired, model-free Docker control."""

import argparse
import asyncio
import hashlib
import json
import os
from itertools import permutations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TASK = HERE.parents[1] / "tasks" / "kit-check"
KITS = ("K1", "K2", "K3", "K4")
ROLES = {"left": False, "right": False, "builder": True}
PAIRS = (("visible", "silent"), ("silent", "visible"))
ARTIFACT = "artifacts/logs/world/kit-check.json"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _overlay(mode: str) -> Path:
    return HERE / f"kit-check-{mode}.override.yaml"


def _check(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


async def _run(root: Path) -> None:
    os.environ["HARBOR_TELEMETRY"] = "0"
    from harbor.job import Job
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.trial.config import EnvironmentConfig, Participant, TaskConfig

    root.mkdir(parents=True, exist_ok=False)
    for pair, sequence in enumerate(PAIRS, start=1):
        for mode in sequence:
            name = f"pair-{pair}-{mode}"
            print(f"Running {name}", flush=True)
            async with asyncio.timeout(240):
                job = await Job.create(
                    JobConfig(
                        job_name=name,
                        jobs_dir=root,
                        n_attempts=1,
                        n_concurrent_trials=1,
                        quiet=True,
                        retry=RetryConfig(max_retries=0),
                        environment=EnvironmentConfig(
                            type=EnvironmentType.DOCKER,
                            delete=True,
                            extra_docker_compose=[_overlay(mode)],
                        ),
                        participants=[
                            Participant(seat=role, name="oracle") for role in ROLES
                        ],
                        tasks=[TaskConfig(path=TASK)],
                    )
                )
                await job.run()


def _read_arm(
    job: Path, mode: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    result = _load(job / "result.json")
    _check(
        bool(result.get("finished_at")) and result["n_total_trials"] == 1,
        f"{job.name}: expected one finished trial",
    )
    (trial_path,) = sorted(job.glob("*/result.json"))
    trial = _load(trial_path)
    _check(
        trial["reward_valid"] and trial["exception_info"] is None,
        f"{job.name}: trial did not produce a valid reward",
    )
    seats = trial["seat_results"]
    _check(
        len(seats) == len(ROLES)
        and {row["seat"]: row["under_test"] for row in seats} == ROLES,
        f"{job.name}: recorded seats do not match the protocol",
    )
    _check(
        all(
            row["exception_info"] is None and row["agent_info"]["name"] == "oracle"
            for row in seats
        ),
        f"{job.name}: every scripted participant must complete normally",
    )
    job_lock = _load(job / "lock.json")
    lock = _load(trial_path.parent / "lock.json")
    _check(job_lock["trials"] == [lock], f"{job.name}: trial lock differs from plan")
    overlays = lock.pop("extra_docker_compose")
    _check(
        len(overlays) == 1
        and overlays[0]["digest"] == _digest(_overlay(mode).read_bytes()),
        f"{job.name}: unexpected Compose overlay",
    )
    lock["environment"].pop("extra_docker_compose", None)
    common = {
        "harbor": job_lock["harbor"],
        "lock": lock,
        "checksum": trial["task_checksum"],
    }

    world_bytes = (trial_path.parent / ARTIFACT).read_bytes()
    world = json.loads(world_bytes)
    _check(
        world["schema"] == "kit-check/v1" and world["mode"] == mode,
        f"{job.name}: wrong world schema or mode",
    )
    cases = world["cases"]
    worlds = list(permutations(KITS, 3))
    _check(len(cases) == len(worlds), f"{job.name}: incomplete world batch")
    hits = 0
    for seed, ((gold, a, b), case) in enumerate(zip(worlds, cases, strict=True)):
        reviews = {"left": sorted((gold, a)), "right": sorted((gold, b))}
        inbox = reviews if mode == "visible" else dict.fromkeys(reviews)
        _check(
            case["seed"] == seed
            and case["reviews"] == reviews
            and case["observation"] == {"choices": list(KITS), **inbox},
            f"{job.name}: seed {seed} received different evidence",
        )
        choice = gold if mode == "visible" else KITS[0]
        _check(
            case["choice"] == choice
            and case["correct"] == gold
            and case["success"] is (choice == gold),
            f"{job.name}: seed {seed} does not replay",
        )
        hits += choice == gold
    _check(
        trial["verifier_result"]["rewards"] == {"reward": hits / len(worlds)},
        f"{job.name}: Harbor reward differs from the replay",
    )
    return (
        {
            "job": job.name,
            "job_id": result["id"],
            "trial_id": trial["id"],
            "mode": mode,
            "correct": hits,
            "cases": len(worlds),
            "world_sha256": _digest(world_bytes),
        },
        common,
        cases,
    )


def replay(root: Path) -> dict[str, Any]:
    records = []
    locked_inputs = []
    by_mode = {}
    for pair, sequence in enumerate(PAIRS, start=1):
        for mode in sequence:
            record, inputs, cases = _read_arm(root / f"pair-{pair}-{mode}", mode)
            locked_inputs.append(inputs)
            if mode in by_mode:
                _check(
                    record["world_sha256"] == by_mode[mode][0],
                    f"{mode}: repeat changed world bytes",
                )
            else:
                by_mode[mode] = (record["world_sha256"], cases)
            records.append(record)
    common = locked_inputs[0]
    _check(
        all(inputs == common for inputs in locked_inputs[1:]),
        "Only the Compose mode may change across trials",
    )
    for key in ("job_id", "trial_id"):
        _check(
            len({record[key] for record in records}) == len(records), f"Duplicate {key}"
        )
    visible, silent = (by_mode[mode][1] for mode in ("visible", "silent"))
    outcomes = {"visible_only": 0, "silent_only": 0, "ties": 0}
    for visible_case, silent_case in zip(visible, silent, strict=True):
        if visible_case["success"] == silent_case["success"]:
            outcomes["ties"] += 1
        elif visible_case["success"]:
            outcomes["visible_only"] += 1
        else:
            outcomes["silent_only"] += 1
    return {
        "schema": "kit-check-controls/v1",
        "harbor": common["harbor"],
        "task_digest": common["lock"]["task"]["digest"],
        "task_checksum": common["checksum"],
        "paired_outcomes_per_repeat": outcomes,
        "runs": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "replay"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    try:
        if args.action == "run":
            asyncio.run(_run(root))
        print(json.dumps(replay(root), indent=2, sort_keys=True))
    except (OSError, TimeoutError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"Kit Check: {exc}\n")


if __name__ == "__main__":
    main()
