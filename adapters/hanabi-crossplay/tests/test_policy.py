"""Unit tests for deterministic Hanabi cross-play L/R convention policies."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_POLICY_PATH = (
    Path(__file__).resolve().parent.parent
    / "task"
    / "environment"
    / "player"
    / "policy.py"
)
_SPEC = importlib.util.spec_from_file_location("hanabi_crossplay_policy", _POLICY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
policy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(policy)


def _make_obs(
    partner_cards: list[str],
    fireworks: str = "R0 Y0 G0 W0 B0 ",
    own_count: int = 5,
    deck_size: int = 40,
    info_tokens: int = 8,
    life_tokens: int = 3,
    discards: str = "",
) -> str:
    own_lines = ["XX || XX|RYGWB12345"] * own_count
    partner_lines = [f"{card} || XX|RYGWB12345" for card in partner_cards]
    lines = [
        f"Life tokens: {life_tokens}",
        f"Info tokens: {info_tokens}",
        f"Fireworks: {fireworks}",
        "Hands:",
        "Cur player",
        *own_lines,
        "-----",
        *partner_lines,
        f"Deck size: {deck_size}",
        f"Discards: {discards}" if discards else "Discards:",
    ]
    return "\n".join(lines)


def _legal_actions(
    partner_cards: list[str],
    *,
    hand_size: int = 5,
    allow_discard: bool = True,
    allow_reveal: bool = True,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if allow_discard:
        for i in range(hand_size):
            actions.append({"id": i, "text": f"(Discard {i})"})
    for i in range(hand_size):
        actions.append({"id": 5 + i, "text": f"(Play {i})"})
    if allow_reveal:
        colors = ["R", "Y", "G", "W", "B"]
        present_colors = {c[0] for c in partner_cards}
        for idx, color in enumerate(colors):
            if color in present_colors:
                actions.append(
                    {"id": 10 + idx, "text": f"(Reveal player +1 color {color})"}
                )
        present_ranks = {int(c[1]) for c in partner_cards}
        for rank in range(1, 6):
            if rank in present_ranks:
                actions.append(
                    {"id": 14 + rank, "text": f"(Reveal player +1 rank {rank})"}
                )
    return actions


def _make_view(
    partner_cards: list[str],
    *,
    fireworks: str = "R0 Y0 G0 W0 B0 ",
    last_hint: dict[str, Any] | None = None,
    hand_size: int = 5,
    allow_discard: bool = True,
    allow_reveal: bool = True,
) -> dict[str, Any]:
    return {
        "observation": _make_obs(
            partner_cards,
            fireworks=fireworks,
            own_count=hand_size,
            info_tokens=7 if allow_discard else 8,
        ),
        "legal": _legal_actions(
            partner_cards,
            hand_size=hand_size,
            allow_discard=allow_discard,
            allow_reveal=allow_reveal,
        ),
        "last_hint": last_hint,
    }


def test_recipient_honors_immediate_clue_by_convention() -> None:
    # Even when a playable hint is available for partner, recipient plays the hinted slot.
    view = _make_view(
        ["R1", "Y1", "G3", "W4", "B5"],
        last_hint={"positions": [1, 3, 4]},
    )
    assert policy.choose(view, "L") == 6  # (Play 1)
    assert policy.choose(view, "R") == 9  # (Play 4)


def test_recipient_handles_unsorted_hint_positions_and_illegal_fallback() -> None:
    unsorted_view = _make_view(
        ["R3", "Y4", "G5", "W3", "B4"],
        last_hint={"positions": [3, 0, 2]},
    )
    assert policy.choose(unsorted_view, "L") == 5  # (Play 0)
    assert policy.choose(unsorted_view, "R") == 8  # (Play 3)

    # If the indicated slot is somehow not legal to play (e.g., shrunk hand), fall through.
    shrunk_view = _make_view(
        ["R1", "Y4", "G5", "W3"],
        hand_size=3,
        last_hint={"positions": [4]},
    )
    # Falls through to lowest-ID beneficial hint: (Reveal player +1 color R) -> 10.
    assert policy.choose(shrunk_view, "L") == 10
    assert policy.choose(shrunk_view, "R") == 10


def test_unsafe_hint_rejected_even_when_another_touched_card_is_playable() -> None:
    # Partner hand: pos 0 is R3 (not playable), pos 2 is R1 (playable at R0),
    # pos 3 is G4 (not playable), pos 4 is B1 (playable at B0).
    # For 'L':
    #   color R touches [0, 2] -> leftmost is 0 (R3, unplayable) -> REJECTED.
    #   rank 1 touches [2, 4] -> leftmost is 2 (R1, playable) -> ACCEPTED (id 15).
    #   color B touches [4] -> leftmost is 4 (B1, playable) -> ACCEPTED (id 14).
    #   Smallest legal action ID for L is 14 (color B).
    # For 'R':
    #   color R touches [0, 2] -> rightmost is 2 (R1, playable) -> ACCEPTED (id 10).
    #   color B touches [4] -> rightmost is 4 (B1, playable) -> ACCEPTED (id 14).
    #   rank 1 touches [2, 4] -> rightmost is 4 (B1, playable) -> ACCEPTED (id 15).
    #   Smallest legal action ID for R is 10 (color R).
    view = _make_view(["R3", "Y4", "R1", "G4", "B1"])
    assert policy.choose(view, "L") == 14
    assert policy.choose(view, "R") == 10


def test_unsafe_hint_rejected_entirely_falls_back_to_discard() -> None:
    # Only playable card is R1 at pos 2, surrounded by unplayable R4 (pos 0) and G1-not-present/Y1?
    # Use fireworks R0 Y1 G1 W1 B1 so only R1 is playable.
    # Partner holds: [R4, Y1, R1, R5, G1].
    #   color R touches [0, 2, 3]: L -> pos 0 (R4 unplayable), R -> pos 3 (R5 unplayable).
    #   rank 1 touches [1, 2, 4]: L -> pos 1 (Y1 unplayable at Y1), R -> pos 4 (G1 unplayable at G1).
    # Neither convention has any safe playable reveal -> must discard lowest legal position (0).
    view = _make_view(
        ["R4", "Y1", "R1", "R5", "G1"],
        fireworks="R0 Y1 G1 W1 B1 ",
        allow_discard=True,
    )
    assert policy.choose(view, "L") == 0  # (Discard 0)
    assert policy.choose(view, "R") == 0  # (Discard 0)


def test_tie_break_picks_smallest_legal_action_id() -> None:
    # Both color R (10), color G (12), and rank 2 (16) indicate playable cards under L and R.
    # Fireworks: R1 Y0 G1 W0 B0 -> R2 and G2 are playable.
    # Partner hand: [R2, G2, Y4, W5, B3].
    view = _make_view(
        ["R2", "G2", "Y4", "W5", "B3"],
        fireworks="R1 Y0 G1 W0 B0 ",
    )
    assert policy.choose(view, "L") == 10  # color R (10) < color G (12) < rank 2 (16)
    assert policy.choose(view, "R") == 10

    # Shuffle legal list order to verify tie-break uses numeric action ID, not list order.
    reversed_view = {**view, "legal": list(reversed(view["legal"]))}
    assert policy.choose(reversed_view, "L") == 10
    assert policy.choose(reversed_view, "R") == 10


def test_valid_mapping_of_all_colors_and_ranks_against_fireworks() -> None:
    # Test every color R, Y, G, W, B and every rank 1..5 at its exact next firework level.
    cases = [
        ("R1", "R0 Y5 G5 W5 B5 ", 10, 15),
        ("Y2", "R5 Y1 G5 W5 B5 ", 11, 16),
        ("G3", "R5 Y5 G2 W5 B5 ", 12, 17),
        ("W4", "R5 Y5 G5 W3 B5 ", 13, 18),
        ("B5", "R5 Y5 G5 W5 B4 ", 14, 19),
    ]
    for card, fireworks, color_id, rank_id in cases:
        # Place another unplayable card of same color at pos 0 so color reveal is unsafe for L,
        # forcing L to choose the rank reveal while R picks the lower-ID color reveal.
        blocker = f"{card[0]}{1 if card[1] != '1' else 2}"
        # Filler has a completed firework (5) and differs from both card's color and rank.
        filler = "Y5" if card[0] == "R" else ("R5" if card[1] != "5" else "R2")
        hand = [blocker, filler, filler, filler, card]
        view = _make_view(hand, fireworks=fireworks)
        assert policy.choose(view, "L") == rank_id
        assert policy.choose(view, "R") == color_id


def test_unusable_fallback_discard_then_play_lowest_position() -> None:
    # No playable partner cards, discards available -> discard lowest legal position.
    view_with_discard = _make_view(
        ["R3", "Y4", "G5", "W2", "B4"],
        allow_discard=True,
        allow_reveal=True,
    )
    assert policy.choose(view_with_discard, "L") == 0  # (Discard 0)
    assert policy.choose(view_with_discard, "R") == 0  # (Discard 0)

    # Discards unavailable (e.g. max 8 info tokens) and no playable hint -> play lowest position.
    view_no_discard = _make_view(
        ["R3", "Y4", "G5", "W2", "B4"],
        allow_discard=False,
        allow_reveal=True,
    )
    assert policy.choose(view_no_discard, "L") == 5  # (Play 0)
    assert policy.choose(view_no_discard, "R") == 5  # (Play 0)

    # Zero info tokens (no reveals legal) and discards available -> discard lowest legal position.
    view_no_reveal = _make_view(
        ["R1", "Y1", "G1", "W1", "B1"],
        allow_discard=True,
        allow_reveal=False,
    )
    assert policy.choose(view_no_reveal, "L") == 0  # (Discard 0)
    assert policy.choose(view_no_reveal, "R") == 0  # (Discard 0)

    # Non-zero lowest legal discard/play position (defensive check).
    sparse_discard_view = {
        "observation": _make_obs(["R3", "Y4", "G5", "W2", "B4"]),
        "legal": [
            {"id": 3, "text": "(Discard 3)"},
            {"id": 1, "text": "(Discard 1)"},
            {"id": 7, "text": "(Play 2)"},
        ],
        "last_hint": None,
    }
    assert policy.choose(sparse_discard_view, "L") == 1

    sparse_play_view = {
        "observation": _make_obs(["R3", "Y4", "G5", "W2", "B4"]),
        "legal": [
            {"id": 8, "text": "(Play 3)"},
            {"id": 6, "text": "(Play 1)"},
        ],
        "last_hint": None,
    }
    assert policy.choose(sparse_play_view, "R") == 6


def test_completed_firework_rank_5_is_never_playable_again() -> None:
    # Firework R5 is complete; a partner holding R5 must NOT be hinted as playable.
    view = _make_view(
        ["R5", "Y4", "G4", "W4", "B4"],
        fireworks="R5 Y0 G0 W0 B0 ",
        allow_discard=True,
    )
    assert policy.choose(view, "L") == 0
    assert policy.choose(view, "R") == 0


def test_invalid_convention_raises() -> None:
    view = _make_view(["R1", "Y2", "G3", "W4", "B5"])
    with pytest.raises(ValueError, match="Unsupported convention"):
        policy.choose(view, "X")
