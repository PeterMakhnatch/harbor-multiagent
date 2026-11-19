"""Command line: ``flotilla-analyze PATH [PATH ...]`` or ``python3 -m flotilla_analysis PATH ...``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flotilla_analysis import __version__, stats
from flotilla_analysis.render import render
from flotilla_analysis.report import build


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flotilla-analyze",
        description=(
            "Read harbor-multiagent job folders produced by Flotilla tasks and report what coordination "
            "bought the team (solo, silent, talk), what it cost, and whether messages were used. "
            "PATH is a trial folder, a job folder, or any folder that holds job folders."
        ),
    )
    p.add_argument("paths", nargs="+", metavar="PATH")
    p.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    p.add_argument(
        "--seed", type=int, default=1, help="seed for the bootstrap and permutation draws (default 1)"
    )
    p.add_argument(
        "--resamples", type=int, default=stats.RESAMPLES, help="bootstrap resamples (default 10000)"
    )
    p.add_argument(
        "--draws",
        type=int,
        default=stats.DRAWS,
        help="sign-flip draws when there are more than 16 nonzero differences (default 20000)",
    )
    p.add_argument(
        "--tasks",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="where to look for task folders (<DIR>/<label>/<task> or <DIR>/<task>) when the path in "
        "result.json does not exist; may be repeated",
    )
    p.add_argument(
        "--family-hooks",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="folder of family modules (<DIR>/<family>.py, e.g. flotilla/families) whose analysis hooks "
        "(carries_own_data, answer_in_text) replace those of the task folder's family.py; task folders "
        "built before a hook existed lack it. Scores always use the task folder's module. May be repeated",
    )
    p.add_argument(
        "--keep-leaks",
        action="store_true",
        help="keep trials flagged example_leak (the answer key is an answer example in an instruction) in "
        "the decomposition; by default they are left out",
    )
    p.add_argument("--output", type=Path, help="write the report to this file instead of standard output")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.resamples < 1 or args.draws < 1:
        print("flotilla-analyze: --resamples and --draws must be positive", file=sys.stderr)
        return 2
    report = build(
        args.paths,
        seed=args.seed,
        resamples=args.resamples,
        draws=args.draws,
        task_roots=args.tasks,
        keep_leaks=args.keep_leaks,
        hook_dirs=args.family_hooks,
    )
    text = render(report, args.format)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if not report["scores"]:
        print("flotilla-analyze: no Flotilla trials found", file=sys.stderr)
        return 1
    return 0
