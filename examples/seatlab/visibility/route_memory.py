from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from . import route_protocol as protocol

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from .route_packet import Case

STAGES = ("bound", "anonymous", "x-review", "x-decide", "y-review", "y-decide")
_FIELDS = ((0, 1), (0, 2), (1, 2))


@dataclass(frozen=True)
class Menu:
    own: protocol.View
    peers: tuple[protocol.View, protocol.View]
    history: tuple[tuple[int, int], tuple[int, int]]
    source_indices: tuple[int, int]


@dataclass(frozen=True)
class Request:
    stage: str
    role: str
    prompt: str


def blocks() -> tuple[protocol.Block, ...]:
    return tuple(block for block in protocol.blocks() if block.round >= 3)


def key(block: protocol.Block) -> str:
    return "ma-" + block.id.removeprefix("rv-")


def run_id(block: protocol.Block, stage: str) -> str:
    if stage not in STAGES:
        raise ValueError("unknown memory stage")
    return f"{key(block)}-{stage}"


def arm_order(block: protocol.Block) -> tuple[str, str]:
    if (block.rotation + block.swap + block.rep) % 2 == 0:
        return "bound", "anonymous"
    return "anonymous", "bound"


def peer_order(block: protocol.Block) -> tuple[str, str]:
    if (block.policy + block.round + block.rotation + block.rep) % 2 == 0:
        return "X", "Y"
    return "Y", "X"


def exact(case: Case, source_index: int) -> bool:
    truth, reports = case.truth, case.reports
    if (
        truth is None
        or reports is None
        or len(reports) != len(_FIELDS)
        or type(source_index) is not int
        or not 0 <= source_index < len(_FIELDS)
    ):
        raise ValueError("invalid report audit input")
    i, j = _FIELDS[source_index]
    a, b = truth
    return reports[source_index] == ((a[i], a[j]), (b[i], b[j]))


def _prior_audit(prior: Case, current: Case, cutoff: int) -> bool:
    if (
        prior.round >= current.round
        or prior.trial_sha256 != current.trial_sha256
        or prior.source_sha256 != current.source_sha256
        or prior.reason is not None
    ):
        return False
    fetch, reports, truth = prior.fetch_seq, prior.report_seqs, prior.truth_seqs
    return (
        fetch is not None
        and reports is not None
        and truth is not None
        and len(reports) > 0
        and len(truth) > 0
        and max(fetch, *reports, *truth) < cutoff
    )


def menu(cases: Sequence[Case], block: protocol.Block) -> Menu | None:
    if block.round < 3:
        return None
    current = next(
        (
            case
            for case in cases
            if (case.policy, case.round) == (block.policy, block.round)
        ),
        None,
    )
    if (
        current is None
        or current.reason is not None
        or current.reports is None
        or current.source_sha256 is None
    ):
        return None
    cutoff = current.fetch_seq
    if cutoff is None:
        return None
    prior = tuple(case for case in cases if _prior_audit(case, current, cutoff))
    checked = len(prior)
    if checked < 2:
        return None

    step = 1 if (block.policy + block.round + block.swap + block.rep) % 2 == 0 else -1
    x = (block.rotation + step) % len(_FIELDS)
    y = (block.rotation - step) % len(_FIELDS)
    return Menu(
        own=protocol.view(current, block, "duplicate"),
        peers=(
            protocol.view(current, replace(block, rotation=x), "duplicate"),
            protocol.view(current, replace(block, rotation=y), "duplicate"),
        ),
        history=(
            (sum(exact(case, x) for case in prior), checked),
            (sum(exact(case, y) for case in prior), checked),
        ),
        source_indices=(x, y),
    )


def select_prompt(menu: Menu, arm: str) -> str:
    if arm not in ("bound", "anonymous"):
        raise ValueError("unknown memory arm")
    own = menu.own
    audits = []
    for (correct, checked), label in sorted(zip(menu.history, ("X", "Y"), strict=True)):
        row: dict[str, str | int] = {"exact": correct, "checked": checked}
        if arm == "bound":
            row["candidate"] = label
        audits.append(row)
    data = {
        "own": protocol._observations(own.fields, own.a, own.b),
        "candidates": {
            "X": {"fields": menu.peers[0].fields},
            "Y": {"fields": menu.peers[1].fields},
        },
        "audits": audits,
    }
    return (
        "Choose one reviewer to help select a route. Each route's total is the sum of "
        "its latency, reliability, and safety scores. The goal is to maximize the "
        "total. Each audit gives the number of prior reports that matched all four "
        "scores (exact) and the number checked. Request one candidate. "
        'Reply with exactly one JSON object, {"ask":"X"} or {"ask":"Y"}, '
        "and no other text.\n\nData:\n" + json.dumps(data, separators=(",", ":"))
    )


def parse_selection(text: str) -> str:
    try:
        label = protocol._response(text, {"ask"})["ask"]
    except ValueError:
        raise ValueError("invalid selection response") from None
    if not isinstance(label, str) or label not in ("X", "Y"):
        raise ValueError("invalid selection response")
    return label


def steps(menu: Menu, block: protocol.Block) -> Generator[Request, str | None, None]:
    selected: set[str] = set()
    for arm in arm_order(block):
        text = yield Request(arm, "route_decide", select_prompt(menu, arm))
        if text is not None:
            try:
                selected.add(parse_selection(text))
            except ValueError:
                continue

    for label in peer_order(block):
        if label not in selected:
            continue
        view = menu.peers[0 if label == "X" else 1]
        stage = label.lower()
        text = yield Request(
            f"{stage}-review", "route_review", protocol.review_prompt(view)
        )
        if text is None:
            continue
        try:
            review = protocol.parse_review(text)
        except ValueError:
            continue
        yield Request(
            f"{stage}-decide",
            "route_decide",
            protocol.decision_prompt(menu.own, view.fields, review, "evidence"),
        )
