"""Write Harbor job configs for built Flotilla tasks, one job per seat roster.

A job's participant roster must match every task's seats, so `solo` tasks (seat p1) and team tasks
(p1..pN) go to separate jobs. Every seat gets the same harness and model (a homogeneous team).

    python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent oracle --name oracle-check
    python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent terminus-2 \\
        --model openrouter/deepseek/deepseek-v4-flash --attempts 1 --concurrent 3 --name ds-wave1

It refuses tasks with readable names (`build_tasks.py --readable-names`, or a build before opaque names):
Harbor names a trial's Docker containers and networks after the task folder, and a seat can read them, so
they say the condition. `--allow-readable-names` lets them through, for a local debugging job only.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPAQUE_NAME = re.compile(r"^t-[0-9a-f]{12}$")  # the folder name that build_tasks.py writes (CONTRACT.md 2)


def seats_of(task_dir: Path) -> tuple[str, ...]:
    config = tomllib.loads((task_dir / "task.toml").read_text())
    return tuple(sorted(config.get("agents", {})))


def opaque(task_dir: Path) -> bool:
    """Whether the folder name and the task name are opaque (`t-<12 hex>`, `flotilla/t-<12 hex>`): Harbor names the
    trial and its Docker containers and networks after the folder, and its results after the task name."""
    name = (tomllib.loads((task_dir / "task.toml").read_text()).get("task") or {}).get("name")
    return bool(OPAQUE_NAME.match(task_dir.name)) and name == f"flotilla/{task_dir.name}"


def participants(seats: tuple[str, ...], agent: str, model: str | None, max_turns: int | None,
                 seat_agent: list[str]) -> list[dict]:
    base = {"name": agent} | ({"model_name": model} if model else {})
    if max_turns:
        base["kwargs"] = {"max_turns": max_turns}
    overrides = {}
    for spec in seat_agent:
        seat, _, rest = spec.partition("=")
        name, _, over_model = rest.partition(":")
        over = {"name": name} | ({"model_name": over_model} if over_model else {})
        if "kwargs" in base:
            over["kwargs"] = dict(base["kwargs"])
        overrides[seat] = over
    return [{"seat": seat, **overrides.get(seat, base)} for seat in seats]

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
    parser.add_argument("--allow-readable-names", action="store_true",
                        help="accept tasks with readable names (their Docker names say the condition): for a local "
                        "debugging job only, never for a wave")
    args = parser.parse_args()

    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    readable: list[str] = []
    for path in sorted(glob.glob(args.tasks)):
        task_dir = Path(path)
        if (task_dir / "task.toml").is_file():
            groups[seats_of(task_dir)].append(str(task_dir.resolve()))
            if not opaque(task_dir):
                readable.append(task_dir.name)
    if readable and not args.allow_readable_names:
        total = sum(len(tasks) for tasks in groups.values())
        print(f"refused: readable names in {len(readable)} of {total} tasks (for example {readable[0]}); Harbor names "
              "a trial's Docker containers and networks after the task folder, and a seat can read them. Build the "
              "set again without --readable-names, or pass --allow-readable-names for a local debugging job.",
              file=sys.stderr)
        sys.exit(2)
    args.out.mkdir(parents=True, exist_ok=True)
    same_count = {size: sum(len(roster) == size for roster in groups) for size in {len(r) for r in groups}}
    for seats, tasks in sorted(groups.items()):
        participant_rows = participants(seats, args.agent, args.model, args.max_turns, args.seat_agent)
        suffix = f"{len(seats)}seat" if same_count[len(seats)] == 1 else f"{len(seats)}seat-{'-'.join(seats)}"
        config = {
            "job_name": f"{args.name}-{suffix}",
            "jobs_dir": args.jobs_dir,
            "n_attempts": args.attempts,
            "timeout_multiplier": 1.0,
            "n_concurrent_trials": args.concurrent,
            "quiet": True,
            "environment": {"type": args.env, "force_build": False, "delete": True}
            | ({"kwargs": {"modal_vm_runtime": True, "app_name": "flotilla"}} if args.env == "modal" else {}),
            "participants": participant_rows,
            "tasks": [{"path": task} for task in tasks],
        }
        out = args.out / f"{args.name}-{suffix}.json"
        out.write_text(json.dumps(config, indent=2) + "\n")
        print(f"{out}  ({len(tasks)} tasks, seats {','.join(seats)})")


if __name__ == "__main__":
    main()
