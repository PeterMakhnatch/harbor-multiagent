import json

import pytest

from examples.seatlab.visibility import boxes_choice as choice
from examples.seatlab.visibility.boxes_packet import Case


def _case() -> Case:
    return Case(
        instance=7,
        own=((1, 1, False), (11, 2, True)),
        peers=(1, 12, 60),
        budget_left=7,
        earlier_observation=False,
    )


def test_reflection_preserves_room_results_but_hides_unassigned_peer_numbers():
    case = _case()
    visible = choice.prompt(case, choice.Cell(7, 1, 1, "shared")).splitlines()
    hidden = choice.prompt(case, choice.Cell(7, 1, 1, "own")).splitlines()
    diffs = [(a, b) for a, b in zip(visible, hidden, strict=True) if a != b]
    assert diffs == [
        (
            "Box numbers previously opened by teammates: [1,49,60]",
            "Box numbers previously opened by teammates: not supplied",
        )
    ]
    own = json.loads(
        next(
            line.split(": ", 1)[1]
            for line in visible
            if line.startswith("Your previous results")
        )
    )
    assert own == [
        {"box": 60, "room": 6, "flag": False},
        {"box": 50, "room": 5, "flag": True},
    ]
    assert "instance" not in "\n".join(visible) and "bx-" not in "\n".join(visible)


@pytest.mark.parametrize(
    ("display", "outcome"),
    [(60, "own_repeat"), (49, "peer_only_repeat"), (48, "globally_new")],
)
def test_reflected_action_scores_against_the_physical_box(display, outcome):
    assert (
        choice.category(_case(), choice.decode(json.dumps({"box": display}), 1))
        == outcome
    )


@pytest.mark.parametrize(
    "response",
    [
        '{"box":1,"box":2}',
        '{"box":true}',
        '{"box":1.0}',
        '{"box":61}',
        '{"box":0}',
        '{"box":2,"next":3}',
    ],
)
def test_invalid_actions_do_not_become_legal_boxes(response):
    with pytest.raises(ValueError):
        choice.decode(response, 1)
