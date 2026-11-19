"""Write Harbor job configs for built Flotilla tasks, one job per seat roster.

A job's participant roster must match every task's seats, so `solo` tasks (seat p1) and team tasks
(p1..pN) go to separate jobs. Every seat gets the same harness and model (a homogeneous team).

    python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent oracle --name oracle-check
    python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent terminus-2 \\
        --model openrouter/deepseek/deepseek-v4-flash --attempts 1 --concurrent 3 --name ds-wave1
"""

from __future__ import annotations

import argparse
import glob
import json
import tomllib
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def seats_of(task_dir: Path) -> tuple[str, ...]:
    config = tomllib.loads((task_dir / "task.toml").read_text())
    return tuple(sorted(config.get("agents", {})))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", required=True, help="glob of task directories")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--model")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--concurrent", type=int, default=2)
    parser.add_argument("--name", required=True, help="job name prefix")
    parser.add_argument("--max-turns", type=int, help="cap the turns of every model seat (terminus-2 max_turns)")
    parser.add_argument("--seat-agent", action="append", default=[], metavar="SEAT=AGENT[:MODEL]",
                        help="override the agent of one seat, e.g. p3=oracle; repeatable")
    parser.add_argument("--env", choices=("docker", "modal"), default="docker",
                        help="docker: local; modal: Modal VM runtime (Compose networks need it)")
    parser.add_argument("--jobs-dir", default=str(ROOT.parent / ".flotilla-local" / "jobs"))
    parser.add_argument("--out", type=Path, default=ROOT / "configs" / "generated")
    args = parser.parse_args()

    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for path in sorted(glob.glob(args.tasks)):
        task_dir = Path(path)
        if (task_dir / "task.toml").is_file():
            groups[seats_of(task_dir)].append(str(task_dir.resolve()))
    args.out.mkdir(parents=True, exist_ok=True)
    for seats, tasks in sorted(groups.items()):
        participant = {"name": args.agent} | ({"model_name": args.model} if args.model else {})
        if args.max_turns:
            participant["kwargs"] = {"max_turns": args.max_turns}
        overrides = {}
        for spec in args.seat_agent:
            seat, _, agent = spec.partition("=")
            name, _, model = agent.partition(":")
            overrides[seat] = {"name": name} | ({"model_name": model} if model else {})
        config = {
            "job_name": f"{args.name}-{len(seats)}seat",
            "jobs_dir": args.jobs_dir,
            "n_attempts": args.attempts,
            "timeout_multiplier": 1.0,
            "n_concurrent_trials": args.concurrent,
            "quiet": True,
            "environment": {"type": args.env, "force_build": False, "delete": True}
            | ({"kwargs": {"modal_vm_runtime": True, "app_name": "flotilla"}} if args.env == "modal" else {}),
            "participants": [{"seat": seat, **overrides.get(seat, participant)} for seat in seats],
            "tasks": [{"path": task} for task in tasks],
        }
        out = args.out / f"{args.name}-{len(seats)}seat.json"
        out.write_text(json.dumps(config, indent=2) + "\n")
        print(f"{out}  ({len(tasks)} tasks, seats {','.join(seats)})")


if __name__ == "__main__":
    main()
