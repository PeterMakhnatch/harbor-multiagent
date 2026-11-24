from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypeGuard

from .capsule import encode_view, view_hash

_BAYS = frozenset("ABCD")
_SYSTEM = (
    "Choose one loading bay for each shipment. Each case gives your utility for "
    "each bay and two inventory reports of available bays. Choose an available "
    "bay with the highest possible utility using the reports. Reply only with a "
    'JSON object with a "rows" array. Each row must contain the case "id" and one '
    '"bay" letter. Include each case ID exactly once.'
)


@dataclass(frozen=True)
class OrderArm:
    id: str
    prompt_sha256: str
    messages: list[dict[str, str]]


@dataclass(frozen=True)
class OrderPair:
    block: int
    cases: list[dict[str, Any]]
    chronological: OrderArm
    reversed: OrderArm


def _fields(value: object, *names: str) -> TypeGuard[dict[str, Any]]:
    return (
        type(value) is dict
        and value.keys() == set(names)
        and all(type(key) is str for key in value)
    )


def _bay(value: Any) -> bool:
    return type(value) is str and value in _BAYS


def _available(value: Any) -> bool:
    return (
        type(value) is list
        and len(value) == 2
        and all(map(_bay, value))
        and value[0] != value[1]
    )


def _validate_cases(cases: list[dict[str, Any]]) -> None:
    if type(cases) is not list or not 1 <= len(cases) <= 256:
        raise ValueError("expected 1 to 256 cases")
    for index, case in enumerate(cases):
        if not _fields(case, "id", "utilities", "before", "after", "reports"):
            raise ValueError("invalid case fields")
        if type(case["id"]) is not int or case["id"] != index:
            raise ValueError("case IDs must be consecutive and start at zero")
        ranks = case["utilities"]
        if (
            not _fields(ranks, "A", "B", "C", "D")
            or any(type(rank) is not int for rank in ranks.values())
            or set(ranks.values()) != {1, 2, 3, 4}
        ):
            raise ValueError("invalid dispatch utilities")
        if not _available(case["before"]) or not _available(case["after"]):
            raise ValueError("invalid stock availability")
        reports = case["reports"]
        if type(reports) is not list or len(reports) != 2:
            raise ValueError("expected two inventory reports")
        for revision, report in enumerate(reports, 1):
            if (
                not _fields(report, "revision", "available")
                or type(report["revision"]) is not int
                or report["revision"] != revision
                or not _available(report["available"])
            ):
                raise ValueError("invalid inventory report")


def build_pair(
    block: int, source_sha256: str, cases: list[dict[str, Any]]
) -> OrderPair:
    if type(block) is not int or not 0 <= block < 1024:
        raise ValueError("invalid block ordinal")
    if type(source_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        raise ValueError("invalid source SHA-256")
    _validate_cases(cases)
    cases = deepcopy(cases)

    def arm(name: str, step: int) -> OrderArm:
        visible = [
            {
                "id": case["id"],
                "utilities": case["utilities"],
                "messages": case["reports"][::step],
            }
            for case in cases
        ]
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": encode_view({"cases": visible}).decode("utf-8"),
            },
        ]
        digest = view_hash(messages)
        subject = view_hash(
            ["seatlab.dispatch-order-request/v1", source_sha256, block, name, digest]
        )
        return OrderArm(subject, digest, messages)

    return OrderPair(block, cases, arm("chronological", 1), arm("reversed", -1))
