import argparse
import json
import re
from collections.abc import Iterable
from itertools import product
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, TypeGuard

_SCHEMA = "seatlab.peer-choice-study/v1"
_SOURCE_REPORT = (
    Path(__file__).with_name("results") / "public-receiver-panel-native.json"
)
_ROSTER = {
    **{
        f"calendar/rx-w{world:02d}-d{direction}-r{repeat}-{arm}": ("calendar", world, 1)
        for world in (0, 4, 8)
        for direction, repeat, arm in product(range(2), range(2), ("blind", "peer"))
    },
    **{f"dispatch/{world}": ("dispatch", world, 6) for world in range(6801, 6809)},
}
_ROW_FIELDS = {
    "id",
    "task",
    "world",
    "units",
    "source_prompt_sha256",
    "drafts",
    "reviews",
}
_DRAFT_FIELDS = {"reply_sha256", "action_sha256", "correct"}
_REVIEW_FIELDS = {
    "order",
    "repeat",
    "review_prompt_sha256",
    "reply_sha256",
    "pick",
    "status",
}
_REVIEW_SLOTS = ((0, 0), (0, 1), (1, 0), (1, 1))
_SHA256 = re.compile(r"[0-9a-f]{64}")


class _Draft(NamedTuple):
    action_sha256: str
    correct: int


class _Review(NamedTuple):
    prompt_sha256: str
    status: str
    pick: int | None
    physical: int | None


class _Source(NamedTuple):
    task: str
    world: int
    units: int
    grades: tuple[int, int]
    reviews: tuple[_Review, ...]


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_keys,
        parse_constant=_reject_constant,
    )


def _is_sha256(value: object) -> TypeGuard[str]:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _source_prompts() -> dict[str, str]:
    report = _read_json(_SOURCE_REPORT)
    if (
        type(report) is not dict
        or report.get("schema") != "seatlab.public-receiver-panel/v1"
        or type(report.get("rows")) is not list
        or len(report["rows"]) != len(_ROSTER)
    ):
        raise ValueError("invalid frozen receiver panel")
    prompts: dict[str, str] = {}
    for row in report["rows"]:
        if type(row) is not dict:
            raise ValueError("invalid frozen receiver panel row")
        identifier, digest = row.get("id"), row.get("prompt_sha256")
        if (
            type(identifier) is not str
            or identifier not in _ROSTER
            or identifier in prompts
            or not _is_sha256(digest)
        ):
            raise ValueError("invalid frozen receiver panel binding")
        prompts[identifier] = digest
    return prompts


def _draft(value: Any, units: int) -> _Draft:
    if type(value) is not dict or value.keys() != _DRAFT_FIELDS:
        raise ValueError("invalid draft fields")
    reply, action, correct = (
        value["reply_sha256"],
        value["action_sha256"],
        value["correct"],
    )
    if not _is_sha256(reply) or not _is_sha256(action):
        raise ValueError("invalid draft digest")
    if type(correct) is not int or not 0 <= correct <= units:
        raise ValueError("draft grade is outside the registered units")
    return _Draft(action, correct)


def _review(value: Any) -> tuple[tuple[int, int], _Review]:
    if type(value) is not dict or value.keys() != _REVIEW_FIELDS:
        raise ValueError("invalid review fields")
    order, repeat = value["order"], value["repeat"]
    if (
        type(order) is not int
        or type(repeat) is not int
        or (order, repeat) not in _REVIEW_SLOTS
    ):
        raise ValueError("invalid review order or repeat")
    prompt, reply = value["review_prompt_sha256"], value["reply_sha256"]
    if not _is_sha256(prompt):
        raise ValueError("invalid review prompt digest")
    status, pick = value["status"], value["pick"]
    if status == "valid":
        if not _is_sha256(reply) or type(pick) is not int or pick not in (1, 2):
            raise ValueError("invalid valid-review reply digest or pick")
        physical = (order + pick - 1) % 2
    elif status in ("invalid", "missing"):
        reply_matches_status = (
            _is_sha256(reply) if status == "invalid" else reply is None
        )
        if pick is not None or not reply_matches_status:
            raise ValueError("invalid unavailable-review reply digest or pick")
        physical = None
    else:
        raise ValueError("invalid review status")
    return (order, repeat), _Review(prompt, status, pick, physical)


def _reviews(values: Any, distinct: bool) -> tuple[_Review, ...]:
    if type(values) is not list or len(values) != (
        len(_REVIEW_SLOTS) if distinct else 0
    ):
        raise ValueError(
            "expected 4 reviews for distinct actions and 0 for identical actions"
        )
    if not distinct:
        return ()
    reviews: dict[tuple[int, int], _Review] = {}
    for value in values:
        slot, review = _review(value)
        if slot in reviews:
            raise ValueError("duplicate review order and repeat")
        reviews[slot] = review
    ordered = tuple(reviews[slot] for slot in _REVIEW_SLOTS)
    normal, normal_repeat, reversed_order, reversed_repeat = (
        review.prompt_sha256 for review in ordered
    )
    if (
        normal != normal_repeat
        or reversed_order != reversed_repeat
        or normal == reversed_order
    ):
        raise ValueError(
            "review prompts must match within orders and differ across orders"
        )
    return ordered


