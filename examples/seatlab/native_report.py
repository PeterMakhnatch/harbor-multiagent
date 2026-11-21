from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import Counter
from math import fsum, isfinite
from pathlib import Path
from typing import Any, NamedTuple, cast
from uuid import UUID

type Record = dict[str, Any]
type Scores = dict[tuple[int, int, str], float | None]

_EPISODES = 6
_MAX_BYTES = 1024 * 1024
_ALIASES = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6")
_METRICS = (
    "opened_report_changes",
    "total_report_changes",
    "opened_stock_changes",
    "opened_sensitive",
    "source_copy_errors",
)
_STATES = (
    "complete",
    "unclaimed",
    "reserved_without_trial",
    "claimed",
    "native_failed",
    "unverified",
)
_TOKENS = ("input_tokens", "cache_tokens", "output_tokens")
_ENGINES = ("deepseek-flash", "deepseek-v4.1-flash-1017")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PANELS: dict[str, tuple[tuple[str, str], tuple[str, ...]]] = {
    "attention": (("named", "count"), ("inventory", "select", "decide")),
    "briefing": (("brief", "masked"), ("inventory", "brief", "push", "decide")),
}
_REPORTS = {f"seatlab.native-{tag}-report/v1": tag for tag in _PANELS}


class PublicReport(NamedTuple):
    tag: str
    transport: str
    summary: Record
    observed_worlds: int


