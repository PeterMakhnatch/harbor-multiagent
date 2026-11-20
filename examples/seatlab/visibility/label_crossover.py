"""Measure route choice under paired label mappings on the six archived worlds."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, ClassVar

from . import deepseek, listener

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA = "seatlab-listener-label-crossover-v1"
ORIGINAL_SCHEDULE_SHA256 = (
    "b95aa3434271a357cd297d8a56b1715ff8edae1fd4a8566441b4a0995c1dedda"
)
_MAPS = ("original", "complementary")
_SCOPE = (
    "Standalone choices on six archived worlds, not new native trials. "
    "Repeated calls and relabeling do not add independent worlds. The none prompt "
    "has identical text under both maps; only its scoring key changes."
)


@dataclass(frozen=True)
class _CrossoverSchedule(listener._Schedule):
    run_prefix: ClassVar[str] = "mirror"

    source_file_sha256: dict[str, str]

    def receipt(self) -> dict[str, Any]:
        receipt = super().receipt()
        receipt.update(
            schema=SCHEMA,
            original_schedule_sha256=ORIGINAL_SCHEDULE_SHA256,
            source_file_sha256=self.source_file_sha256,
            requests_per_case_per_arm_per_map=receipt.pop("requests_per_case_per_arm"),
            scope=_SCOPE,
        )
        receipt["transport"]["published_revision"] = deepseek.REVISION
        swaps = _original_swaps(self)
        for row, cell in zip(receipt["cells"], self.cells, strict=True):
            row.update(map=_MAPS[_map_index(cell, swaps)], run_id=self.run_id(cell.id))
        return receipt

    def summary(self, raw: bytes) -> dict[str, Any]:
        return {
            **super().summary(raw),
            "schema": SCHEMA,
            "original_schedule_sha256": ORIGINAL_SCHEDULE_SHA256,
        }


def _original_swaps(plan: listener._Schedule) -> dict[str, bool]:
    return {case.id: case.swap for case in plan.cases}


def _map_index(cell: listener._Cell, original_swaps: dict[str, bool]) -> int:
    return int(cell.case.swap != original_swaps[cell.case.id])


def _source_fingerprints(root: Path, paths: tuple[Path, ...]) -> dict[str, str]:
    if not paths:
        raise ValueError("the original listener cohort has no census provenance")
    hashes = {}
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("listener census provenance is outside the archive root")
        hashes[resolved.relative_to(root).as_posix()] = listener._sha(
            resolved.read_bytes()
        )
    return hashes


def build_schedule(root: Path) -> _CrossoverSchedule:
    """Require the published original schedule before building any new treatment."""
    root = root.resolve()
    original = listener.build_schedule(root)
    if listener._sha(original.seal_bytes()) != ORIGINAL_SCHEDULE_SHA256:
        raise ValueError("original listener schedule does not match the pinned SHA-256")
    if len(original.cases) != 6:
        raise ValueError("label crossover requires exactly six original cases")

    cells = []
    for case_idx, case in enumerate(original.cases):
        data = case.data
        if data is None:
            raise ValueError(
                "label crossover requires all six original cases to be eligible"
            )
        mapped_cases = (case, replace(case, swap=not case.swap))
        prompts = tuple(
            {arm: listener._prompt(data, mapped.swap, arm) for arm in listener.ARMS}
            for mapped in mapped_cases
        )
        for rep_idx in range(listener.REPEATS):
            first_map = (case_idx + rep_idx) % 2
            for map_idx in (first_map, 1 - first_map):
                arms = listener.ARMS
                if (case_idx + rep_idx + map_idx) % 2:
                    arms = arms[::-1]
                for arm in arms:
                    cells.append(
                        listener._Cell(
                            len(cells) + 1,
                            mapped_cases[map_idx],
                            rep_idx + 1,
                            arm,
                            prompts[map_idx][arm],
                        )
                    )
    return _CrossoverSchedule(
        original.candidates,
        original.cases,
        tuple(cells),
        original.source_paths,
        _source_fingerprints(root, original.source_paths),
    )


def seal(root: Path, study: Path) -> dict[str, Any]:
    return listener._seal_study(root, study, build_schedule)


def run_cell(root: Path, study: Path, cell_id: int) -> dict[str, Any]:
    return listener._run_cell(root, study, cell_id, build_schedule)


def _pair_comparison(
    pairs: dict[tuple[str, int], dict[str, dict[int, listener.Label | None]]],
) -> tuple[dict[str, dict[str, int]], dict[str, int | float | None]]:
    comparisons = {arm: Counter[str]() for arm in listener.ARMS}
    matched: Counter[str] = Counter(scheduled=len(pairs))
    for by_arm in pairs.values():
        flips = {}
        for arm, by_map in by_arm.items():
            counts = comparisons[arm]
            counts["scheduled"] += 1
            original, complementary = by_map[0], by_map[1]
            if original is None or complementary is None:
                continue
            counts["both_valid"] += 1
            flips[arm] = original != complementary
            counts["letter_flip_preserves_route" if flips[arm] else "same_letter"] += 1
        if len(flips) == len(listener.ARMS):
            matched["all_four_valid"] += 1
            if flips["visible"] and flips["none"]:
                matched["both_flip"] += 1
            elif flips["visible"]:
                matched["visible_only_flips"] += 1
            elif flips["none"]:
                matched["none_only_flips"] += 1
            else:
                matched["neither_flips"] += 1
    complete = matched["all_four_valid"]
    return (
        {
            arm: {
                key: counts[key]
                for key in (
                    "scheduled",
                    "both_valid",
                    "letter_flip_preserves_route",
                    "same_letter",
                )
            }
            for arm, counts in comparisons.items()
        },
        {
            **{
                key: matched[key]
                for key in (
                    "scheduled",
                    "all_four_valid",
                    "both_flip",
                    "visible_only_flips",
                    "none_only_flips",
                    "neither_flips",
                )
            },
            "visible_minus_none_flip_fraction": (
                (matched["visible_only_flips"] - matched["none_only_flips"]) / complete
                if complete
                else None
            ),
        },
    )


def report(root: Path, study: Path) -> dict[str, Any]:
    """Publish counts only; pair by original world, replicate, and visibility."""
    plan, sealed, outcomes = listener._report_inputs(root, study, build_schedule)
    swaps = _original_swaps(plan)
    counts = {(arm, m): Counter[str]() for arm in listener.ARMS for m in _MAPS}
    prompts: dict[tuple[str, str], set[str]] = {key: set() for key in counts}
    pairs: dict[tuple[str, int], dict[str, dict[int, listener.Label | None]]] = {}
    models: Counter[str] = Counter()
    valid_worlds = set()
    for cell in plan.cells:
        outcome = outcomes[cell.id]
        map_idx = _map_index(cell, swaps)
        key = (cell.arm, _MAPS[map_idx])
        listener._count_outcome(counts[key], cell, outcome)
        prompts[key].add(cell.prompt)
        pairs.setdefault((cell.case.id, cell.replicate), {}).setdefault(cell.arm, {})[
            map_idx
        ] = outcome.label
        if outcome.label is not None:
            valid_worlds.add(cell.case.uuid)
        if outcome.served_model is not None:
            models[outcome.served_model] += 1
    by_visibility, matched = _pair_comparison(pairs)
    return {
        **plan.summary(sealed),
        "scope": _SCOPE,
        "unique_worlds": len({case.uuid for case in plan.cases}),
        "worlds_with_valid_responses": len(valid_worlds),
        "distinct_prompts": len(set().union(*prompts.values())),
        "served_model_counts": dict(models),
        "by_visibility_and_map": {
            arm: {
                m: {
                    **listener._choice_summary(counts[arm, m]),
                    "correct_denominator": counts[arm, m]["valid"],
                    "distinct_prompts": len(prompts[arm, m]),
                }
                for m in _MAPS
            }
            for arm in listener.ARMS
        },
        "map_pairs_by_visibility": by_visibility,
        "visible_vs_none_matched_crossover": matched,
    }


def main(argv: list[str] | None = None) -> int:
    return listener._main(
        argv,
        description=__doc__,
        name="label-crossover",
        build=build_schedule,
        summarize=report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
