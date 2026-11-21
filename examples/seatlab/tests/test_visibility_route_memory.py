from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import pytest

from examples.seatlab.visibility import route_memory as memory
from examples.seatlab.visibility import route_protocol as protocol
from examples.seatlab.visibility.route_packet import Case

if TYPE_CHECKING:
    from collections.abc import Mapping

    from examples.seatlab.visibility.listener import Report
    from examples.seatlab.visibility.route_packet import Truth

_TRUTH: Truth = ((31, 43, 57), (62, 74, 86))
_EXACT_REPORTS: tuple[Report, ...] = (
    ((31, 43), (62, 74)),
    ((31, 57), (62, 86)),
    ((43, 57), (74, 86)),
)
_CURRENT_REPORTS: tuple[Report, ...] = (
    ((11, 12), (13, 14)),
    ((21, 22), (23, 24)),
    ((31, 32), (33, 34)),
)
_FIELDS = (("latency", "reliability"), ("latency", "safety"), ("reliability", "safety"))
_PRIVATE = "private-archive-response"
_REVIEW = '{"vote":"B","a":[71,72],"b":[83,84]}'
_DECISION = '{"choice":"A"}'
_STAGES = ("bound", "anonymous", "x-review", "x-decide", "y-review", "y-decide")


def _case(policy: int, round_number: int, reports: tuple[Report, ...]) -> Case:
    start = round_number * 10
    return Case(
        policy=policy,
        round=round_number,
        reports=reports,
        best=1,
        source_sha256="a" * 64,
        trial_sha256="b" * 64,
        truth=_TRUTH,
        report_seqs=(start + 2, start + 1, start + 3),
        fetch_seq=start + 4,
        decision_seq=200 + round_number,
        truth_seqs=(start + 5, start + 6, start + 7),
    )


def _cases(policy: int, round_number: int) -> tuple[Case, Case, Case]:
    a, b, _ = _EXACT_REPORTS
    return (
        _case(policy, 1, (a, b, ((43, 57), (74, 85)))),
        _case(policy, 2, (a, ((31, 57), (62, 85)), ((43, 57), (73, 86)))),
        _case(policy, round_number, _CURRENT_REPORTS),
    )


def _menu(block: protocol.Block) -> memory.Menu:
    menu = memory.menu(_cases(block.policy, block.round), block)
    assert menu is not None
    return menu


def _data(prompt: str) -> object:
    return json.loads(prompt.split("\n\nData:\n", 1)[1])


def _requests(
    menu: memory.Menu, block: protocol.Block, responses: Mapping[str, str | None]
) -> tuple[memory.Request, ...]:
    steps = memory.steps(menu, block)
    request = next(steps)
    requests = []
    for _ in _STAGES:
        requests.append(request)
        try:
            request = steps.send(responses[request.stage])
        except StopIteration:
            return tuple(requests)
    pytest.fail("the block requested more than 6 turns")


def test_memory_register_and_counterbalancing() -> None:
    blocks = memory.blocks()
    assert blocks == tuple(block for block in protocol.blocks() if block.round >= 3)
    assert len(blocks) == 144
    assert memory.STAGES == _STAGES
    ids = set()
    for block in blocks:
        key = "ma-" + block.id.removeprefix("rv-")
        assert memory.key(block) == key
        for stage in _STAGES:
            run_id = memory.run_id(block, stage)
            assert run_id == f"{key}-{stage}"
            ids.add(run_id)
        assert memory.arm_order(block) == (
            ("bound", "anonymous")
            if (block.rotation + block.swap + block.rep) % 2 == 0
            else ("anonymous", "bound")
        )
        assert memory.peer_order(block) == (
            ("X", "Y")
            if (block.policy + block.round + block.rotation + block.rep) % 2 == 0
            else ("Y", "X")
        )
    assert len(ids) == 864


def test_menu_aliases_and_route_swaps_follow_all_registered_blocks() -> None:
    exact_counts = (2, 1, 0)
    for block in memory.blocks():
        own = block.rotation
        following = (own + 1) % 3
        preceding = (own - 1) % 3
        peers = (
            (following, preceding)
            if (block.policy + block.round + block.swap + block.rep) % 2 == 0
            else (preceding, following)
        )
        views = tuple(
            protocol.View(_FIELDS[index], values[block.swap], values[1 - block.swap])
            for index, values in enumerate(_CURRENT_REPORTS)
        )
        x, y = peers
        assert _menu(block) == memory.Menu(
            views[own],
            (views[x], views[y]),
            ((exact_counts[x], 2), (exact_counts[y], 2)),
            peers,
        )


