from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING, cast

import pytest

from examples.seatlab.visibility import route_protocol as protocol

if TYPE_CHECKING:
    from examples.seatlab.visibility.listener import Report
    from examples.seatlab.visibility.route_packet import Case


@dataclass(frozen=True)
class _SourceCase:
    policy: int = 1
    round: int = 4
    reports: tuple[Report, ...] | None = (
        ((11, 22), (31, 42)),
        ((13, 24), (33, 44)),
        ((15, 26), (35, 46)),
    )
    source_id: str = "private-source-id"
    text: str = "private-archived-text"

    @property
    def best(self) -> int:
        raise AssertionError("the public protocol must not read truth")


def _data(prompt: str) -> dict[str, object]:
    value = json.loads(prompt.split("\n\nData:\n", 1)[1])
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def test_full_register_order_and_branch_pairing() -> None:
    blocks = protocol.blocks()
    assert [(b.rep, b.policy, b.round, b.rotation, b.swap) for b in blocks] == list(
        product(range(2), range(3), range(1, 7), range(3), range(2))
    )
    assert blocks[0].id == "rv-p0-r1-s0-m0-d0"
    assert blocks[-1].id == "rv-p2-r6-s2-m1-d1"
    assert len({b.id for b in blocks}) == 216
    ids = []
    for block in blocks:
        first, second = protocol.lenses(block)
        parity = (
            block.policy + block.round - 1 + block.rotation + block.swap + block.rep
        ) % 2
        if parity == 0:
            assert (first, second) == ("duplicate", "complementary")
        else:
            assert (first, second) == ("complementary", "duplicate")
        assert protocol.channels(block, second) == protocol.channels(block, first)[::-1]
        repeat = protocol.Block(
            block.policy, block.round, block.rotation, block.swap, 1 - block.rep
        )
        for lens in (first, second):
            assert (
                protocol.channels(block, lens) == protocol.channels(repeat, lens)[::-1]
            )
        for lens, lens_code in (("duplicate", "d"), ("complementary", "c")):
            stages = (("review", "r"), ("vote", "v"), ("evidence", "e"))
            for stage, stage_code in stages:
                run_id = protocol.run_id(block, lens, stage)
                assert run_id == f"{block.id}-{lens_code}-{stage_code}"
                ids.append(run_id)
    assert len(set(ids)) == 1296
    assert all(run_id.isascii() and run_id.startswith("rv-") for run_id in ids)


@pytest.mark.parametrize("rotation,next_rotation", [(0, 1), (1, 2), (2, 0)])
@pytest.mark.parametrize("swap", [0, 1])
def test_public_views_rotate_and_swap_together(
    rotation: int, next_rotation: int, swap: int
) -> None:
    case = cast("Case", _SourceCase())
    block = protocol.Block(1, 4, rotation, swap, 0)
    own = protocol.view(case, block, "duplicate")
    peer = protocol.view(case, block, "complementary")
    expected = (
        (("latency", "reliability"), (11, 22), (31, 42)),
        (("latency", "safety"), (13, 24), (33, 44)),
        (("reliability", "safety"), (15, 26), (35, 46)),
    )
    assert len(set(own.fields) & set(peer.fields)) == 1
    review = protocol.parse_review('{"vote":"B","a":[0,100],"b":[99,1]}')
    own_data = None
    for visible, index in ((own, rotation), (peer, next_rotation)):
        fields, a, b = expected[index]
        if swap:
            a, b = b, a
        assert visible == protocol.View(fields, a, b)
        public = protocol.review_prompt(visible)
        data = {"fields": list(fields), "a": list(a), "b": list(b)}
        assert _data(public) == data
        assert '"vote":"A|B"' in public
        if index == rotation:
            own_data = data
        for marker in (
            block.id,
            "private-source-id",
            "private-archived-text",
            "scout_",
        ):
            assert marker not in public

    vote = protocol.decision_prompt(own, peer.fields, review, "vote")
    evidence = protocol.decision_prompt(own, peer.fields, review, "evidence")
    assert vote.split("\n\nData:\n")[0] == evidence.split("\n\nData:\n")[0]
    assert _data(vote) == {"own": own_data, "review": {"vote": "B"}}
    assert _data(evidence) == {
        "own": own_data,
        "review": {
            "vote": "B",
            "fields": list(peer.fields),
            "a": [0, 100],
            "b": [99, 1],
        },
    }
    for prompt in (vote, evidence):
        assert not any(
            marker in prompt
            for marker in (
                block.id,
                "private-source-id",
                "private-archived-text",
                "scout_",
                "duplicate",
                "complementary",
            )
        )
    changed = protocol.Review("B", (6, 7), (8, 9))
    assert protocol.decision_prompt(own, own.fields, changed, "vote") == vote


