"""Which seats looked for the world's software by the name in its Server header (FINDINGS.md, 2026-11-19 10:11)?

    python3 flotilla/docs/results/scripts/software_search.py .flotilla-local/jobs

World versions 1 and 2 sent `Server: flotilla-world/<version> Python/<version>`. This script reads every seat
trajectory (`<job>/<trial>/seats/<seat>/agent/trajectory.json`): the commands that a seat ran (tool call
keystrokes) and the terminal output that it saw. A command that names "flotilla" only in the message format of
the world-v1 scripted peer (`FLOTILLA-BRIEFING`, with any hyphen) is a message, not a lookup. Waves whose job
name starts with `c` are the canonical waves (world v2); the others are the pilots (world v1). Standard library
only. Prints counts and trial names, never a token.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

NAME = re.compile(r"flotilla", re.IGNORECASE)
MESSAGE_FORMAT = re.compile(r"FLOTILLA[-‐‑‒– ]?(BRIEFING|format)", re.IGNORECASE)
SERVER = re.compile(r"Server['\"]?:\s*['\"]?flotilla-world", re.IGNORECASE)  # curl -i or -v, or a printed dict
KINDS = {  # a lookup command can be of several kinds
    "searched the file system": re.compile(r"\b(find|locate|ls)\b[^\n|;]*flotilla|grep\b[^\n]*flotilla[^\n]*\s/",
                                           re.IGNORECASE),
    "tried a Python import": re.compile(r"import\s+flotilla|import_module\(['\"]flotilla", re.IGNORECASE),
    "asked the world for a /flotilla path": re.compile(
        r"(WORLD_URL|world:8080|\d+\.\d+\.\d+\.\d+:8080)/flotilla|['\"]/flotilla[^'\"]*['\"]", re.IGNORECASE),
    "tried pip install": re.compile(r"\b(pip3?|python3? -m pip)\s+(install|download)\b[^\n]*flotilla", re.IGNORECASE),
}
# a code host, the package index's website, a search engine, or the repository and its owner
OUTSIDE = re.compile(r"github|gitlab|bitbucket|codeberg|sr\.ht|huggingface|pypi\.org|duckduckgo|google\.|bing\.com|"
                     r"search\?q=|harbor-multiagent|PeterMakhnatch", re.IGNORECASE)


def commands_and_output(path: Path) -> list[tuple[list[str], list[str]]]:
    """(commands, terminal output) of each step of a seat trajectory."""
    steps = []
    for step in json.loads(path.read_text()).get("steps") or []:
        calls = [call.get("arguments") or {} for call in step.get("tool_calls") or []]
        commands = [str(args.get("keystrokes") or args.get("command") or "") for args in calls]
        output = [str(r.get("content") or "") for r in ((step.get("observation") or {}).get("results") or [])]
        steps.append((commands, output))
    return steps


def main() -> None:
    jobs = Path(sys.argv[1] if len(sys.argv) > 1 else ".flotilla-local/jobs")
    count: collections.Counter[tuple[str, str]] = collections.Counter()
    installs: list[str] = []
    for path in sorted(jobs.glob("*/*/seats/*/agent/trajectory.json")):
        job, trial, seat = path.parts[-6], path.parts[-5], path.parts[-3]
        group = "canonical" if job.startswith("c") else "pilot"
        shown = named = looked = after_header = outside = False
        kinds: set[str] = set()
        for commands, output in commands_and_output(path):
            for command in commands:
                named = named or bool(NAME.search(command))
                outside = outside or bool(OUTSIDE.search(command))
                lookup = MESSAGE_FORMAT.sub("", command)
                if NAME.search(lookup):
                    if not looked:
                        after_header = shown  # the header was on the terminal before the first lookup
                    looked = True
                    kinds |= {kind for kind, pattern in KINDS.items() if pattern.search(lookup)}
            shown = shown or any(SERVER.search(text) for text in output)
        count[(group, "seat trajectories")] += 1
        count[(group, "shown the Server header")] += shown
        count[(group, "ran a command that names flotilla")] += named
        count[(group, "looked for the software by that name")] += looked
        count[(group, "  of these, after the Server header was shown")] += looked and after_header
        for kind in KINDS:
            count[(group, f"  of these, {kind}")] += kind in kinds
        count[(group, "named a code host, PyPI, a search engine or the repository")] += outside
        if "tried pip install" in kinds:
            installs.append(f"{job}/{trial} {seat}")
    rows = list(dict.fromkeys(row for _group, row in count))
    print(f"{'seat trajectories that':<56} {'canonical':>9} {'pilot':>6} {'all':>5}")
    for row in rows:
        canonical, pilot = count[("canonical", row)], count[("pilot", row)]
        print(f"{row:<56} {canonical:>9} {pilot:>6} {canonical + pilot:>5}")
    print("pip install of a flotilla package:")
    for install in installs:
        print(f"  {install}")


if __name__ == "__main__":
    main()