@pytest.mark.parametrize("source_index", range(3))
@pytest.mark.parametrize("row_index", range(2))
@pytest.mark.parametrize("score_index", range(2))
def test_exact_checks_each_physical_score(
    source_index: int, row_index: int, score_index: int
) -> None:
    case = _case(0, 1, _EXACT_REPORTS)
    assert memory.exact(case, source_index)
    rows = list(_EXACT_REPORTS[source_index])
    first, second = rows[row_index]
    rows[row_index] = (first + int(score_index == 0), second + int(score_index == 1))
    reports = list(_EXACT_REPORTS)
    reports[source_index] = (rows[0], rows[1])
    assert not memory.exact(replace(case, reports=tuple(reports)), source_index)


@pytest.mark.parametrize("event", ["report", "truth", "fetch"])
@pytest.mark.parametrize("offset", [0, 1])
def test_history_excludes_evidence_at_or_after_current_fetch(
    event: Literal["report", "truth", "fetch"], offset: int
) -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    first, second, current = _cases(block.policy, block.round)
    third = _case(block.policy, 3, _EXACT_REPORTS)
    assert current.fetch_seq is not None
    at_or_after = current.fetch_seq + offset
    match event:
        case "report":
            third = replace(third, report_seqs=(at_or_after, 31, 33))
        case "truth":
            third = replace(third, truth_seqs=(35, 36, at_or_after))
        case "fetch":
            third = replace(third, fetch_seq=at_or_after)
    assert memory.menu((first, third, current), block) is None
    assert memory.menu((first, second, third, current), block) == _menu(block)


@pytest.mark.parametrize(
    "third",
    [
        pytest.param(
            replace(_case(0, 3, _EXACT_REPORTS), trial_sha256="c" * 64),
            id="other-trial",
        ),
        pytest.param(
            replace(_case(0, 3, _EXACT_REPORTS), source_sha256="d" * 64),
            id="other-commitment",
        ),
        pytest.param(replace(_case(0, 3, _EXACT_REPORTS), round=5), id="not-earlier"),
        pytest.param(
            replace(_case(0, 3, _EXACT_REPORTS), fetch_seq=None),
            id="missing-authenticated-fetch",
        ),
        pytest.param(
            replace(_case(0, 3, _EXACT_REPORTS), report_seqs=None),
            id="missing-report-events",
        ),
        pytest.param(
            replace(_case(0, 3, _EXACT_REPORTS), truth_seqs=None),
            id="missing-truth-observations",
        ),
    ],
)
def test_history_requires_the_same_source_and_an_earlier_authenticated_round(
    third: Case,
) -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    first, second, current = _cases(block.policy, block.round)
    assert memory.menu((first, third, current), block) is None
    assert memory.menu((first, second, third, current), block) == _menu(block)


def test_history_uses_all_qualifying_rounds_and_ignores_prior_decision_times() -> None:
    block = protocol.Block(0, 5, 0, 1, 0)
    first, second, current = _cases(block.policy, block.round)
    third = _case(block.policy, 3, _EXACT_REPORTS)
    expected = memory.menu((first, second, third, current), block)
    assert expected is not None
    assert expected.history == ((2, 3), (1, 3))
    assert (
        memory.menu(
            (
                replace(first, decision_seq=18),
                replace(second, decision_seq=28),
                replace(third, decision_seq=38),
                current,
            ),
            block,
        )
        == expected
    )


def test_missing_current_report_cannot_form_a_menu() -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    first, second, current = _cases(block.policy, block.round)
    assert memory.menu((first, second), block) is None
    assert memory.menu((first, second, replace(current, reports=None)), block) is None


