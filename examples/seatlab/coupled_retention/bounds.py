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
        if type(packet) is not bytes or not 1 <= len(packet) <= MAX_PACKET_BYTES:
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


def _decentralized(east: PacketGroups, west: PacketGroups) -> int:
    left = _candidates(east)
    right = _candidates(west)
    left_count = _policy_count(left)
    right_count = _policy_count(right)
    left_work = left_count * _response_cost(west, right)
    right_work = right_count * _response_cost(east, left)
    if (left_count, left_work) <= (right_count, right_work):
        rules, responses, groups = left, right, west
        count, operations = left_count, left_work
    else:
        rules, responses, groups = right, left, east
        count, operations = right_count, right_work
    if count > MAX_POLICIES or operations > MAX_SEARCH_SCORE_OPERATIONS:
        raise ValueError("exact decentralized search exceeds its budget")

    best = 0
    for actions in product(*rules.values()):
        rule = dict(zip(rules, actions, strict=True))
        value = sum(
            max(
                sum(matrix[action][rule[other]] for other, matrix in edges)
                for action in responses[packet]
            )
            for packet, edges in groups.items()
        )
        best = max(best, value)
    return best


def evaluate(rows: list[Cell]) -> dict[str, str]:
    """Compute exact common rewards for local, pooled, and world-aware decisions."""
    pairs, total, oracle = _aggregate(rows)
    pooled = sum(max(map(max, matrix)) for matrix in pairs.values())
    decentralized = _decentralized(*_groups(pairs))
    if not 0 <= decentralized <= pooled <= oracle <= total:
        raise ValueError("invalid reward bounds")
    return {
        "decentralized": str(Fraction(decentralized, total)),
        "pooled": str(Fraction(pooled, total)),
        "oracle": str(Fraction(oracle, total)),
    }
