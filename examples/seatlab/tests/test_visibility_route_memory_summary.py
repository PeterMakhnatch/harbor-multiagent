import json
from math import isclose

from examples.seatlab.visibility import route_memory as m
from examples.seatlab.visibility.route_memory_summary import summarize

type _Row = dict[str, int | str | bool | None]


def _register() -> list[_Row]:
    return [
        {
            "policy": block.policy,
            "round": block.round,
            "rotation": block.rotation,
            "swap": block.swap,
            "rep": block.rep,
            "arm": arm,
            "status": "unrun",
            "selected": None,
            "source_index": None,
            "better_history": None,
            "current_exact": None,
            "label": None,
            "best": "B" if block.swap else "A",
        }
        for block in m.blocks()
        for arm in ("bound", "anonymous")
    ]


def _pair(
    rows: list[_Row],
    *,
    policy: int = 0,
    round_number: int = 3,
    rotation: int = 0,
    swap: int = 0,
    rep: int = 0,
) -> tuple[_Row, _Row]:
    matches = [
        row
        for row in rows
        if (row["policy"], row["round"], row["rotation"], row["swap"], row["rep"])
        == (policy, round_number, rotation, swap, rep)
    ]
    bound, anonymous = matches
    return bound, anonymous


def _choose(
    row: _Row,
    source: int,
    label: str | None,
    *,
    better: bool | None = None,
    exact: bool = True,
) -> None:
    parity = sum(int(str(row[k])) for k in ("policy", "round", "swap", "rep")) % 2
    rotation = int(str(row["rotation"]))
    order = ((rotation + 1) % 3, (rotation + 2) % 3)
    if parity:
        order = order[::-1]
    row.update(
        selected="XY"[order.index(source)],
        source_index=source,
        better_history=better,
        current_exact=exact,
        status="missing" if label is None else "valid",
        label=label,
    )


def _reject(rows: list[_Row], message: str) -> None:
    try:
        summarize(rows)
    except ValueError as error:
        assert message in str(error)
        assert "private-example" not in str(error)
    else:
        raise AssertionError("malformed register was accepted")


def test_missing_bound_endpoint_respects_the_observed_anonymous_choice() -> None:
    rows = _register()
    _, anonymous = _pair(rows)
    _choose(anonymous, 1, "A", exact=False)
    result = summarize(rows)
    primary = result["primary"]
    assert primary["registered_blocks"] == 144
    assert primary["registered_scored_cells"] == 288
    assert primary["anonymous_only"] == primary["known_scored_cells"] == 1
    assert primary["complete_blocks"] == 0
    assert primary["paired_complete_delta"] is None
    assert primary["worst_delta"] == -1
    assert primary["best_delta"] == 143 / 144
    assert primary["by_policy"]["0"]["best_delta"] == 47 / 48
    assert result["routing"]["by_arm"]["anonymous"]["current_exact"] == {
        "compared": 1,
        "matches": 0,
        "fraction": 0,
    }

    _choose(anonymous, 1, "B", exact=False)
    primary = summarize(rows)["primary"]
    assert primary["worst_delta"] == -(143 / 144)
    assert primary["best_delta"] == 1


def test_repeat_and_label_swap_variation_compare_physical_choices() -> None:
    rows = _register()
    for swap in range(2):
        for rep in range(2):
            bound, anonymous = _pair(rows, swap=swap, rep=rep)
            _choose(bound, 1, "A" if rep else "BA"[swap], better=True)
            _choose(anonymous, 2 - rep, "A", better=False if rep == 0 else True)
    bound_0, anonymous_0 = _pair(rows)
    bound_1, anonymous_1 = _pair(rows, rep=1)
    assert bound_0["selected"] != bound_1["selected"]
    assert anonymous_0["selected"] == anonymous_1["selected"]
    variation = summarize(rows)["variation"]
    same = {"comparable_pairs": 2, "physical_agreement": 2, "physical_disagreement": 0}
    different = {
        "comparable_pairs": 2,
        "physical_agreement": 0,
        "physical_disagreement": 2,
    }
    assert variation["bound"]["repeat_pairs"]["registered_pairs"] == 72
    assert variation["bound"]["repeat_pairs"]["reporter"] == same
    assert variation["anonymous"]["repeat_pairs"]["reporter"] == different
    assert variation["bound"]["repeat_pairs"]["final"] == {
        "comparable_pairs": 2,
        "physical_agreement": 1,
        "physical_disagreement": 1,
    }
    assert variation["anonymous"]["repeat_pairs"]["final"] == same
    assert variation["anonymous"]["label_swap_pairs"]["final"] == different