def test_selection_prompts_show_only_the_registered_observations() -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    menu = _menu(block)
    bound_prompt = memory.select_prompt(menu, "bound")
    anonymous_prompt = memory.select_prompt(menu, "anonymous")
    observations = {
        "own": {"fields": ["latency", "reliability"], "a": [11, 12], "b": [13, 14]},
        "candidates": {
            "X": {"fields": ["latency", "safety"]},
            "Y": {"fields": ["reliability", "safety"]},
        },
    }
    assert _data(bound_prompt) == {
        **observations,
        "audits": [
            {"exact": 0, "checked": 2, "candidate": "Y"},
            {"exact": 1, "checked": 2, "candidate": "X"},
        ],
    }
    assert _data(anonymous_prompt) == {
        **observations,
        "audits": [{"exact": 0, "checked": 2}, {"exact": 1, "checked": 2}],
    }
    for prompt in (bound_prompt, anonymous_prompt):
        for hidden in (block.id, "a" * 64, "b" * 64, "[21,22]", "[31,32]"):
            assert hidden not in prompt


def test_selection_audits_sort_by_checked_when_exact_counts_tie() -> None:
    menu = replace(_menu(protocol.Block(0, 4, 0, 0, 0)), history=((1, 3), (1, 2)))
    for arm, audits in (
        (
            "bound",
            [
                {"exact": 1, "checked": 2, "candidate": "Y"},
                {"exact": 1, "checked": 3, "candidate": "X"},
            ],
        ),
        ("anonymous", [{"exact": 1, "checked": 2}, {"exact": 1, "checked": 3}]),
    ):
        data = _data(memory.select_prompt(menu, arm))
        assert isinstance(data, dict)
        assert data["audits"] == audits


def test_present_truth_and_best_do_not_affect_any_prompt() -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    first, second, current = _cases(block.policy, block.round)
    altered = replace(current, truth=((21, 98, 22), (23, 7, 24)), best=0)
    assert not memory.exact(current, 1)
    assert memory.exact(altered, 1)
    altered_menu = memory.menu((first, second, altered), block)
    assert altered_menu is not None
    responses = {
        "bound": '{"ask":"X"}',
        "anonymous": '{"ask":"Y"}',
        "x-review": _REVIEW,
        "x-decide": _DECISION,
        "y-review": _REVIEW,
        "y-decide": _DECISION,
    }
    assert _requests(_menu(block), block, responses) == _requests(
        altered_menu, block, responses
    )


@pytest.mark.parametrize("candidate", ["X", "Y"])
def test_parse_selection_accepts_only_the_registered_choice(candidate: str) -> None:
    assert memory.parse_selection(f'\n {{"ask":"{candidate}"}} \n') == candidate


@pytest.mark.parametrize(
    "text",
    [
        _PRIVATE,
        '```json\n{"ask":"X"}\n```',
        '{"ask":"X"} {}',
        '"X"',
        '[{"ask":"X"}]',
        "{}",
        '{"ask":"x"}',
        '{"ask":"X|Y"}',
        '{"ask":true}',
        '{"ask":NaN}',
        '{"ask":"X","as\\u006b":"Y"}',
        '{"ask":"X","ask":"X"}',
        f'{{"ask":"X","extra":"{_PRIVATE}"}}',
        f'{{"ask":"{_PRIVATE}"}}',
    ],
)
def test_parse_selection_rejects_malformed_or_extra_data(text: str) -> None:
    with pytest.raises(ValueError) as error:
        memory.parse_selection(text)
    assert _PRIVATE not in str(error.value)


@pytest.mark.parametrize("candidate,index", [("X", 0), ("Y", 1)])
def test_same_selections_share_one_review_and_final(candidate: str, index: int) -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    menu = _menu(block)
    peer = menu.peers[index]
    prefix = candidate.lower()
    selection = json.dumps({"ask": candidate})
    assert _requests(
        menu,
        block,
        {
            "bound": selection,
            "anonymous": selection,
            f"{prefix}-review": _REVIEW,
            f"{prefix}-decide": _DECISION,
        },
    ) == (
        memory.Request("bound", "route_decide", memory.select_prompt(menu, "bound")),
        memory.Request(
            "anonymous", "route_decide", memory.select_prompt(menu, "anonymous")
        ),
        memory.Request(
            f"{prefix}-review", "route_review", protocol.review_prompt(peer)
        ),
        memory.Request(
            f"{prefix}-decide",
            "route_decide",
            protocol.decision_prompt(
                menu.own,
                peer.fields,
                protocol.Review("B", (71, 72), (83, 84)),
                "evidence",
            ),
        ),
    )


