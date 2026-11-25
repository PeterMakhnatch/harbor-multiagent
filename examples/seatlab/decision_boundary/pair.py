from fractions import Fraction

from .core import BoundaryError, World, analyze


def compare(
    before: list[World], before_scale: int, after: list[World], after_scale: int
) -> dict[str, int | str]:
    before_analysis = analyze(before, before_scale)
    after_analysis = analyze(after, after_scale)
    if before_scale != after_scale:
        raise BoundaryError("scale mismatch")
    before_by_name = {world.name: world for world in before}
    after_by_name = {world.name: world for world in after}
    if before_by_name.keys() != after_by_name.keys():
        raise BoundaryError("world names mismatch")

    before_to_after: dict[bytes, bytes] = {}
    after_to_before: dict[bytes, bytes] = {}
    refinement = True
    coarsening = True
    same_bytes = 0
    for name, previous in before_by_name.items():
        current = after_by_name[name]
        if previous.mass != current.mass:
            raise BoundaryError("world mass mismatch")
        if previous.scores != current.scores:
            raise BoundaryError("score matrix mismatch")
        old = previous.observation
        new = current.observation
        same_bytes += int(old == new)
        if before_to_after.setdefault(old, new) != new:
            coarsening = False
        if after_to_before.setdefault(new, old) != old:
            refinement = False

    if refinement and coarsening:
        relation = "equivalent"
    elif refinement:
        relation = "refinement"
    elif coarsening:
        relation = "coarsening"
    else:
        relation = "incomparable"

    before_observed = Fraction(before_analysis["observed"])
    after_observed = Fraction(after_analysis["observed"])
    gain = after_observed - before_observed
    assert not refinement or gain >= 0
    assert not coarsening or gain <= 0
    return {
        "relation": relation,
        "before_observed": str(before_observed),
        "after_observed": str(after_observed),
        "gain": str(gain),
        "before_observations": before_analysis["observations"],
        "after_observations": after_analysis["observations"],
        "same_bytes": same_bytes,
        "worlds": len(before),
    }