def test_ineligible_round_stays_in_schedule_but_has_no_public_view() -> None:
    case = cast("Case", _SourceCase(reports=None))
    block = protocol.Block(1, 4, 0, 0, 0)
    assert block in protocol.blocks()
    with pytest.raises(ValueError):
        protocol.view(case, block, "duplicate")


def test_prompt_rejects_fields_and_scores_outside_the_public_contract() -> None:
    with pytest.raises(ValueError):
        protocol.review_prompt(protocol.View(("private", "safety"), (1, 2), (3, 4)))
    with pytest.raises(ValueError):
        protocol.review_prompt(protocol.View(("latency", "safety"), (True, 2), (3, 4)))
    own = protocol.View(("latency", "safety"), (1, 2), (3, 4))
    with pytest.raises(ValueError):
        protocol.decision_prompt(
            own, ("private", "safety"), protocol.Review("A", (1, 2), (3, 4)), "evidence"
        )


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"vote":"A","a":[1,2],"b":[3,4]}\n```',
        '{"vote":"A","a":[1,2],"b":[3,4]} {}',
        '{"vote":"A","vote":"B","a":[1,2],"b":[3,4]}',
        '{"vote":"C","a":[1,2],"b":[3,4]}',
        '{"vote":"A|B","a":[1,2],"b":[3,4]}',
        '{"vote":"A","a":[1,2],"b":[3,4],"why":"guess"}',
        '{"vote":"A","a":[1,2]}',
        '{"vote":"A","a":[-1,2],"b":[3,4]}',
        '{"vote":"A","a":[1,101],"b":[3,4]}',
        '{"vote":"A","a":[true,2],"b":[3,4]}',
        '{"vote":"A","a":[1.0,2],"b":[3,4]}',
        '{"vote":"A","a":[1e0,2],"b":[3,4]}',
        '{"vote":"A","a":[NaN,2],"b":[3,4]}',
        '{"vote":"A","a":[Infinity,2],"b":[3,4]}',
        '{"vote":"A","a":["1",2],"b":[3,4]}',
        '{"vote":"A","a":[1],"b":[3,4]}',
        '{"vote":"A","a":[1,2,3],"b":[3,4]}',
        '{"vote":"A","a":{"0":1,"1":2},"b":[3,4]}',
    ],
)
def test_reject_malformed_review(text: str) -> None:
    with pytest.raises(ValueError):
        protocol.parse_review(text)


@pytest.mark.parametrize(
    "text",
    [
        '"A"',
        '[{"choice":"A"}]',
        '{"vote":"A"}',
        '{"choice":"a"}',
        '{"choice":true}',
        '{"choice":NaN}',
        '{"choice":"A","choic\\u0065":"A"}',
        '{"choice":"A","why":"guess"}',
        '{"choice":"A"}\nExplanation',
    ],
)
def test_reject_malformed_decision(text: str) -> None:
    with pytest.raises(ValueError):
        protocol.parse_decision(text)


def test_response_limit_for_both_roles() -> None:
    review = '{"b":[3,4],"a":[0,100],"vote":"B"}'
    decision = '{"choice":"A"}'
    expected = protocol.Review("B", (0, 100), (3, 4))
    assert protocol.parse_review(review.ljust(8192)) == expected
    assert protocol.parse_decision(decision.ljust(8192)) == "A"
    with pytest.raises(ValueError):
        protocol.parse_review(review.ljust(8193))
    with pytest.raises(ValueError):
        protocol.parse_decision(decision.ljust(8193))