def _source(row: dict[str, Any], identifier: str, prompt: str) -> _Source:
    task, world, units = _ROSTER[identifier]
    if (
        row["task"] != task
        or type(row["world"]) is not int
        or row["world"] != world
        or type(row["units"]) is not int
        or row["units"] != units
    ):
        raise ValueError("task, world, or units do not match the registered case")
    digest = row["source_prompt_sha256"]
    if not _is_sha256(digest) or digest != prompt:
        raise ValueError("source prompt digest does not match the registered case")
    drafts = row["drafts"]
    if type(drafts) is not list or len(drafts) != 2:
        raise ValueError("expected exactly 2 ordered drafts")
    first, second = (_draft(draft, units) for draft in drafts)
    distinct = first.action_sha256 != second.action_sha256
    if not distinct and first.correct != second.correct:
        raise ValueError("identical draft actions have different grades")
    reviews = _reviews(row["reviews"], distinct)
    return _Source(task, world, units, (first.correct, second.correct), reviews)


def _validated_sources(rows: list[dict[str, Any]]) -> list[_Source]:
    if type(rows) is not list or len(rows) != len(_ROSTER):
        raise ValueError(f"expected all {len(_ROSTER)} registered source rows")
    prompts = _source_prompts()
    sources: list[_Source] = []
    for index, row in enumerate(rows, start=1):
        if type(row) is not dict or row.keys() != _ROW_FIELDS:
            raise ValueError(f"row {index}: invalid source fields")
        identifier = row["id"]
        if type(identifier) is not str or identifier not in _ROSTER:
            raise ValueError(f"row {index}: unknown case ID")
        if identifier not in prompts:
            raise ValueError(f"{identifier}: duplicate case ID")
        try:
            sources.append(_source(row, identifier, prompts.pop(identifier)))
        except ValueError as exc:
            raise ValueError(f"{identifier}: {exc}") from exc
    return sources


def _agreement(pairs: Iterable[tuple[int | None, int | None]]) -> tuple[int, int]:
    agreed = observed = 0
    for a, b in pairs:
        if a is not None and b is not None:
            agreed += a == b
            observed += 1
    return agreed, observed


def _metrics(rows: list[_Source], *, task_level: bool) -> dict[str, Any]:
    distinct = equal_score_distinct = valid = invalid = missing = first_position = 0
    units = uniform = selected = oracle = 0
    same_agreed = same_observed = opposite_agreed = opposite_observed = 0
    for row in rows:
        a, b = row.grades
        units += row.units
        uniform += a + b
        oracle += max(a, b)
        if not row.reviews:
            selected += 4 * a
            continue
        distinct += 1
        equal_score_distinct += a == b
        for review in row.reviews:
            if review.physical is not None:
                valid += 1
                first_position += review.pick == 1
                selected += row.grades[review.physical]
            elif review.status == "invalid":
                invalid += 1
            else:
                missing += 1
        p0, p1, p2, p3 = (review.physical for review in row.reviews)
        agreed, observed = _agreement(((p0, p1), (p2, p3)))
        same_agreed += agreed
        same_observed += observed
        agreed, observed = _agreement(product((p0, p1), (p2, p3)))
        opposite_agreed += agreed
        opposite_observed += observed
    complete = invalid == missing == 0
    metrics: dict[str, Any] = {
        "sources": len(rows),
        "worlds": len({row.world for row in rows}),
        "distinct_cases": distinct,
        "identical_action_cases": len(rows) - distinct,
        "equal_score_distinct_cases": equal_score_distinct,
        "valid": valid,
        "invalid": invalid,
        "missing": missing,
        "same_order_agreement": {
            "numerator": same_agreed,
            "denominator": same_observed,
        },
        "opposite_order_agreement": {
            "numerator": opposite_agreed,
            "denominator": opposite_observed,
        },
        "first_position_selections": {
            "numerator": first_position,
            "denominator": valid,
        },
        "uniform_correct_2x": uniform,
        "selected_correct_4x": selected if complete else None,
        "oracle_correct": oracle,
        "units": units,
    }
    if task_level:
        if not complete or distinct == 0:
            metrics["status"] = "inconclusive"
        elif 5 * opposite_agreed >= 4 * opposite_observed and selected >= 2 * uniform:
            metrics["status"] = "pass"
        else:
            metrics["status"] = "not_established"
    return metrics


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources = _validated_sources(rows)
    tasks = {}
    for task in ("calendar", "dispatch"):
        group = [source for source in sources if source.task == task]
        metrics = _metrics(group, task_level=True)
        metrics["by_world"] = {
            str(world): _metrics(
                [row for row in group if row.world == world], task_level=False
            )
            for world in sorted({source.world for source in group})
        }
        tasks[task] = metrics
    return {"tasks": tasks}


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Check a public peer-choice receipt. Print the recomputed summary.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        receipt = _read_json(args.receipt)
        if type(receipt) is not dict or receipt.get("schema") != _SCHEMA:
            raise ValueError(f"expected schema {_SCHEMA}")
        summary = summarize(receipt.get("rows"))
        if "summary" in receipt:
            supplied = json.dumps(receipt["summary"], sort_keys=True, allow_nan=False)
            if supplied != json.dumps(summary, sort_keys=True, allow_nan=False):
                raise ValueError("receipt summary does not match recomputed metrics")
    except (OSError, ValueError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    _main()
