from dataclasses import dataclass
from fractions import Fraction


class BoundaryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class World:
    name: str
    mass: int
    observation: bytes
    scores: dict[str, int]


_MAX_WORLDS = 1024
_MAX_ACTIONS = 512
_MAX_CELLS = 100_000
_MAX_OBSERVATION = 1 << 20
_MAX_TOTAL_OBSERVATIONS = 32 << 20
_MAX_INTEGER = (1 << 63) - 1
_MAX_LABEL_LENGTH = 256


def _positive_integer(value: object) -> bool:
    return type(value) is int and 1 <= value <= _MAX_INTEGER


def _clean_label(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= _MAX_LABEL_LENGTH
        and value.isprintable()
        and value == value.strip()
    )


def _validate(worlds: list[World], scale: int) -> tuple[str, ...]:
    if not _positive_integer(scale):
        raise BoundaryError("invalid scale")
    if type(worlds) is not list or not 1 <= len(worlds) <= _MAX_WORLDS:
        raise BoundaryError("invalid world count")
    names: set[str] = set()
    actions: tuple[str, ...] = ()
    observation_bytes = 0
    for world in worlds:
        if type(world) is not World or not _clean_label(world.name):
            raise BoundaryError("invalid world or world name")
        if world.name in names:
            raise BoundaryError("duplicate world name")
        names.add(world.name)
        if not _positive_integer(world.mass):
            raise BoundaryError("invalid world mass")
        if (
            type(world.observation) is not bytes
            or len(world.observation) > _MAX_OBSERVATION
        ):
            raise BoundaryError("invalid observation")
        observation_bytes += len(world.observation)
        if observation_bytes > _MAX_TOTAL_OBSERVATIONS:
            raise BoundaryError("aggregate observation limit exceeded")
        if type(world.scores) is not dict or not 1 <= len(world.scores) <= _MAX_ACTIONS:
            raise BoundaryError("invalid action count")
        if not actions:
            actions = tuple(world.scores)
            if len(actions) * len(worlds) > _MAX_CELLS:
                raise BoundaryError("score matrix limit exceeded")
        for action, score in world.scores.items():
            if not _clean_label(action):
                raise BoundaryError("invalid action label")
            if type(score) is not int or not 0 <= score <= scale:
                raise BoundaryError("invalid score")
        if world.scores.keys() != worlds[0].scores.keys():
            raise BoundaryError("inconsistent action rules")
    return actions


def analyze(worlds: list[World], scale: int) -> dict[str, int | str]:
    actions = _validate(worlds, scale)
    totals = [0] * len(actions)
    class_totals: dict[bytes, list[int]] = {}
    class_informed: dict[bytes, int] = {}
    mass = 0
    for world in worlds:
        mass += world.mass
        observation = world.observation
        conditional = class_totals.get(observation)
        if conditional is None:
            conditional = [0] * len(actions)
            class_totals[observation] = conditional
            class_informed[observation] = 0
        best = 0
        for index, action in enumerate(actions):
            weighted_score = world.mass * world.scores[action]
            totals[index] += weighted_score
            conditional[index] += weighted_score
            best = max(best, weighted_score)
        class_informed[observation] += best

    unobserved = max(totals)
    observed = 0
    informed = 0
    ambiguous_classes = 0
    for observation, conditional in class_totals.items():
        conditional_best = max(conditional)
        informed_best = class_informed[observation]
        assert conditional_best <= informed_best
        observed += conditional_best
        informed += informed_best
        ambiguous_classes += int(informed_best > conditional_best)

    denominator = mass * scale
    assert 0 <= unobserved <= observed <= informed <= denominator
    return {
        "worlds": len(worlds),
        "observations": len(class_totals),
        "actions": len(actions),
        "mass": mass,
        "scale": scale,
        "unobserved": str(Fraction(unobserved, denominator)),
        "observed": str(Fraction(observed, denominator)),
        "informed": str(Fraction(informed, denominator)),
        "observation_value": str(Fraction(observed - unobserved, denominator)),
        "gap": str(Fraction(informed - observed, denominator)),
        "ambiguous_classes": ambiguous_classes,
    }


def conflict_witness(worlds: list[World], scale: int) -> dict[str, list[int]] | None:
    actions = _validate(worlds, scale)
    classes: dict[bytes, list[int]] = {}
    for index, world in enumerate(worlds):
        classes.setdefault(world.observation, []).append(index)
    for members in classes.values():
        common = set(actions)
        rows, counts = [], []
        for index in members:
            scores = worlds[index].scores
            maximum = max(scores.values())
            narrowed = {rule for rule in common if scores[rule] == maximum}
            if len(narrowed) == len(common):
                continue
            rows.append(index)
            counts.append(len(narrowed))
            common = narrowed
            if not common:
                return {"row_indices": rows, "remaining_optimal_rule_counts": counts}
    return None