def _grid(tag: str) -> tuple[Record, ...]:
    if tag == "attention":
        from .attention import protocol as attention

        return attention.grid()
    from .briefing import protocol as briefing

    return briefing.grid()


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _count(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _index(grid: dict[str, Record], entries: object) -> dict[str, Record]:
    if not isinstance(entries, list) or len(entries) != len(grid):
        raise ValueError("expected exactly the registered cells")
    index: dict[str, Record] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("invalid registered cell")
        entry = cast(Record, raw)
        cell_id = entry.get("cell_id")
        if not isinstance(cell_id, str) or cell_id not in grid or cell_id in index:
            raise ValueError("unknown or duplicate registered cell")
        if any(
            type(entry.get(key)) is not type(value) or entry[key] != value
            for key, value in grid[cell_id].items()
        ):
            raise ValueError("mislabelled registered cell")
        index[cell_id] = entry
    return index


def _trial_id(value: object) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if parsed.int != 0 and str(parsed) == value:
                return value
    raise ValueError("expected a canonical native trial UUID")


def _reward(outcome: Record) -> float:
    actions = outcome.get("actions")
    if not isinstance(actions, list) or len(actions) != _EPISODES:
        raise ValueError("expected six native actions")
    ids: set[int] = set()
    for action in actions:
        if (
            not isinstance(action, dict)
            or not _count(action.get("id"), _EPISODES - 1)
            or action["id"] in ids
            or action.get("choice") not in ("A", "B", "C", "D")
            or type(action.get("optimal")) is not bool
            or type(action.get("feasible")) is not bool
            or (action["optimal"] and not action["feasible"])
        ):
            raise ValueError("invalid or duplicate native action")
        ids.add(action["id"])
    score = outcome.get("native_score")
    expected = sum(row["optimal"] for row in actions) / _EPISODES
    if type(score) not in (int, float) or score != expected:
        raise ValueError("native reward differs from the recorded actions")
    return expected


def _metrics(outcome: Record, briefing: bool) -> None:
    opened = outcome.get("opened")
    if (
        not isinstance(opened, list)
        or len(opened) != 2
        or opened[0] == opened[1]
        or any(alias not in _ALIASES for alias in opened)
    ):
        raise ValueError("expected two distinct registered aliases")
    values = [outcome.get(key) for key in _METRICS]
    if not all(
        _count(value, limit)
        for value, limit in zip(values, (2, _EPISODES, 2, 2, _EPISODES), strict=True)
    ):
        raise ValueError("invalid native observation counts")
    report, total, stock, sensitive, errors = cast(list[int], values)
    if (
        not 0 <= total - report <= _EPISODES - 2
        or sensitive > stock
        or abs(report - stock) > errors
    ):
        raise ValueError("invalid native observation counts")
    if briefing and (
        not _count(outcome.get("brief_correct"), _EPISODES)
        or type(outcome.get("brief_slots")) is not int
        or outcome["brief_slots"] != _EPISODES
    ):
        raise ValueError("invalid briefing card counts")


def _turns(
    outcome: Record, cell_id: str, stages: tuple[str, ...], transport: str | None
) -> None:
    turns = outcome.get("turns")
    if not isinstance(turns, list) or len(turns) != len(stages):
        raise ValueError("unexpected native turn count")
    for turn, stage in zip(turns, stages, strict=True):
        role = "inventory" if stage in ("inventory", "push") else "dispatch"
        if (
            not isinstance(turn, dict)
            or turn.get("run") != f"{cell_id}-{stage}"
            or turn.get("stage") != stage
            or turn.get("role") != role
            or turn.get("status") != "received"
            or not _digest(turn.get("prompt_sha256"))
            or not _digest(turn.get("response_sha256"))
        ):
            raise ValueError("invalid native turn identity or digest")
        usage = turn.get("usage")
        if usage is None and transport != "direct":
            continue
        if transport == "commands" or not isinstance(usage, dict):
            raise ValueError("invalid model usage for the native transport")
        token_usage = cast(Record, usage)
        if (
            token_usage.keys() != {"served_model", *_TOKENS}
            or not isinstance(token_usage["served_model"], str)
            or token_usage["served_model"].casefold() not in _ENGINES
            or any(
                type(token_usage[key]) is not int or token_usage[key] < 0
                for key in _TOKENS
            )
            or token_usage["cache_tokens"] > token_usage["input_tokens"]
        ):
            raise ValueError("invalid model usage for the native transport")


def _primary(scores: Scores, arms: tuple[str, str]) -> Record:
    seeds = sorted({seed for seed, _, _ in scores})
    repetitions = sorted({rep for _, rep, _ in scores})
    blocks: list[Record] = []
    lower, upper = [], []
    for seed in seeds:
        deltas = []
        for rep in repetitions:
            treatment, control = (scores[seed, rep, arm] for arm in arms)
            if treatment is not None and control is not None:
                deltas.append(treatment - control)
            low_t, high_t = (0.0, 1.0) if treatment is None else (treatment, treatment)
            low_c, high_c = (0.0, 1.0) if control is None else (control, control)
            lower.append(low_t - high_c)
            upper.append(high_t - low_c)
        blocks.append(
            {
                "seed": seed,
                "matched_pairs": len(deltas),
                "mean": fsum(deltas) / len(deltas) if deltas else None,
            }
        )
    means = [block["mean"] for block in blocks if block["mean"] is not None]
    pairs = len(lower)
    return {
        "contrast": f"{arms[0]}_minus_{arms[1]}_native_score",
        "estimate": fsum(means) / len(seeds) if len(means) == len(seeds) else None,
        "matched_pairs": sum(block["matched_pairs"] for block in blocks),
        "registered_pairs": pairs,
        "registered_seeds": len(seeds),
        "paired_seeds": [block["seed"] for block in blocks if block["matched_pairs"]],
        "seed_blocks": blocks,
        "registered_bounds": {
            "lower": fsum(lower) / pairs,
            "upper": fsum(upper) / pairs,
        },
    }


def _secondary(outcomes: list[Record], briefing: bool) -> Record:
    counts = dict.fromkeys(_METRICS, 0)
    slots = optimal = feasible = cases = unreported = 0
    models: Counter[str] = Counter()
    for outcome in outcomes:
        for field in counts:
            counts[field] += outcome[field]
        slots += len(outcome["opened"])
        cases += len(outcome["actions"])
        optimal += sum(action["optimal"] for action in outcome["actions"])
        feasible += sum(action["feasible"] for action in outcome["actions"])
        usage = outcome["turns"][0].get("usage")
        if usage is None:
            unreported += 1
        else:
            models[usage["served_model"]] += 1
    result: Record = {
        "observed_cells": len(outcomes),
        "opened_slots": slots,
        **counts,
        "opened_report_change_rate": counts["opened_report_changes"] / slots
        if slots
        else None,
        "cases": cases,
        "optimal": optimal,
        "feasible": feasible,
        "inventory_served_models": dict(models),
        "inventory_model_unreported": unreported,
    }
    if briefing:
        correct = sum(outcome["brief_correct"] for outcome in outcomes)
        result.update(
            brief_correct=correct,
            brief_slots=cases,
            brief_correct_rate=correct / cases if cases else None,
        )
    return result


def _summarize(
    tag: str, cells: object, rows: object, transport: str | None = None
) -> tuple[Record, int]:
    arms, stages = _PANELS[tag]
    briefing = tag == "briefing"
    grid = {cell["cell_id"]: cell for cell in _grid(tag)}
    _index(grid, cells)
    scores: Scores = {}
    by_arm: dict[str, list[Record]] = {arm: [] for arm in arms}
    fingerprints: dict[int, str] = {}
    trials: set[str] = set()
    for cell_id, row in _index(grid, rows).items():
        state, outcome = row.get("state"), row.get("outcome")
        valid_outcome = (
            isinstance(outcome, dict) if state == "complete" else outcome is None
        )
        if state not in _STATES or "outcome" not in row or not valid_outcome:
            raise ValueError("native row state and outcome disagree")
        seed, arm = row["seed"], row["arm"]
        score = None
        if outcome is not None:
            trial = _trial_id(outcome.get("trial_id"))
            fingerprint = outcome.get("fingerprint")
            if (
                trial in trials
                or not _digest(fingerprint)
                or fingerprints.setdefault(seed, fingerprint) != fingerprint
            ):
                raise ValueError(
                    "native trial UUID or world fingerprint is inconsistent"
                )
            trials.add(trial)
            score = _reward(outcome)
            _metrics(outcome, briefing)
            _turns(outcome, cell_id, stages, transport)
            by_arm[arm].append(outcome)
        scores[seed, row["repetition"], arm] = score
    if len(set(fingerprints.values())) != len(fingerprints):
        raise ValueError("different registered worlds share a fingerprint")
    return {
        "registered_cells": len(grid),
        "observed_cells": len(trials),
        "missing_cells": len(grid) - len(trials),
        "primary": _primary(scores, arms),
        "secondary": {arm: _secondary(by_arm[arm], briefing) for arm in arms},
    }, len(fingerprints)


def summarize_attention(cells: list[Record], rows: list[Record]) -> Record:
    return _summarize("attention", cells, rows)[0]


def summarize_briefing(cells: list[Record], rows: list[Record]) -> Record:
    return _summarize("briefing", cells, rows)[0]


def _unique(pairs: list[tuple[str, Any]]) -> Record:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate JSON key")
    return obj


def _finite(text: str) -> float:
    value = float(text)
    if not isfinite(value):
        raise ValueError("nonfinite JSON number")
    return value


def _read_json(path: Path) -> Any:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not a regular file")
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("report exceeds size limit")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_float=_finite,
            parse_constant=_finite,
        )
    except (OSError, ValueError, RecursionError):
        raise ValueError(
            "expected a bounded JSON file with unique keys and finite numbers"
        ) from None


