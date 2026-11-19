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
        config = {
            "job_name": f"{args.name}-{len(seats)}seat",
            "jobs_dir": args.jobs_dir,
            "n_attempts": args.attempts,
            "timeout_multiplier": 1.0,
            "n_concurrent_trials": args.concurrent,
            "quiet": True,
            "environment": {"type": "docker", "force_build": False, "delete": True},
            "participants": [{"seat": seat, **participant} for seat in seats],
            "tasks": [{"path": task} for task in tasks],
        }
        out = args.out / f"{args.name}-{len(seats)}seat.json"
        out.write_text(json.dumps(config, indent=2) + "\n")
        print(f"{out}  ({len(tasks)} tasks, seats {','.join(seats)})")


if __name__ == "__main__":
    main()
