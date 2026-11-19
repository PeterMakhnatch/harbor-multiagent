"""Render Flotilla seat tasks: every instance of a family, in every condition.

    python3 flotilla/build_tasks.py --family schedule --instances 0-11 --seats 2 --difficulty easy

Writes flotilla/tasks/<family>/<family>-<iii>-<condition>/ (git-ignored: answer keys and seat tokens
must not be published, because seats have public network access). The same seed always gives the
same instances; tokens are random per build. See CONTRACT.md 2, 3 and 8.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import secrets
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONDITIONS = ("solo", "silent", "talk")
PLAY_TIMEOUT_SEC = 900.0

PLAYER_DOCKERFILE = """FROM python:3.13-slim

RUN apt-get update \\
    && apt-get install -y --no-install-recommends bash ca-certificates curl git jq \\
    && rm -rf /var/lib/apt/lists/* \\
    && useradd --create-home --shell /bin/bash player
"""

MAIN_DOCKERFILE = """FROM python:3.13-slim

COPY world.py score.py family.py task.json /world/
"""

TEST_SH = """#!/bin/bash
set -eu
python3 /world/score.py
"""

SOLVE_SH = """#!/bin/bash
set -eu
python3 "$(dirname "$0")/oracle.py"
"""

CONDITION_TEXT = {
    "solo": "You work alone, and you have all of the information the task needs.",
    "silent": (
        "Your teammates hold information that you do not have, and you hold information that they do "
        "not have. In this run you cannot communicate with them: messaging is disabled. Give the best "
        "answer you can from your own information."
    ),
    "talk": (
        "Your teammates hold information that you do not have, and you hold information that they do "
        "not have. You can exchange messages with them through the world."
    ),
}
CONDITION_TEXT["talk-liar"] = CONDITION_TEXT["talk"]  # the honest seats are not warned
SCRIPTED_INSTRUCTION = "Scripted seat: Harbor's oracle agent runs solution/oracle.py here. No model reads this.\n"


def load_family(name: str) -> Any:
    path = ROOT / "families" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"flotilla_family_{name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"no family module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instruction(family: Any, inst: dict[str, Any], seat: str, seats: list[str], condition: str) -> str:
    team = (
        "You work alone."
        if condition == "solo"
        else f"You are seat **{seat}** in a team of {len(seats)} agents (seats: {', '.join(seats)})."
    )
    private = inst["solo_text"] if condition == "solo" else inst["private_text"][seat]
    auth = '-H "Authorization: Bearer $SEAT_TOKEN"'
    lines = [
        f"# {family.TITLE}",
        "",
        f"{team} {CONDITION_TEXT[condition]}",
        "",
        "## The goal",
        "",
        inst["goal"],
        "",
        "## Your information",
        "",
        private,
        "",
        "## The world service",
        "",
        "All team actions go through the world service at `$WORLD_URL` (http://world:8080). "
        "Authenticate every request with your seat token, which is in the environment variable `$SEAT_TOKEN`.",
        "",
        "```bash",
        f"curl -s $WORLD_URL/whoami {auth}",
        f"curl -s -X POST $WORLD_URL/submit {auth} -H 'Content-Type: application/json' "
        "-d '{\"answer\": {...}}'",
        "```",
        "",
        "The team's answer is the **last submission received from any seat**. You may submit more than once.",
    ]
    if condition in ("talk", "talk-liar"):
        lines += [
            "",
            "Messages (to one seat, or to `all`):",
            "",
            "```bash",
            f"curl -s -X POST $WORLD_URL/messages {auth} -H 'Content-Type: application/json' "
            "-d '{\"to\": \"all\", \"text\": \"...\"}'",
            f"curl -s $WORLD_URL/messages {auth}",
            "```",
        ]
    lines += [
        "",
        "## Answer format",
        "",
        inst["answer_format"],
        "",
        "When your team's final answer is submitted, stop.",
        "",
    ]
    return "\n".join(lines)


def compose(condition: str, seats: list[str], tokens: dict[str, str], admin: str,
            briefings: dict[str, Any]) -> str:
    q = json.dumps  # a JSON string is a valid YAML double-quoted scalar
    lines = [
        "x-player: &player",
        "  build: ./player",
        "  user: player",
        "  working_dir: /home/player",
        '  command: ["sleep", "infinity"]',
        "  init: true",
        "  networks: [play]",
        "  depends_on:",
        "    main:",
        "      condition: service_healthy",
        "",
        "services:",
        "  main:",
        '    command: ["python3", "/world/world.py"]',
        "    environment:",
        f"      FLOTILLA_CONDITION: {q(condition)}",
        f"      FLOTILLA_SEATS: {q(','.join(seats))}",
        f"      FLOTILLA_TOKENS: {q(','.join(f'{s}:{tokens[s]}' for s in seats))}",
        f"      FLOTILLA_ADMIN: {q(admin)}",
        "    networks:",
        "      play:",
        "        aliases: [world]",
        "    healthcheck:",
        '      test: ["CMD", "python3", "-c", "import urllib.request; '
        "urllib.request.urlopen('http://localhost:8080/health', timeout=2)\"]",
        "      interval: 2s",
        "      timeout: 5s",
        "      retries: 15",
        "      start_period: 2s",
    ]
    for seat in seats:
        lines += [
            f"  {seat}:",
            "    <<: *player",
            "    environment:",
            f"      SEAT: {q(seat)}",
            f"      SEAT_TOKEN: {q(tokens[seat])}",
            f"      SEAT_BRIEFING: {q(json.dumps(briefings[seat], sort_keys=True))}",
            '      WORLD_URL: "http://world:8080"',
        ]
    lines += ["", "networks:", "  play: {}", ""]
    return "\n".join(lines)


def task_toml(name: str, meta: dict[str, Any], seats: list[str], title: str,
              scripted: tuple[str, ...] = ()) -> str:
    lines = [
        'schema_version = "1.4"',
        'artifacts = ["/logs/world/events.jsonl", "/logs/world/messages.jsonl"]',
        "",
        "[task]",
        f'name = "{name}"',
        f'description = "Flotilla {title}: {meta["condition"]} condition, instance {meta["instance"]}."',
        f'keywords = ["flotilla", "seats", "multi-agent", "{meta["family"]}", "{meta["condition"]}"]',
        "",
        "[metadata]",
        *(f"{k} = {json.dumps(v)}" for k, v in meta.items()),
        "",
        "[verifier]",
        "timeout_sec = 120.0",
        "",
        "[agent]",
        f"timeout_sec = {PLAY_TIMEOUT_SEC}",
        "",
        "[environment]",
        "build_timeout_sec = 600.0",
        "",
        *(f"[agents.{seat}]\n" + ("under_test = false\n" if seat in scripted else "") for seat in seats),
    ]
    return "\n".join(lines)


def build(family_name: str, instances: list[int], n_seats: int, difficulty: str, seed: int,
          conditions: tuple[str, ...], out: Path, variant: str = "", scripted: bool = False) -> list[Path]:
    family = load_family(family_name)
    family_file = ROOT / "families" / f"{family_name}.py"
    label = f"{family_name}.{variant}" if variant else family_name  # e.g. schedule.3seat
    built = []
    for i in instances:
        inst = family.generate_scripted(seed, i, difficulty) if scripted else family.generate(seed, i, n_seats, difficulty)
        team_seats = sorted(inst["private"])
        scripted_seats = tuple(inst.get("scripted_seats", ())) if scripted else ()
        for condition in conditions:
            seats = ["p1"] if condition == "solo" else team_seats
            briefings = (
                {"p1": inst["solo_private"]} if condition == "solo" else {s: inst["private"][s] for s in seats}
            )
            for seat in scripted_seats:  # a scripted peer posts its part, honestly or not
                lying = "liar" in condition
                briefings[seat] = {
                    "script": "liar" if lying else "honest",
                    "text": (inst["liar_text"] if lying else inst["private_text"])[seat],
                    "part": (inst["liar_private"] if lying else inst["private"])[seat],
                }
            tokens = {seat: secrets.token_hex(16) for seat in seats}
            admin = secrets.token_hex(16)
            name = f"{label}-{i:03d}-{condition}"
            task_dir = out / label / name
            if task_dir.exists():
                shutil.rmtree(task_dir)
            env = task_dir / "environment"
            (env / "player").mkdir(parents=True)
            (task_dir / "instructions").mkdir()
            (task_dir / "solution").mkdir()
            (task_dir / "tests").mkdir()
            meta = {"family": family_name, "variant": variant, "instance": i, "condition": condition,
                    "n_seats": len(seats), "team_seats": len(team_seats), "difficulty": difficulty, "seed": seed}
            if scripted_seats:
                meta["scripted_seats"] = list(scripted_seats)
            (task_dir / "task.toml").write_text(
                task_toml(f"flotilla/{name}", meta, seats, family.TITLE, scripted_seats)
            )
            for seat in seats:
                (task_dir / "instructions" / f"{seat}.md").write_text(
                    SCRIPTED_INSTRUCTION if seat in scripted_seats else instruction(family, inst, seat, seats, condition)
                )
            (env / "docker-compose.yaml").write_text(compose(condition, seats, tokens, admin, briefings))
            (env / "Dockerfile").write_text(MAIN_DOCKERFILE)
            (env / "player" / "Dockerfile").write_text(PLAYER_DOCKERFILE)
            shutil.copy(ROOT / "world" / "world.py", env / "world.py")
            shutil.copy(ROOT / "world" / "score.py", env / "score.py")
            shutil.copy(family_file, env / "family.py")
            task_json = {"family": family_name, "variant": variant, "instance": i, "condition": condition,
                         "seats": seats,
                         "difficulty": difficulty, "seed": seed, "answer_key": inst["answer_key"]}
            for extra in ("decoy", "witness", "scripted_seats"):
                if extra in inst and scripted:
                    task_json[extra] = inst[extra]
            (env / "task.json").write_text(json.dumps(task_json, indent=2, sort_keys=True) + "\n")
            (task_dir / "tests" / "test.sh").write_text(TEST_SH)
            (task_dir / "solution" / "solve.sh").write_text(SOLVE_SH)
            shutil.copy(ROOT / "world" / "oracle.py", task_dir / "solution" / "oracle.py")
            shutil.copy(family_file, task_dir / "solution" / "family.py")
            for script in (task_dir / "tests" / "test.sh", task_dir / "solution" / "solve.sh"):
                script.chmod(0o755)
            built.append(task_dir)
    return built


def parse_instances(spec: str) -> list[int]:
    result: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            result.extend(range(int(lo), int(hi) + 1))
        elif part:
            result.append(int(part))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--family", required=True)
    parser.add_argument("--instances", default="0-11", help="e.g. 0-11 or 0,3,5")
    parser.add_argument("--seats", type=int, default=2)
    parser.add_argument("--difficulty", default="easy")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--out", type=Path, default=ROOT / "tasks")
    parser.add_argument("--variant", default="", help="label for a variant, e.g. 3seat or hard: "
                        "task names become flotilla/<family>.<variant>-<iii>-<condition>")
    parser.add_argument("--scripted", action="store_true",
                        help="use the family's generate_scripted(): a scripted peer seat (not under test); "
                        "conditions talk (honest peer) and talk-liar (lying peer)")
    args = parser.parse_args()
    conditions = tuple(c for c in args.conditions.split(",") if c)
    if args.scripted and args.conditions == ",".join(CONDITIONS):
        conditions = ("talk", "talk-liar")
    built = build(args.family, parse_instances(args.instances), args.seats, args.difficulty, args.seed,
                  conditions, args.out, args.variant or ("liar" if args.scripted else ""), args.scripted)
    print(f"built {len(built)} tasks under {args.out}")


if __name__ == "__main__":
    main()
