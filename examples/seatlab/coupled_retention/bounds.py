from fractions import Fraction
from itertools import product
from math import prod

from examples.seatlab.coupled_retention.records import Cell

MAX_CELLS = 256
MAX_PACKET_BYTES = 8 * 1024
MAX_WEIGHT = 1_000_000
MAX_ACTIONS = 16
MAX_POLICIES = 65_536
MAX_SEARCH_SCORE_OPERATIONS = 10_000_000

WeightedMatrix = list[list[int]]
PacketPair = tuple[bytes, bytes]
PacketGroups = dict[bytes, list[tuple[bytes, WeightedMatrix]]]
Candidates = dict[bytes, tuple[int, ...]]


def _shape(cell: Cell) -> tuple[int, int]:
    if type(cell) is not Cell:
        raise ValueError("expected a Cell")
    if type(cell.weight) is not int or not 1 <= cell.weight <= MAX_WEIGHT:
        raise ValueError("invalid cell weight")
    for packet in (cell.east, cell.west):
        if type(packet) is not bytes or len(packet) > MAX_PACKET_BYTES:
            raise ValueError("invalid observation bytes")
    scores = cell.scores
    if type(scores) is not tuple or not 1 <= len(scores) <= MAX_ACTIONS:
        raise ValueError("invalid score row count")
    first = scores[0]
    if type(first) is not tuple or not 1 <= len(first) <= MAX_ACTIONS:
        raise ValueError("invalid score column count")
    for row in scores:
        if type(row) is not tuple or len(row) != len(first):
            raise ValueError("nonrectangular score matrix")
        if any(type(score) is not int or score not in (0, 1) for score in row):
            raise ValueError("scores must be binary integers")
    return len(scores), len(first)


def _aggregate(
    rows: list[Cell],
) -> tuple[dict[PacketPair, WeightedMatrix], int, int]:
    if not 1 <= len(rows) <= MAX_CELLS:
        raise ValueError("invalid cell count")
    height, width = _shape(rows[0])
    pairs: dict[PacketPair, WeightedMatrix] = {}
    total = oracle = 0
    for cell in rows:
        if _shape(cell) != (height, width):
            raise ValueError("inconsistent score dimensions")
        pair = (cell.east, cell.west)
        if pair not in pairs:
            pairs[pair] = [[0] * width for _ in range(height)]
        weighted = pairs[pair]
        for i, row in enumerate(cell.scores):
            for j, score in enumerate(row):
                weighted[i][j] += cell.weight * score
        total += cell.weight
        oracle += cell.weight * max(map(max, cell.scores))
    return pairs, total, oracle


def _groups(
    pairs: dict[PacketPair, WeightedMatrix],
) -> tuple[PacketGroups, PacketGroups]:
    east: PacketGroups = {}
    west: PacketGroups = {}
    for (left, right), matrix in pairs.items():
        east.setdefault(left, []).append((right, matrix))
        transpose = [list(column) for column in zip(*matrix, strict=True)]
        west.setdefault(right, []).append((left, transpose))
    return east, west


def _candidates(groups: PacketGroups) -> Candidates:
    candidates: Candidates = {}
    for packet, edges in groups.items():
        vectors = [
            tuple(score for _, matrix in edges for score in matrix[action])
            for action in range(len(edges[0][1]))
        ]
        candidates[packet] = tuple(
            action
            for action, vector in enumerate(vectors)
            if not any(
                (other < action or alternative != vector)
                and all(a >= b for a, b in zip(alternative, vector, strict=True))
                for other, alternative in enumerate(vectors)
            )
        )
    return candidates


def _policy_count(candidates: Candidates) -> int:
    return prod(map(len, candidates.values()))


def _response_cost(groups: PacketGroups, candidates: Candidates) -> int:
    return sum(len(edges) * len(candidates[packet]) for packet, edges in groups.items())


def _response_score(
    edges: list[tuple[bytes, WeightedMatrix]],
    action: int,
    rule: dict[bytes, int],
) -> int:
    return sum(matrix[action][rule[other]] for other, matrix in edges)


def _decentralized(
    east: PacketGroups, west: PacketGroups, *, witness: bool
) -> tuple[int, dict[bytes, int], dict[bytes, int]]:
    left = _candidates(east)
    right = _candidates(west)
    left_count = _policy_count(left)
    right_count = _policy_count(right)
    left_work = left_count * _response_cost(west, right)
    right_work = right_count * _response_cost(east, left)
    left_fits = left_count <= MAX_POLICIES and left_work <= MAX_SEARCH_SCORE_OPERATIONS
    right_fits = (
        right_count <= MAX_POLICIES and right_work <= MAX_SEARCH_SCORE_OPERATIONS
    )
    if not left_fits and not right_fits:
        raise ValueError("exact decentralized search exceeds its budget")
    east_first = left_fits and (
        not right_fits or (left_work, left_count) <= (right_work, right_count)
    )
    if east_first:
        rules, responses, groups = left, right, west
    else:
        rules, responses, groups = right, left, east

    packets = sorted(rules)
    best = -1
    best_rule: dict[bytes, int] = {}
    for actions in product(*(rules[packet] for packet in packets)):
        rule = dict(zip(packets, actions, strict=True))
        value = sum(
            max(_response_score(edges, action, rule) for action in responses[packet])
            for packet, edges in groups.items()
        )
        if value > best:
            best = value
            if witness:
                best_rule = rule
    if not witness:
        return best, {}, {}

    best_response = {
        packet: max(
            responses[packet],
            key=lambda action, packet=packet: _response_score(
                groups[packet], action, best_rule
            ),
        )
        for packet in sorted(groups)
    }
    if east_first:
        return best, best_rule, best_response
    return best, best_response, best_rule


def _evaluate(
    rows: list[Cell], *, witness: bool
) -> tuple[dict[str, str], dict[bytes, int], dict[bytes, int]]:
    pairs, total, oracle = _aggregate(rows)
    pooled = sum(max(map(max, matrix)) for matrix in pairs.values())
    decentralized, east_rule, west_rule = _decentralized(
        *_groups(pairs), witness=witness
    )
    if not 0 <= decentralized <= pooled <= oracle <= total:
        raise ValueError("invalid reward bounds")
    values = {
        "decentralized": str(Fraction(decentralized, total)),
        "pooled": str(Fraction(pooled, total)),
        "oracle": str(Fraction(oracle, total)),
    }
    return values, east_rule, west_rule


def evaluate(rows: list[Cell]) -> dict[str, str]:
    """Compute exact common rewards for local, pooled, and world-aware decisions."""
    return _evaluate(rows, witness=False)[0]


def optimize(
    rows: list[Cell],
) -> tuple[dict[str, str], dict[bytes, int], dict[bytes, int]]:
    """Compute exact rewards and a deterministic optimal rule for each role."""
    return _evaluate(rows, witness=True)