def read_report(path: Path, *, expected: str | None = None) -> PublicReport:
    report = _read_json(path)
    schema = report.get("schema") if isinstance(report, dict) else None
    if not isinstance(schema, str) or schema not in _REPORTS:
        raise ValueError("unknown native report schema")
    tag = _REPORTS[schema]
    if expected is not None and expected != tag:
        raise ValueError("unexpected native report register")
    plan = report.get("plan")
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != f"seatlab.native-{tag}-plan/v1"
    ):
        raise ValueError("unknown native plan schema")
    transport, config = plan.get("transport"), plan.get("command_config_sha256")
    if (
        transport not in ("direct", "commands")
        or "command_config_sha256" not in plan
        or (transport == "direct" and config is not None)
        or (transport == "commands" and not _digest(config))
    ):
        raise ValueError("invalid native transport or command digest")
    cells = plan.get("cells")
    calls = len(cells) * len(_PANELS[tag][1]) if isinstance(cells, list) else 0
    calls = calls if transport == "direct" else 0
    if (
        type(plan.get("episodes")) is not int
        or plan["episodes"] != _EPISODES
        or type(plan.get("max_direct_calls")) is not int
        or plan["max_direct_calls"] != calls
    ):
        raise ValueError("invalid native plan counts")
    for key in ("sources", "seat_sources"):
        stamps = plan.get(key)
        if not isinstance(stamps, dict) or not stamps:
            raise ValueError("missing native plan source digests")
        if any(not _digest(stamp) for stamp in stamps.values()):
            raise ValueError("invalid native plan source digests")
    summary, worlds = _summarize(tag, cells, report.get("rows"), transport)
    try:
        published = json.dumps(report.get("summary"), sort_keys=True)
    except RecursionError:
        raise ValueError("published summary nesting is too deep") from None
    if json.dumps(summary, sort_keys=True) != published:
        raise ValueError("published reward summary differs")
    return PublicReport(tag, transport, summary, worlds)


