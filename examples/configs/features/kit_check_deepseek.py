"""Run one paid DeepSeek Kit Check trial. The key stays on the host."""

import argparse
import asyncio
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from kit_check_controls import HERE, ROLES, TASK
from kit_check_deepseek_agent import DeepSeekKitAgent, set_api_key
from kit_check_subscription import _summarize

_BALANCE_URL = "https://api.deepseek.com/user/balance"
_MIN_BALANCE_USD = 1.50
_LANE_CAP_USD = 0.50


def _balance(key: str) -> float:
    request = urllib.request.Request(
        _BALANCE_URL, headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(4096))
    except (OSError, ValueError):
        raise RuntimeError("Could not read the DeepSeek balance") from None
    infos = payload.get("balance_infos") if isinstance(payload, dict) else None
    if not isinstance(infos, list):
        raise RuntimeError("DeepSeek balance response has no balance")
    for info in infos:
        if isinstance(info, dict) and info.get("currency") == "USD":
            return float(info["total_balance"])
    raise RuntimeError("DeepSeek balance response has no USD balance")


async def _run(directory: Path, mode: str) -> dict[str, Any]:
    from harbor.job import Job
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.trial.config import EnvironmentConfig, Participant, TaskConfig

    directory.mkdir(parents=True, exist_ok=False)
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
            participants=[
                Participant(
                    seat=seat,
                    model_name="deepseek-host/deepseek-flash",
                    import_path=DeepSeekKitAgent.import_path(),
                    kwargs={"role": seat},
                )
                for seat in ROLES
            ],
            tasks=[TaskConfig(path=TASK)],
        )
    )
    await job.run()
    return _summarize(directory / mode, mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("visible", "silent"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-freeze", action="store_true")
    args = parser.parse_args()
    key = os.environ.pop("DEEPSEEK_API_KEY", "")
    if not key:
        parser.error("DEEPSEEK_API_KEY is not set")
    os.environ["HARBOR_TELEMETRY"] = "0"
    if args.require_freeze:
        from harbor.models.trial.result import SeatResult

        if "freeze" not in SeatResult.model_fields:
            parser.error("This Harbor checkout cannot record seat freezes")
    before = _balance(key)
    if before < _MIN_BALANCE_USD:
        parser.error(f"DeepSeek balance ${before:.2f} is below ${_MIN_BALANCE_USD:.2f}")
    set_api_key(key)
    directory = args.directory.resolve()
    receipt = asyncio.run(_run(directory, args.mode))
    after = _balance(key)
    spent = round(before - after, 6)
    receipt["deepseek_balance_before_usd"] = before
    receipt["deepseek_balance_after_usd"] = after
    receipt["deepseek_balance_delta_usd"] = spent
    output = directory / "receipt.json"
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(f"DeepSeek balance change: ${spent:.6f}")
    if spent > _LANE_CAP_USD:
        parser.exit(2, "This trial exceeded the lane cap\n")
    if not receipt["valid_reward"] or any(seat["error"] for seat in receipt["seats"]):
        parser.exit(1, "No valid trial with all seats completed\n")
    if args.require_freeze and receipt["all_seats_frozen_before_verifier"] is not True:
        parser.exit(
            1, "The result does not confirm that all seats froze before grading\n"
        )


if __name__ == "__main__":
    main()
