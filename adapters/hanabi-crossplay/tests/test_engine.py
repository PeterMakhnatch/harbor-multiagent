"""Unit tests for the OpenSpiel-backed Hanabi world engine."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

pyspiel = pytest.importorskip("pyspiel")

_ENGINE_PATH = (
    Path(__file__).resolve().parent.parent / "task" / "environment" / "engine.py"
)
_SPEC = importlib.util.spec_from_file_location("hanabi_crossplay_engine", _ENGINE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Failed to load module spec for {_ENGINE_PATH}")
engine = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(engine)

_COLORS = "RYGWB"
_RANKS = "12345"
_MULTIPLICITIES = (3, 2, 2, 2, 1)


def _expected_deck(deck_id: int) -> list[int]:
    items: list[tuple[bytes, int]] = []
    for color in range(5):
        for rank_idx, count in enumerate(_MULTIPLICITIES):
            action_id = color * 5 + rank_idx
            for copy_idx in range(count):
                key = f"seatlab-hanabi-deck-v1:{deck_id}:{action_id}:{copy_idx}".encode(
                    "ascii"
                )
                items.append((hashlib.sha256(key).digest(), action_id))
    items.sort(key=lambda pair: pair[0])
    return [action_id for _, action_id in items]


def _card_str(chance_action_id: int) -> str:
    return f"{_COLORS[chance_action_id // 5]}{_RANKS[chance_action_id % 5]}"


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _parse_deck_size(observation: str) -> int:
    match = re.search(r"^Deck size:\s*(\d+)$", observation, re.MULTILINE)
    if match is None:
        raise AssertionError(f"Missing Deck size line in:\n{observation}")
    return int(match.group(1))


def _parse_hands(observation: str) -> tuple[list[str], list[str]]:
    lines = observation.splitlines()
    hands_idx = lines.index("Hands:")
    sep_idx = lines.index("-----", hands_idx + 1)
    deck_idx = next(i for i, line in enumerate(lines) if line.startswith("Deck size:"))
    own = [line for line in lines[hands_idx + 1 : sep_idx] if line != "Cur player"]
    partner = [line for line in lines[sep_idx + 1 : deck_idx] if line != "Cur player"]
    return own, partner


def test_deck_determinism_repeat_and_distinct_ids() -> None:
    g0_a = engine.Game(0)
    g0_b = engine.Game(0)
    g1 = engine.Game(1)

    expected_0 = _expected_deck(0)
    assert len(expected_0) == 50
    assert g0_a.deck_sha256 == _canonical_sha256(expected_0)
    assert g0_a.deck_sha256 == g0_b.deck_sha256
    assert g0_a.view() == g0_b.view()

    assert g1.deck_sha256 == _canonical_sha256(_expected_deck(1))
    assert g1.deck_sha256 != g0_a.deck_sha256
    assert g1.view()["observation"] != g0_a.view()["observation"]

    with pytest.raises(ValueError):
        engine.Game(-1)
    with pytest.raises(ValueError):
        engine.Game(True)  # type: ignore[arg-type]


def test_actor_private_text_masks_own_cards_and_exposes_partner_hand() -> None:
    deck_id = 0
    deck = _expected_deck(deck_id)
    game = engine.Game(deck_id)

    assert game.player == 0
    assert game.turn == 1
    view0 = game.view()
    assert view0["last_hint"] is None
    assert _parse_deck_size(view0["observation"]) == 40

    own_lines, partner_lines = _parse_hands(view0["observation"])
    assert len(own_lines) == 5
    assert all(line.startswith("XX ||") for line in own_lines)
    assert [line.split(" ||")[0] for line in partner_lines] == [
        _card_str(cid) for cid in deck[5:10]
    ]

    # Advance via a reveal so p1 acts without altering p0's initial 5 cards.
    reveal = next(
        entry for entry in view0["legal"] if entry["text"].startswith("(Reveal ")
    )
    game.apply(reveal["id"])
    assert game.player == 1
    assert game.turn == 2
    view1 = game.view()
    own1, partner1 = _parse_hands(view1["observation"])
    assert all(line.startswith("XX ||") for line in own1)
    assert [line.split(" ||")[0] for line in partner1] == [
        _card_str(cid) for cid in deck[0:5]
    ]


def test_chance_draw_after_plays_and_discards_vs_hints() -> None:
    deck_id = 2
    deck = _expected_deck(deck_id)
    game = engine.Game(deck_id)

    # Hint does not consume a deck card and populates last_hint.positions.
    view_p0 = game.view()
    assert _parse_deck_size(view_p0["observation"]) == 40
    _, partner_before_hint = _parse_hands(view_p0["observation"])
    partner_cards = [line.split(" ||")[0] for line in partner_before_hint]

    color_reveal = next(
        e for e in view_p0["legal"] if e["text"].startswith("(Reveal player +1 color ")
    )
    target_color = color_reveal["text"][-2]
    expected_positions = [
        idx for idx, card in enumerate(partner_cards) if card[0] == target_color
    ]

    game.apply(color_reveal["id"])
    view_p1 = game.view()
    assert _parse_deck_size(view_p1["observation"]) == 40
    assert view_p1["last_hint"] == {"positions": expected_positions}

    # Rank reveal from p1 also keeps deck size unchanged and sets exact touched positions.
    _, p0_hand_lines = _parse_hands(view_p1["observation"])
    p0_cards = [line.split(" ||")[0] for line in p0_hand_lines]
    rank_reveal = next(
        e for e in view_p1["legal"] if e["text"].startswith("(Reveal player +1 rank ")
    )
    target_rank = rank_reveal["text"][-2]
    expected_rank_positions = [
        idx for idx, card in enumerate(p0_cards) if card[1] == target_rank
    ]
    game.apply(rank_reveal["id"])
    view_p0_after_rank = game.view()
    assert _parse_deck_size(view_p0_after_rank["observation"]) == 40
    assert view_p0_after_rank["last_hint"] == {"positions": expected_rank_positions}

    # Play consumes exactly one deck card (deck[10]) and clears last_hint.
    play_action = next(
        e for e in view_p0_after_rank["legal"] if e["text"] == "(Play 0)"
    )
    game.apply(play_action["id"])
    view_p1_after_play = game.view()
    assert _parse_deck_size(view_p1_after_play["observation"]) == 39
    assert view_p1_after_play["last_hint"] is None
    _, partner_after_play = _parse_hands(view_p1_after_play["observation"])
    assert partner_after_play[-1].split(" ||")[0] == _card_str(deck[10])

    # Discard also consumes exactly one deck card (deck[11]) and clears last_hint.
    discard_action = next(
        e for e in view_p1_after_play["legal"] if e["text"] == "(Discard 0)"
    )
    game.apply(discard_action["id"])
    view_p0_after_discard = game.view()
    assert _parse_deck_size(view_p0_after_discard["observation"]) == 38
    assert view_p0_after_discard["last_hint"] is None
    _, partner_after_discard = _parse_hands(view_p0_after_discard["observation"])
    assert partner_after_discard[-1].split(" ||")[0] == _card_str(deck[11])


def test_legal_actions_notation_and_validation() -> None:
    game = engine.Game(3)
    view = game.view()
    legal = view["legal"]
    assert legal
    ids = [entry["id"] for entry in legal]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)

    pattern = re.compile(
        r"^(\(Play [0-4]\)|\(Discard [0-4]\)|\(Reveal player \+1 (color [RYGWB]|rank [1-5])\))$"
    )
    for entry in legal:
        assert type(entry["id"]) is int
        assert isinstance(entry["text"], str)
        assert pattern.match(entry["text"])

    # At 8 initial info tokens, Discard 0 (id=0) is illegal.
    assert 0 not in ids
    with pytest.raises(ValueError):
        game.apply(0)
    with pytest.raises(ValueError):
        game.apply(999)
    with pytest.raises(ValueError):
        game.apply(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        game.apply("5")  # type: ignore[arg-type]


def test_view_is_immutable_copy_for_caller() -> None:
    game = engine.Game(4)
    reveal = next(e for e in game.view()["legal"] if e["text"].startswith("(Reveal "))
    game.apply(reveal["id"])

    v1: dict[str, Any] = game.view()
    original_digest = _canonical_sha256(v1)
    assert v1["last_hint"] is not None

    v1["legal"].clear()
    v1["last_hint"]["positions"].append(99)
    v1["observation"] = "tampered"

    v2 = game.view()
    assert _canonical_sha256(v2) == original_digest
    assert 99 not in v2["last_hint"]["positions"]
    assert v2["legal"]


def test_terminal_guards_and_no_incomplete_score() -> None:
    game = engine.Game(5)

    with pytest.raises(RuntimeError):
        game.scores()

    # Drive game cleanly to terminal by discarding when legal, else revealing, else playing.
    turns = 0
    while game.player is not None:
        assert game.turn == turns + 1
        legal = game.view()["legal"]
        discards = [e["id"] for e in legal if e["text"].startswith("(Discard ")]
        reveals = [e["id"] for e in legal if e["text"].startswith("(Reveal ")]
        action_id = (
            discards[0] if discards else (reveals[0] if reveals else legal[0]["id"])
        )
        game.apply(action_id)
        turns += 1

    assert game.player is None
    assert game.turn == turns + 1
    scores = game.scores()
    assert len(scores) == 2
    assert scores[0] == scores[1]
    assert all(type(s) is int and 0 <= s <= 25 for s in scores)

    with pytest.raises(RuntimeError):
        game.view()
    with pytest.raises(RuntimeError):
        game.apply(5)


def test_unsupported_observation_format_fails_clearly() -> None:
    with pytest.raises(RuntimeError, match="Unsupported OpenSpiel Hanabi observation"):
        engine._partner_hand_from_observation("Not a valid Hanabi observation")
    with pytest.raises(RuntimeError, match="Unsupported OpenSpiel Hanabi partner-card"):
        engine._partner_hand_from_observation(
            "Hands:\nXX || XX\n-----\nBAD_CARD\nDeck size: 40\n"
        )