def _print_panel(number: int, report: PublicReport) -> None:
    summary = report.summary
    primary = summary["primary"]
    treatment, control = _PANELS[report.tag][0]
    transport = (
        "direct (DeepSeek API usage recorded)"
        if report.transport == "direct"
        else "commands (model usage unknown)"
    )
    estimate = primary["estimate"]
    effect = "unknown" if estimate is None else f"{estimate:+.4f}"
    bounds = primary["registered_bounds"]
    print(f"Report {number}: {report.tag} | model transport: {transport}")
    print(
        f"  Public rows checked: {summary['observed_cells']}/{summary['registered_cells']}"
        f" teams; {report.observed_worlds}/{primary['registered_seeds']} worlds with"
        f" outcomes; {summary['missing_cells']} unknown teams."
    )
    print(
        f"  {treatment} minus {control} native score: {effect}; "
        f"{primary['matched_pairs']}/{primary['registered_pairs']} matched pairs"
        f" in {len(primary['paired_seeds'])} worlds."
    )
    print(
        f"  Finite missing-data bounds: [{bounds['lower']:+.4f}, {bounds['upper']:+.4f}]"
        " (not a confidence interval)."
    )


def verified_main(argv: list[str], *, expected: str) -> int:
    parser = argparse.ArgumentParser(
        description="Verify one public native report.", allow_abbrev=False
    )
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = read_report(args.report, expected=expected)
    except ValueError as error:
        print(f"Cannot verify {expected} report: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result.summary, sort_keys=True, allow_nan=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check public native SeatLab report arithmetic.", allow_abbrev=False
    )
    parser.add_argument(
        "--json", action="store_true", help="Print recomputed summaries as JSON."
    )
    parser.add_argument("files", metavar="FILE", type=Path, nargs="+")
    args = parser.parse_args(argv)
    reports = []
    for number, path in enumerate(args.files, 1):
        try:
            reports.append(read_report(path))
        except ValueError as error:
            print(f"Report {number}: {error}", file=sys.stderr)
            return 1
    if args.json:
        summaries = [report.summary for report in reports]
        payload = summaries[0] if len(reports) == 1 else summaries
        print(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False))
    else:
        for number, report in enumerate(reports, 1):
            _print_panel(number, report)
        print(
            "Public arithmetic only. This does not verify live worlds, "
            "model identity, or billing."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
