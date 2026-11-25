"""Make plan token_profiles from retained playtests or a zero-model Terminus 2 estimate."""

from __future__ import annotations

import argparse
import functools
import json
import math
import statistics
import sys
import tomllib
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import pricing

KEYS = ("miss", "hit", "out")
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class TurnModel:
    overhead: float
    growth: float
    cache_fraction: float
    output_per_turn: float

    def predict(self, context: float, turns: int) -> dict[str, float]:
        total = turns * max(1, context + self.overhead) + self.growth * turns * (turns - 1) / 2
        return {"miss": total * (1 - self.cache_fraction), "hit": total * self.cache_fraction,
                "out": self.output_per_turn * turns}


# Frozen from lab/.flotilla-local/jobs on 2026-11-25 by --fit-history.
CALIBRATED = TurnModel(overhead=478.25, growth=603.2, cache_fraction=0.7008, output_per_turn=307.0)
CALIBRATION = ("2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE "
               "miss 36.36%, hit 39.32%, out 39.67%")


def count(usage: dict, key: str, *, optional: bool = False) -> int:
    value = usage.get(key, 0 if optional else None)
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid or missing usage field {key}")
    return value


def log_tokens(path: Path) -> tuple[dict[str, int], str]:
    """Only aggregate completion records, never intermediate assistant usage or reasoning twice."""
    codex = []
    claude = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn.failed" or (kind == "result" and event.get("is_error")):
            raise ValueError(f"failed model turn in {path}")
        if kind not in ("turn.completed", "result"):
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            raise TypeError(f"no usage in {kind} in {path}")
        total, out = count(usage, "input_tokens"), count(usage, "output_tokens")
        if kind == "turn.completed":
            hit = count(usage, "cached_input_tokens", optional=True)
            if hit > total:
                raise ValueError(f"cached input exceeds total in {path}")
            codex.append((total - hit, hit, out))
        else:
            hit = count(usage, "cache_read_input_tokens", optional=True)
            miss = total + count(usage, "cache_creation_input_tokens", optional=True)
            claude.append((miss, hit, out))
    if codex and not claude:
        rows, source = codex, "Codex turn.completed usage"
    elif len(claude) == 1 and not codex:
        rows, source = claude, "Claude final result usage (cache creation at miss rate)"
    else:
        raise ValueError(f"unknown or ambiguous log format (no complete usage) in {path}")
    tokens = dict(zip(KEYS, (sum(row[i] for row in rows) for i in range(3)), strict=True))
    if not sum(tokens.values()):
        raise ValueError(f"no nonzero token usage in {path}")
    return tokens, source


def task_meta(path: Path) -> dict:
    return tomllib.loads((path / "task.toml").read_text())["metadata"]


def check_task(path: Path, family: str, level: str) -> dict:
    meta = task_meta(path)
    kind = pricing.kind_of(path.parent.name)
    if kind != family or meta["difficulty"] != level:
        raise ValueError(f"{path}: expected kind {family} and level {level}, got {kind}/{meta['difficulty']}")
    return meta


def playtest_samples(dirs: list[Path], family: str, level: str) -> list[tuple[str, str, dict, str]]:
    samples = []
    for directory in dirs:
        result = json.loads((directory / "result.json").read_text())
        task = Path(result["task"])
        meta = check_task(task, family, level)
        if result.get("seat_failures") or result.get("timed_out") or (result.get("score") or {}).get("error"):
            raise ValueError(f"{directory}: playtest recorded a failure")
        found = False
        for seat, info in result["seats"].items():
            if info["mode"] != "external":
                continue
            if info.get("exit_code") != 0 or info.get("timed_out") or info.get("error"):
                raise ValueError(f"{directory}/{seat}: external seat did not finish successfully")
            tokens, source = log_tokens(directory / "seats" / seat / "seat.log")
            samples.append((meta["condition"], seat, tokens, source))
            found = True
        if not found:
            raise ValueError(f"{directory}: no external seats")
    return samples


@functools.lru_cache(maxsize=4096)
def context_tokens(path: Path, seat: str) -> float:
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    services = yaml.load((path / "environment/docker-compose.yaml").read_text(), Loader=loader)["services"]
    briefing = services[seat]["environment"]["SEAT_BRIEFING"].replace("$$", "$")
    instruction = (path / "instructions" / f"{seat}.md").read_text()
    return (len(instruction) + len(briefing)) / CHARS_PER_TOKEN


def model_samples(dirs: list[Path], family: str, level: str, calls_per_turn: float,
                  schedule_seed: int) -> list[tuple[str, str, dict, str]]:
    import sim

    samples = []
    for task in dirs:
        meta = check_task(task, family, level)
        agents = tomllib.loads((task / "task.toml").read_text())["agents"]
        result = sim.simulate_built(task, "careful", schedule_seed)
        if result.get("invalid") or result.get("stalled") or result["reward"] != 1:
            raise ValueError(f"{task}: careful policy did not pass")
        for seat, options in agents.items():
            if not options.get("under_test", True):
                continue
            calls = result["requests"].get(seat, 0)
            turns = max(1, math.ceil(calls / calls_per_turn))
            tokens = CALIBRATED.predict(context_tokens(task, seat), turns)
            print(f"{meta['condition']} {seat}: {calls} careful calls, {turns} estimated turns, "
                  f"{context_tokens(task, seat):.1f} context tokens", file=sys.stderr)
            source = (f"Terminus 2 turn model; {CHARS_PER_TOKEN} chars/token (instruction + briefing); "
                      f"{calls_per_turn:g} careful world calls/turn, ceil, min 1; schedule {schedule_seed}; "
                      f"overhead {CALIBRATED.overhead:g}, growth {CALIBRATED.growth:g}, "
                      f"cache fraction {CALIBRATED.cache_fraction:g}, output/turn {CALIBRATED.output_per_turn:g}; "
                      f"linear context growth, no compaction, files/replies in fitted growth; {CALIBRATION}")
            samples.append((meta["condition"], seat, tokens, source))
    return samples