def test_shared_result_counts_once_even_when_both_arms_succeed() -> None:
    rows = _register()
    bound, anonymous = _pair(rows)
    for arm in (bound, anonymous):
        _choose(arm, 1, "A", better=True)
    for arm in _pair(rows, rep=1):
        _choose(arm, 1, None, better=True)
    bound_other, anonymous_other = _pair(rows, rotation=1)
    _choose(bound_other, 2, "A")
    _choose(anonymous_other, 0, "A", exact=False)

    result = summarize(rows)
    assert result == summarize(list(reversed(rows)))
    assert result == json.loads(json.dumps(result, allow_nan=False))
    primary = result["primary"]
    assert primary["complete_blocks"] == primary["complete_ties"] == 2
    assert primary["known_scored_cells"] == 4
    assert primary["worst_delta"] == -(141 / 144)
    assert primary["best_delta"] == 141 / 144
    routing = result["routing"]
    assert routing["selectors"] == {"neither": 141, "one": 0, "same": 2, "different": 1}
    assert routing["final_branches"] == {
        "shared_valid": 1,
        "shared_correct": 1,
        "unshared_valid": 2,
        "unshared_correct": 2,
        "distinct_valid": 3,
        "distinct_correct": 3,
        "blocks_with_two_valid_branches": 1,
    }
    assert routing["by_arm"]["bound"]["better_history"]["compared"] == 2
    assert routing["by_arm"]["bound"]["correct_decisions"] == 2
    anonymous["label"] = "B"
    _reject(rows, "conflicting outcomes on a shared final branch")


def test_equal_source_weights_require_complete_pairs_in_all_sources() -> None:
    rows = _register()
    for row in rows:
        if row["policy"] == 1:
            row.update(best=None, status="ineligible")
    for policy, rep in ((0, 0), (2, 0), (2, 1)):
        bound, anonymous = _pair(rows, policy=policy, rep=rep)
        _choose(bound, 1, "A" if policy == 0 else "B")
        _choose(anonymous, 2, "B" if policy == 0 else "A")
    result = summarize(rows)
    assert result["population"]["source_policies_with_known_rounds"] == 2
    primary = result["primary"]
    assert primary["by_policy"]["1"]["registered_blocks"] == 48
    assert primary["by_policy"]["1"]["complete_blocks"] == 0
    assert primary["by_policy"]["1"]["paired_complete_delta"] is None
    assert primary["paired_complete_delta"] is None

    for row in rows:
        if row["policy"] == 1:
            row.update(best="B" if row["swap"] == 1 else "A", status="unrun")
    assert summarize(rows)["primary"]["paired_complete_delta"] is None
    bound, anonymous = _pair(rows, policy=1)
    _choose(bound, 1, "A")
    _choose(anonymous, 2, "B")
    primary = summarize(rows)["primary"]
    assert primary["complete_blocks"] == 4
    assert primary["complete_positive"] == primary["complete_negative"] == 2
    assert isclose(primary["paired_complete_delta"], 1 / 3)


def test_rejects_unregistered_and_invalid_outcomes_without_echoing_input() -> None:
    rows = _register()
    _reject(rows[1:], "expected 288 registered decision rows")
    _reject([rows[1], *rows[1:]], "duplicate registered decision row")
    bound, _ = _pair(rows)
    for field, value, message in (
        ("policy", True, "invalid block coordinate"),
        ("arm", "private-example", "invalid decision category"),
        ("best", "B", "inconsistent physical answer key"),
        ("status", "valid", "status and final label disagree"),
        ("label", "private-example", "invalid decision category"),
    ):
        original = bound[field]
        bound[field] = value
        _reject(rows, message)
        bound[field] = original
    _choose(bound, 1, "A")
    bound["source_index"] = 2
    _reject(rows, "selection does not match the scheduled reporter")
