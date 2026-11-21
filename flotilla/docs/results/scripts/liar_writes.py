"""Who wrote the final answer in the scripted-liar trials, and did it matter?

    python3 flotilla/docs/results/scripts/liar_writes.py .flotilla-local/jobs

Reads wave c3 (`c3-*-3seat`): result.json (checked reward), verifier/score.json (deceived),
verifier/world-state.json (submissions in order) and the task's environment/task.json (answer key).
p1 holds the witness's true calendar, so only p1 can catch the lie; p2 cannot; p3 is the scripted peer.
Standard library only.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from collections.abc import Iterable
from math import comb
from pathlib import Path
from typing import Literal, NamedTuple

WriterOutcome = tuple[str, str]
CorruptedAs = Literal["correct", "deceived"] | None


class WriterTest(NamedTuple):
    pairs: int
    p1_correct: int
    p2_correct: int
    p_value: float | None


def norm(answer):
    if not isinstance(answer, dict):
        return None
    day = str(answer.get("day", "")).strip()[:3].title()
    match = re.fullmatch(
        r"(\d{1,2})(?::00(?::00)?)?", str(answer.get("start", "")).strip()
    )
    return (day, int(match.group(1))) if match else None


def paired_writer_test(
    pairs: Iterable[tuple[WriterOutcome, WriterOutcome]],
    *,
    corrupted_as: CorruptedAs = None,
) -> WriterTest:
    """Exact paired test, conditional on each instance's two writers and two outcomes.

    Only pairs with different writers and different outcomes inform the association.
    Under the null, either writer is equally likely to have the correct outcome in each such pair.
    """
    usable = p1 = p2 = 0
    for (first_seat, first), (second_seat, second) in pairs:
        if first == "corrupted" or second == "corrupted":
            if corrupted_as is None:
                continue
            first = corrupted_as if first == "corrupted" else first
            second = corrupted_as if second == "corrupted" else second
        usable += 1
        if first_seat == second_seat or first == second:
            continue
        if (first_seat if first == "correct" else second_seat) == "p1":
            p1 += 1
        else:
            p2 += 1
    n = p1 + p2
    p = (
        min(1.0, 2 * sum(comb(n, i) for i in range(min(p1, p2) + 1)) / (1 << n))
        if usable
        else None
    )
    return WriterTest(usable, p1, p2, p)


def show_writer_test(label: str, test: WriterTest) -> None:
    p = "n/a" if test.p_value is None else f"{test.p_value:.4f}"
    print(
        f"  {label}: {test.pairs} pairs, {test.p1_correct + test.p2_correct} informative "
        f"(p1 correct: {test.p1_correct}, p2 correct: {test.p2_correct}), two-sided p = {p}"
    )


def main() -> None:
    jobs = Path(sys.argv[1] if len(sys.argv) > 1 else ".flotilla-local/jobs")
    table = collections.Counter()
    deceived_paths = collections.Counter()
    by_instance: dict[tuple[str, str], dict[str, WriterOutcome]] = (
        collections.defaultdict(dict)
    )
    unusable = 0
    for trial in sorted(jobs.glob("c3-*-3seat/*/")):
        try:
            result = json.loads((trial / "result.json").read_text())
        except (OSError, ValueError):
            continue
        match = re.fullmatch(
            r"(.+-\d{3})-(talk-liar(?:-warned)?)", result.get("task_name", "")
        )
        if match is None:
            continue
        try:
            score = json.loads((trial / "verifier" / "score.json").read_text())
            state = json.loads((trial / "verifier" / "world-state.json").read_text())
        except (OSError, ValueError):
            unusable += 1
            continue
        reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get(
            "reward"
        )
        if (
            result.get("finished_at") is None
            or result.get("reward_valid") is False
            or type(reward) not in (int, float)
            or reward not in (0.0, 1.0)
            or (reward == 0.0 and not isinstance(score.get("deceived"), bool))
            or not state.get("submissions")
            or state["submissions"][-1]["seat"] not in ("p1", "p2")
        ):
            unusable += 1
            continue
        final = state["submissions"][-1]["seat"]
        outcome = (
            "correct"
            if reward == 1.0
            else ("deceived" if score["deceived"] else "corrupted")
        )
        arms = by_instance[(trial.parent.name, match[1])]
        if match[2] in arms:
            raise ValueError(
                f"duplicate {match[2]} trial for {match[1]} in {trial.parent.name}"
            )
        arms[match[2]] = (final, outcome)
        table[(final, outcome)] += 1
        if outcome == "deceived":
            task_json = Path(result["task_id"]["path"]) / "environment" / "task.json"
            key = norm(json.loads(task_json.read_text())["answer_key"])
            right_first = key is not None and any(
                norm(s["answer"]) == key for s in state["submissions"]
            )
            deceived_paths[
                (
                    "right answer first" if right_first else "decoy only",
                    f"final by {final}",
                )
            ] += 1
    print(
        "Liar conditions (talk-liar and talk-liar-warned), all models: final writer x outcome"
    )
    for seat in ("p1", "p2"):
        print(
            f"  final by {seat}: "
            + ", ".join(
                f"{o} {table[(seat, o)]}" for o in ("correct", "deceived", "corrupted")
            )
        )
    total = sum(table.values())
    corrupted = sum(table[(seat, "corrupted")] for seat in ("p1", "p2"))
    pairs = [
        (arms["talk-liar"], arms["talk-liar-warned"])
        for arms in by_instance.values()
        if len(arms) == 2
    ]
    print(
        "Exact paired final-writer association test (warned vs unwarned, within model and instance):"
    )
    print(
        f"  {len(pairs)} complete pairs before outcome exclusions; {total - 2 * len(pairs)} unpaired trials."
    )
    print(f"  Trials without a usable outcome or p1/p2 final writer: {unusable}.")
    print(f"  Corrupted trials omitted from primary test: {corrupted}/{total}.")
    show_writer_test("Correct vs deceived", paired_writer_test(pairs))
    show_writer_test(
        "Corrupted counted as deceived",
        paired_writer_test(pairs, corrupted_as="deceived"),
    )
    show_writer_test(
        "Corrupted counted as correct",
        paired_writer_test(pairs, corrupted_as="correct"),
    )
    print("Deceived trials by path:")
    for (path, final), n in sorted(deceived_paths.items()):
        print(f"  {n}: {path}, {final}")


if __name__ == "__main__":
    main()