def profiles(samples: list[tuple[str, str, dict, str]], family: str, level: str, uncertainty: float) -> list[dict]:
    groups = defaultdict(list)
    for arm, seat, tokens, source in samples:
        groups[arm, seat].append((tokens, source))
    rows = []
    for (arm, seat), group in sorted(groups.items()):
        means = {key: round(statistics.mean(tokens[key] for tokens, _ in group), 2) for key in KEYS}
        sources = "; ".join(sorted({source for _, source in group}))
        rows.append({"kind": family, "level": level, "arm": arm, "role": seat, **means,
                     "source": sources, "uncertainty": uncertainty, "n": len(group)})
    pricing.validate_profiles(rows)
    return rows


@dataclass(frozen=True)
class FitSample:
    context: float
    turns: int
    first: int
    total: int
    cached: int
    out: int


def calibration_rows(jobs: Path) -> list[FitSample]:
    """Same finished/exception-free filter as pricing.read_history, with per-turn ATIF evidence."""
    rows = []
    for path in sorted(jobs.glob("*/*/result.json")):
        try:
            result = json.loads(path.read_text())
            kind = pricing._trial_kind(result)
            if not kind or not kind.startswith("probe.") or not result.get("finished_at"):
                continue
            seats = result.get("seat_results") or []
            if result.get("exception_info") is not None or any(
                row.get("exception_info") is not None for row in seats + (result.get("step_results") or [])
            ):
                continue
            config = result["config"]
            task = Path(config["task"]["path"])
            model_seats = {row["seat"] for row in config["participants"] if row.get("name") == "terminus-2"
                           and row.get("model_name")}
            for seat in seats:
                if seat["seat"] not in model_seats:
                    continue
                sample = calibration_seat(path.parent, task, seat)
                if sample:
                    rows.append(sample)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return rows


def calibration_seat(trial: Path, task: Path, seat: dict) -> FitSample | None:
    name = seat["seat"]
    trajectory = json.loads((trial / "seats" / name / "agent/trajectory.json").read_text())
    usage = seat["agent_result"]
    tokens = pricing.usage_tokens(usage)
    metrics = [step["metrics"] for step in trajectory["steps"] if step.get("source") == "agent"
               and step.get("metrics") and "prompt_tokens" in step["metrics"]]
    inputs = [count(metric, "prompt_tokens") for metric in metrics]
    total = tokens["miss"] + tokens["hit"]
    if not inputs or total <= 0 or total != sum(inputs):
        return None
    return FitSample(context_tokens(task, name), len(inputs), inputs[0], total, tokens["hit"], tokens["out"])


def fit(rows: list[FitSample]) -> TurnModel:
    multi = [row for row in rows if row.turns > 1]
    if not multi:
        raise ValueError("no finished multi-turn probe trials with matching ATIF and usage")
    med = statistics.median
    overhead = med(row.first - row.context for row in rows)
    growth = max(0, med((row.total - row.turns * row.first) / (row.turns * (row.turns - 1) / 2) for row in multi))
    cached = med(row.cached / row.total for row in rows)
    output = med(row.out / row.turns for row in rows)
    return TurnModel(round(overhead, 2), round(growth, 2), round(cached, 4), round(output, 2))


def fit_errors(rows: list[FitSample], model: TurnModel) -> dict[str, float]:
    """In-sample weighted absolute percentage error by token category (not a confidence interval)."""
    errors, totals = dict.fromkeys(KEYS, 0.0), dict.fromkeys(KEYS, 0.0)
    for row in rows:
        prediction = model.predict(row.context, row.turns)
        actual = {"miss": row.total - row.cached, "hit": row.cached, "out": row.out}
        for key in KEYS:
            errors[key] += abs(prediction[key] - actual[key])
            totals[key] += actual[key]
    return {key: round(100 * errors[key] / totals[key], 2) for key in KEYS if totals[key]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-playtest", nargs="+", type=Path, metavar="DIR")
    source.add_argument("--model-turns", nargs="+", type=Path, metavar="TASK_DIR")
    source.add_argument("--fit-history", type=Path, metavar="JOBS_DIR", help="reproduce the frozen calibration")
    parser.add_argument("--family", help="task kind (the label before its first dash)")
    parser.add_argument("--level", help="built task difficulty")
    parser.add_argument("--uncertainty", type=float, default=2.0)
    parser.add_argument("--calls-per-turn", type=float, default=1.0)
    parser.add_argument("--schedule-seed", type=int, default=0)
    parser.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    try:
        if args.fit_history:
            rows = calibration_rows(args.fit_history)
            model = fit(rows)
            payload = {"n": len(rows), "constants": asdict(model), "fit_wape_percent": fit_errors(rows, model)}
        else:
            if not args.family or not args.level:
                parser.error("--family and --level are required for profiles")
            if not math.isfinite(args.calls_per_turn) or args.calls_per_turn <= 0:
                parser.error("--calls-per-turn must be finite and positive")
            samples = (playtest_samples(args.from_playtest, args.family, args.level) if args.from_playtest else
                       model_samples(args.model_turns, args.family, args.level, args.calls_per_turn, args.schedule_seed))
            payload = {"token_profiles": profiles(samples, args.family, args.level, args.uncertainty)}
            if args.model_turns:
                print(CALIBRATION, file=sys.stderr)
        text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
        if args.out:
            args.out.write_text(text)
        else:
            print(text, end="")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"no profile: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
