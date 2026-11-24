from typing import Any

from examples.seatlab.dispatch_order_coverage import describe_cases
from examples.seatlab.dispatch_order_pair import build_pair


def _case(
    shipment: int,
    after: str,
    old: str = "AB",
    new: str = "BC",
    utilities: tuple[int, int, int, int] = (4, 2, 3, 1),
) -> dict[str, Any]:
    return {
        "id": shipment,
        "utilities": dict(zip("ABCD", utilities, strict=True)),
        "before": ["A", "B"],
        "after": list(after),
        "reports": [
            {"revision": 1, "available": list(old)},
            {"revision": 2, "available": list(new)},
        ],
    }


def test_dispatch_order_coverage_counts_enrolled_shipments_once() -> None:
    source = "a" * 64
    first = build_pair(
        0, source, [_case(0, "AB"), _case(1, "BC"), _case(2, "AD", old="AD")]
    )
    repeat = build_pair(
        1, source, [_case(0, "AD"), _case(1, "BC"), _case(2, "AD", old="AD")]
    )
    reordered = build_pair(
        2,
        source,
        [
            _case(0, "AD", old="AD"),
            _case(1, "AB", old="BA", new="CB"),
            _case(2, "AB", old="BC", new="AB"),
            _case(3, "AB", utilities=(4, 1, 3, 2)),
            _case(4, "AD"),
        ],
    )

    # The shared case has 4 A and 2 C optima. The other classes have 3, 1, and 1 A.
    assert describe_cases([first, repeat, reordered]) == {
        "distinct_prompt_arrays": 4,
        "distinct_visible_cases": 4,
        "repeated_visible_occurrences": 7,
        "ambiguous_visible_cases": 1,
        "shipments_in_ambiguous_cases": 6,
        "best_case_only_correct": 9,
    }