@pytest.mark.parametrize(
    "rep,stages",
    [
        (0, ("bound", "anonymous", "x-review", "x-decide", "y-review", "y-decide")),
        (1, ("anonymous", "bound", "y-review", "y-decide", "x-review", "x-decide")),
    ],
)
def test_distinct_selections_use_counterbalanced_branches(
    rep: int, stages: tuple[str, ...]
) -> None:
    block = protocol.Block(0, 4, 0, 0, rep)
    menu = _menu(block)
    responses = {
        "bound": '{"ask":"Y"}',
        "anonymous": '{"ask":"X"}',
        "x-review": _REVIEW,
        "x-decide": _DECISION,
        "y-review": '{"vote":"A","a":[15,16],"b":[17,18]}',
        "y-decide": '{"choice":"B"}',
    }
    requests = _requests(menu, block, responses)
    assert tuple(request.stage for request in requests) == stages
    by_stage = {request.stage: request for request in requests}
    for arm in ("bound", "anonymous"):
        assert by_stage[arm] == memory.Request(
            arm, "route_decide", memory.select_prompt(menu, arm)
        )
    for prefix, peer, review in zip(
        ("x", "y"),
        menu.peers,
        (
            protocol.Review("B", (71, 72), (83, 84)),
            protocol.Review("A", (15, 16), (17, 18)),
        ),
        strict=True,
    ):
        assert by_stage[f"{prefix}-review"] == memory.Request(
            f"{prefix}-review", "route_review", protocol.review_prompt(peer)
        )
        assert by_stage[f"{prefix}-decide"] == memory.Request(
            f"{prefix}-decide",
            "route_decide",
            protocol.decision_prompt(menu.own, peer.fields, review, "evidence"),
        )


@pytest.mark.parametrize("invalid", [None, f'{{"ask":"Y","extra":"{_PRIVATE}"}}'])
@pytest.mark.parametrize("arm", ["bound", "anonymous"])
def test_invalid_selection_does_not_create_its_branch(
    arm: str, invalid: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    menu = _menu(block)
    requests = _requests(
        menu,
        block,
        {
            "bound": '{"ask":"X"}',
            "anonymous": '{"ask":"X"}',
            arm: invalid,
            "x-review": _REVIEW,
            "x-decide": _DECISION,
        },
    )
    assert tuple(request.stage for request in requests) == (
        "bound",
        "anonymous",
        "x-review",
        "x-decide",
    )
    assert all(_PRIVATE not in request.prompt for request in requests)
    captures = capsys.readouterr()
    assert _PRIVATE not in captures.out + captures.err


def test_no_valid_selections_means_no_reviewer() -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    requests = _requests(_menu(block), block, {"bound": None, "anonymous": _PRIVATE})
    assert tuple(request.stage for request in requests) == ("bound", "anonymous")


@pytest.mark.parametrize(
    "invalid", [None, f'{{"vote":"B","a":[1,2],"b":[3,4],"text":"{_PRIVATE}"}}']
)
def test_invalid_review_has_no_final_but_other_branch_continues(
    invalid: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    menu = _menu(block)
    requests = _requests(
        menu,
        block,
        {
            "bound": '{"ask":"X"}',
            "anonymous": '{"ask":"Y"}',
            "x-review": invalid,
            "y-review": _REVIEW,
            "y-decide": _DECISION,
        },
    )
    assert tuple(request.stage for request in requests) == (
        "bound",
        "anonymous",
        "x-review",
        "y-review",
        "y-decide",
    )
    assert all(_PRIVATE not in request.prompt for request in requests)
    captures = capsys.readouterr()
    assert _PRIVATE not in captures.out + captures.err


@pytest.mark.parametrize("invalid", [None, f'{{"choice":"A","text":"{_PRIVATE}"}}'])
def test_invalid_final_is_not_retried_or_shared_with_the_other_branch(
    invalid: str | None,
) -> None:
    block = protocol.Block(0, 4, 0, 0, 0)
    requests = _requests(
        _menu(block),
        block,
        {
            "bound": '{"ask":"X"}',
            "anonymous": '{"ask":"Y"}',
            "x-review": _REVIEW,
            "x-decide": invalid,
            "y-review": _REVIEW,
            "y-decide": _DECISION,
        },
    )
    assert tuple(request.stage for request in requests) == _STAGES
    assert all(_PRIVATE not in request.prompt for request in requests)
