import json
from collections import Counter, defaultdict
from statistics import fmean
from typing import Any

from ..attention.protocol import EPISODES, aliases
from ..turn_fork.packet import REPETITIONS, SEEDS, _object
from . import inputs

type _Document = dict[str, Any]
type _Optima = dict[str, dict[int, tuple[str, str]]]
type _Pair = tuple[_Document, _Document]

_CATEGORIES = (
    "visible_truth_agree_chose_best",
    "visible_truth_agree_chose_other",
    "visible_truth_differ_chose_visible",
    "visible_truth_differ_chose_truth",
    "visible_truth_differ_chose_neither",
)
_AGREE_BEST, _AGREE_OTHER, _VISIBLE, _TRUTH, _NEITHER = _CATEGORIES
_MISSES = (_AGREE_OTHER, _VISIBLE, _NEITHER)
_PAIRED_COUNTS = (
    "scheduled_pairs",
    "complete_pairs",
    "baseline_only",
    "candidate_only",
    "neither_complete",
    "same_actions",
    "same_action_decisions",
    "both_received_responses",
    "same_response_sha256",
    "baseline_optimal_in_complete_pairs",
    "candidate_optimal_in_complete_pairs",
)
_SCOPE = (
    "Visible agreement describes the reports actually shown. Disagreement with real "
    "stock does not show that the true answer was unknowable or assign fault."
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _optima(packet: _Document, board: _Document) -> _Optima:
    sources = inputs.source_index(packet)
    optima: _Optima = {}
    for obs in board["observations"]:
        source = sources[obs["source_id"]]
        cases: list[_Document] = source["cases"]
        by_id = {case["id"]: case for case in cases}
        by_alias = aliases(cases, source["seed"], source["repetition"])
        _, body = inputs.prompt(packet, obs).split("\n", 1)
        payload = _object(json.loads(body), "cases")
        result: dict[int, tuple[str, str]] = {}
        for raw in payload["cases"]:
            fields: tuple[str, ...] = ("alias", "revision_1", "utilities")
            if "revision_2" in raw:
                fields += ("revision_2",)
            shown = _object(raw, *fields)
            case_id = by_alias[shown["alias"]]
            stock = shown.get("revision_2", shown["revision_1"])
            visible = max(stock, key=shown["utilities"].__getitem__)
            case = by_id[case_id]
            true = max(case["after"], key=case["utilities"].__getitem__)
            result[case_id] = visible, true
        if result.keys() != by_id.keys():
            raise ValueError("dispatch prompt does not cover every native case")
        optima[obs["obs_id"]] = result
    return optima


def _category(visible: str, true: str, choice: str) -> str:
    if visible == true:
        return _AGREE_BEST if choice == true else _AGREE_OTHER
    if choice == visible:
        return _VISIBLE
    return _TRUTH if choice == true else _NEITHER


def _diagnose(
    rows: list[_Document],
    optima: _Optima,
    weights: Counter[str] | None,
    registered: int,
) -> _Document:
    categories = Counter(dict.fromkeys(_CATEGORIES, 0))
    states: Counter[str] = Counter()
    incomplete: Counter[str] = Counter()
    agreements = 0
    for row in rows:
        obs_id: str = row["obs_id"]
        weight = 1 if weights is None else weights[obs_id]
        expected = optima[obs_id]
        agreements += weight * sum(
            visible == true for visible, true in expected.values()
        )
        states[row["state"]] += weight
        if row["state"] == "incomplete":
            turn = row["turn"]
            incomplete["without_turn" if turn is None else turn["status"]] += weight
        if row["state"] != "complete":
            continue
        for action in row["outcome"]["actions"]:
            visible, true = expected[action["id"]]
            categories[_category(visible, true, action["choice"])] += weight
    scheduled = states.total()
    complete = states["complete"]
    decisions = complete * EPISODES
    truth = categories[_AGREE_BEST] + categories[_TRUTH]
    adheres = categories[_AGREE_BEST] + categories[_VISIBLE]
    return {
        "registered_draws": registered,
        "scheduled_draws": scheduled,
        "no_sender_selection_draws": registered - scheduled,
        "complete_draws": complete,
        "missing_first_attempts": scheduled - complete,
        "unclaimed": states["unclaimed"],
        "incomplete": states["incomplete"],
        "incomplete_without_turn": incomplete["without_turn"],
        "incomplete_callback_error": incomplete["callback_error"],
        "incomplete_received": incomplete["received"],
        "scheduled_decisions": scheduled * EPISODES,
        "scheduled_visible_truth_agreements": agreements,
        "complete_decisions": decisions,
        "native_truth_optimal": truth,
        "native_truth_accuracy": _ratio(truth, decisions),
        "visible_optimal": adheres,
        "visible_adherence": _ratio(adheres, decisions),
        "categories": dict(categories),
        "missed_optimum": {name: categories[name] for name in _MISSES},
    }


def _weights(board: _Document) -> Counter[str]:
    return Counter(s["obs_id"] for s in board["selections"] if s["obs_id"] is not None)


def _counts(
    registered: int, in_packet: int, scheduled: int, complete: int
) -> _Document:
    return {
        "registered": registered,
        "in_packet": in_packet,
        "scheduled": scheduled,
        "complete": complete,
    }


def _describe(
    packet: _Document,
    board: _Document,
    digest: str,
    report: _Document,
    optima: _Optima,
    weights: Counter[str],
) -> _Document:
    rows: list[_Document] = report["rows"]
    sources = inputs.source_index(packet)
    scheduled = {row["source_id"] for row in rows}
    complete = {row["source_id"] for row in rows if row["state"] == "complete"}
    return {
        "schema": "seatlab.decision-cross-diagnosis/v1",
        "board_sha256": digest,
        "transport": report["transport"],
        "interpretation": _SCOPE,
        "inference_unit": "world",
        "sources": _counts(
            len(SEEDS) * len(REPETITIONS), len(sources), len(scheduled), len(complete)
        ),
        "worlds": _counts(
            len(SEEDS),
            len({source["seed"] for source in sources.values()}),
            len({sources[source_id]["seed"] for source_id in scheduled}),
            len({sources[source_id]["seed"] for source_id in complete}),
        ),
        "unique_prompt_draws": _diagnose(rows, optima, None, len(rows)),
        "logical_uses": _diagnose(rows, optima, weights, 2 * len(board["selections"])),
    }


def describe(
    packet: _Document, board: _Document, digest: str, report: _Document
) -> _Document:
    return _describe(
        packet, board, digest, report, _optima(packet, board), _weights(board)
    )


def _response(row: _Document) -> str | None:
    turn = row["turn"]
    return turn["response_sha256"] if turn is not None else None


def _paired(rows: list[_Pair], weights: Counter[str] | None) -> Counter[str]:
    counts: Counter[str] = Counter()
    for baseline, candidate in rows:
        weight = 1 if weights is None else weights[baseline["obs_id"]]
        counts["scheduled_pairs"] += weight
        b, c = baseline["outcome"], candidate["outcome"]
        b_min, b_max = (0, EPISODES) if b is None else (b["optimal"], b["optimal"])
        c_min, c_max = (0, EPISODES) if c is None else (c["optimal"], c["optimal"])
        counts["lower"] += weight * (c_min - b_max)
        counts["upper"] += weight * (c_max - b_min)
        b_hash, c_hash = _response(baseline), _response(candidate)
        if b_hash is not None and c_hash is not None:
            counts["both_received_responses"] += weight
            counts["same_response_sha256"] += weight * (b_hash == c_hash)
        if b is None or c is None:
            if c is not None:
                coverage = "candidate_only"
            elif b is not None:
                coverage = "baseline_only"
            else:
                coverage = "neither_complete"
            counts[coverage] += weight
            continue
        counts["complete_pairs"] += weight
        counts["baseline_optimal_in_complete_pairs"] += weight * b["optimal"]
        counts["candidate_optimal_in_complete_pairs"] += weight * c["optimal"]
        matches = sum(
            left["choice"] == right["choice"]
            for left, right in zip(b["actions"], c["actions"], strict=True)
        )
        counts["same_action_decisions"] += weight * matches
        counts["same_actions"] += weight * (matches == EPISODES)
    return counts


def _coverage(counts: Counter[str]) -> dict[str, int]:
    return {name: counts[name] for name in _PAIRED_COUNTS}


def _world_change(seed: int, counts: Counter[str], registered: int) -> _Document:
    absent = registered - counts["scheduled_pairs"]
    matched = counts["complete_pairs"]
    delta = (
        counts["candidate_optimal_in_complete_pairs"]
        - counts["baseline_optimal_in_complete_pairs"]
    )
    mean = _ratio(delta, matched * EPISODES)
    return {
        "seed": seed,
        "registered_pairs": registered,
        "no_sender_selection_pairs": absent,
        "matched_pairs": matched,
        "unmatched_scheduled_pairs": counts["scheduled_pairs"] - matched,
        "matched_observed_change": mean,
        "fully_observed_change": mean if matched == registered else None,
        "registered_bounds": {
            "lower": (counts["lower"] - absent * EPISODES) / (registered * EPISODES),
            "upper": (counts["upper"] + absent * EPISODES) / (registered * EPISODES),
        },
    }


def _equal_world_change(worlds: list[_Document]) -> _Document:
    matched = [
        w["matched_observed_change"]
        for w in worlds
        if w["matched_observed_change"] is not None
    ]
    full = [
        w["fully_observed_change"]
        for w in worlds
        if w["fully_observed_change"] is not None
    ]
    return {
        "metric": "candidate_minus_baseline_native_truth_accuracy",
        "weighting": "logical_uses_with_equal_weight_per_world",
        "estimate": fmean(full) if len(full) == len(SEEDS) else None,
        "matched_observed_estimate": (
            fmean(matched) if len(matched) == len(SEEDS) else None
        ),
        "registered_worlds": len(SEEDS),
        "fully_observed_worlds": len(full),
        "matched_worlds": len(matched),
        "registered_bounds": {
            side: fmean(world["registered_bounds"][side] for world in worlds)
            for side in ("lower", "upper")
        },
        "worlds": worlds,
    }


def compare(
    packet: _Document,
    board: _Document,
    digest: str,
    baseline: _Document,
    candidate: _Document,
) -> _Document:
    if baseline["board_sha256"] != digest or candidate["board_sha256"] != digest:
        raise ValueError("receivers must use the same board")
    by_source: defaultdict[str, list[_Pair]] = defaultdict(list)
    pairs: list[_Pair] = []
    for expected, left, right in zip(
        inputs.draws(board), baseline["rows"], candidate["rows"], strict=True
    ):
        if any(
            left[key] != value or right[key] != value for key, value in expected.items()
        ):
            raise ValueError("receiver draw identities do not match")
        pair = (left, right)
        by_source[expected["source_id"]].append(pair)
        pairs.append(pair)
    weights = _weights(board)
    optima = _optima(packet, board)
    registered = Counter(s["source_id"] for s in board["selections"])
    within_source: list[_Document] = []
    worlds: list[_Document] = []
    for seed in SEEDS:
        world_counts: Counter[str] = Counter()
        world_registered = 0
        for rep in REPETITIONS:
            source_id = f"s{seed}-r{rep}"
            rows = by_source[source_id]
            within_source.append(
                {"source_id": source_id, **_coverage(_paired(rows, None))}
            )
            world_counts.update(_paired(rows, weights))
            world_registered += 2 * registered[source_id]
        worlds.append(_world_change(seed, world_counts, world_registered))
    return {
        "schema": "seatlab.decision-cross-comparison/v1",
        "board_sha256": digest,
        "inference_unit": "world",
        "baseline": _describe(packet, board, digest, baseline, optima, weights),
        "candidate": _describe(packet, board, digest, candidate, optima, weights),
        "paired": _coverage(_paired(pairs, None)),
        "within_source": within_source,
        "equal_world_change": _equal_world_change(worlds),
        "identical_public_evidence": baseline == candidate,
        "evidence_caveat": (
            "Matching response hashes show matching public commitments, not call "
            "identity. Public reports do not establish independent calls. Comparing a "
            "report with itself is not independent replication. Bounds describe missing "
            "decisions, not statistical uncertainty."
        ),
    }
