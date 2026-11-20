import io
import json
from dataclasses import replace
from itertools import product

import pytest

from examples.seatlab.visibility import intent_protocol as protocol
from examples.seatlab.visibility.boxes_packet import Case
from examples.seatlab.visibility.intent_packet import World


def _world() -> World:
    return World(
        instance=1,
        seats=(
            Case(1, ((6, 1, True), (5, 1, True)), (22, 53), 7, False),
            Case(1, ((22, 3, False),), (5, 6, 53), 8, True),
            Case(1, ((53, 6, True),), (5, 6, 22), 8, False),
        ),
    )


def test_registered_plan_and_call_order(capsys):
    assert protocol.main(["plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["packet_sha256"] == protocol.intent_packet.PACKET_SHA256
    order = list(product(range(2), (1, 2, 6), range(2), range(3)))
    assert [item["id"] for item in plan["blocks"]] == [
        f"bi-i{world:02d}-m{mapping}-l{rotation}-r{repetition}"
        for repetition, world, mapping, rotation in order
    ]
    all_calls = []
    for item, (repetition, world, mapping, rotation) in zip(
        plan["blocks"], order, strict=True
    ):
        first = (repetition + {1: 0, 2: 1, 6: 2}[world] + mapping + rotation) % 2
        arms = ["masked", "shown"]
        assert item["arms"] == (arms if first == 0 else arms[::-1])
        assert item["calls"] == [
            f"{item['id']}-{stage}-{label}"
            for stage in ["proposal", *item["arms"]]
            for label in "abc"
        ]
        all_calls.extend(item["calls"])
    assert len(all_calls) == len(set(all_calls)) == 324


@pytest.mark.parametrize(
    "rotation,seats", [(0, (0, 1, 2)), (1, (2, 0, 1)), (2, (1, 2, 0))]
)
def test_rotations_show_the_labeled_seats_own_ordered_results(rotation, seats):
    block = protocol.Block(1, 1, rotation, 0)
    histories = (
        '[{"box":55,"flag":true,"room":6},{"box":56,"flag":true,"room":6}]',
        '[{"box":39,"flag":false,"room":4}]',
        '[{"box":8,"flag":true,"room":1}]',
    )
    peer_histories = ("[8,39]", "[8,55,56]", "[39,55,56]")
    for label, seat in enumerate(seats):
        assert block.seat_index(label) == seat
        lines = protocol.prompt(_world(), block, label, "proposal").splitlines()
        assert f"Your label: {'ABC'[label]}" in lines
        assert f"Your previous results, oldest first: {histories[seat]}" in lines
        assert (
            f"Box numbers previously opened by teammates: {peer_histories[seat]}"
            in lines
        )
        assert f"Your remaining opens: {(7, 8, 8)[seat]}" in lines
        assert not any("sealed proposal" in line for line in lines)


@pytest.mark.parametrize("numbering,display", [(0, (11, 32, 44)), (1, (50, 29, 17))])
@pytest.mark.parametrize("label", range(3))
def test_only_peer_proposals_differ_between_revisions(numbering, display, label):
    world = _world()
    block = protocol.Block(1, numbering, 2, 0)
    masked = protocol.prompt(world, block, label, "masked", (11, 32, 44))
    shown = protocol.prompt(world, block, label, "shown", (11, 32, 44))
    peers = {name: display[i] for i, name in enumerate("ABC") if i != label}
    shown_line = "Teammates' sealed proposals: " + json.dumps(
        peers, separators=(",", ":")
    )
    assert f"Your sealed proposal: {display[label]}" in masked.splitlines()
    assert [
        (left, right)
        for left, right in zip(masked.splitlines(), shown.splitlines(), strict=True)
        if left != right
    ] == [("Teammates' sealed proposals: ready, not supplied", shown_line)]


def test_peer_flags_never_change_a_prompt_and_peer_proposals_only_change_shown():
    world = _world()
    first, second, third = world.seats
    peer_flags_changed = replace(
        world,
        seats=(
            first,
            replace(second, own=((22, 3, True),)),
            replace(third, own=((53, 6, False),)),
        ),
    )
    block = protocol.Block(1, 1, 1, 0)  # B is the first source seat.
    original, changed = (11, 32, 44), (12, 32, 45)
    assert protocol.prompt(world, block, 1, "proposal") == protocol.prompt(
        peer_flags_changed, block, 1, "proposal"
    )
    assert protocol.prompt(world, block, 1, "masked", original) == protocol.prompt(
        peer_flags_changed, block, 1, "masked", changed
    )
    assert protocol.prompt(world, block, 1, "shown", original) == protocol.prompt(
        peer_flags_changed, block, 1, "shown", original
    )
    assert protocol.prompt(world, block, 1, "shown", original) != protocol.prompt(
        world, block, 1, "shown", changed
    )


@pytest.mark.parametrize(
    "choices,counts",
    [
        ((56, 56, 56), (3, 1, 2, 0)),
        ((5, 5, 5), (0, 0, 0, 3)),
        ((56, 56, 22), (2, 1, 1, 1)),
        ((56, 57, 58), (3, 3, 0, 0)),
    ],
)
def test_metrics_distinguish_simultaneous_collisions_from_historical_repeats(
    choices, counts
):
    individually_new, distinct_new, collisions, repeats = counts
    assert protocol.native_metrics(_world(), choices) == {
        "individually_new": individually_new,
        "distinct_new": distinct_new,
        "excess_new_collisions": collisions,
        "historical_repeats": repeats,
    }


def test_cli_masked_prompt_accepts_only_the_own_proposal(monkeypatch, capsys):
    monkeypatch.setattr(protocol.intent_packet, "load", lambda: {1: _world()})
    block = protocol.Block(1, 1, 1, 0)
    args = ["prompt", block.id, "--seat", "B", "--stage"]
    assert protocol.main([*args, "masked", "--own", "32"]) == 0
    assert capsys.readouterr().out == protocol.prompt(
        _world(), block, 1, "masked", (11, 32, 44)
    )
    assert protocol.main([*args, "shown", "--proposals", "[11,32,44]"]) == 0
    assert capsys.readouterr().out == protocol.prompt(
        _world(), block, 1, "shown", (11, 32, 44)
    )
    assert protocol.main([*args, "shown", "--own", "32"]) == 1
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    "response,original", [(b'{"box":1}', 60), (b'{"box":1,"box":2}', None)]
)
def test_cli_decode_uses_the_existing_exact_grammar(
    monkeypatch, capsys, response, original
):
    with io.TextIOWrapper(io.BytesIO(response)) as stdin:
        monkeypatch.setattr(protocol.sys, "stdin", stdin)
        code = protocol.main(["decode", "bi-i01-m1-l0-r0"])
    out = capsys.readouterr().out
    if original is None:
        assert code == 1
        assert not out
    else:
        assert code == 0
        assert json.loads(out) == {"box": original}
