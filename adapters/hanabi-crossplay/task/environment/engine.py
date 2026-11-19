"""Deterministic full-Hanabi world engine backed by OpenSpiel 2.0.2."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from typing import Any

# The OpenSpiel wheel exposes this compiled extension without Python type stubs.
pyspiel = importlib.import_module("pyspiel")

_HANABI_CONFIG: dict[str, Any] = {
    "players": 2,
    "colors": 5,
    "ranks": 5,
    "hand_size": 5,
    "observation_type": "card_knowledge",
    "random_start_player": False,
    "seed": 1,
}
_RANK_MULTIPLICITIES = (3, 2, 2, 2, 1)
_REVEAL_COLOR_RE = re.compile(r"^\(Reveal player \+1 color ([RYGWB])\)$")
_REVEAL_RANK_RE = re.compile(r"^\(Reveal player \+1 rank ([1-5])\)$")
_CARD_RE = re.compile(r"^([RYGWB])([1-5])\s*\|\|")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _build_deck(deck_id: int) -> tuple[int, ...]:
    if type(deck_id) is not int or deck_id < 0:
        raise ValueError(f"deck_id must be a non-negative int, got {deck_id!r}")
    keyed: list[tuple[bytes, int]] = []
    for color in range(5):
        for rank_index, count in enumerate(_RANK_MULTIPLICITIES):
            action_id = color * 5 + rank_index
            for copy_index in range(count):
                token = f"seatlab-hanabi-deck-v1:{deck_id}:{action_id}:{copy_index}"
                keyed.append(
                    (hashlib.sha256(token.encode("ascii")).digest(), action_id)
                )
    keyed.sort(key=lambda item: item[0])
    return tuple(action_id for _, action_id in keyed)


def _partner_hand_from_observation(observation: str) -> list[tuple[str, str]]:
    """Extract partner cards visible in the acting player's observation string."""
    lines = observation.splitlines()
    try:
        hands_idx = lines.index("Hands:")
        sep_idx = lines.index("-----", hands_idx + 1)
    except ValueError as exc:
        raise RuntimeError(
            "Unsupported OpenSpiel Hanabi observation format: missing Hands/separator"
        ) from exc

    cards: list[tuple[str, str]] = []
    reached_deck = False
    for line in lines[sep_idx + 1 :]:
        if line.startswith("Deck size:"):
            reached_deck = True
            break
        if line == "Cur player":
            continue
        match = _CARD_RE.match(line)
        if match is None:
            raise RuntimeError(
                f"Unsupported OpenSpiel Hanabi partner-card line: {line!r}"
            )
        cards.append((match.group(1), match.group(2)))

    if not reached_deck or not (1 <= len(cards) <= 5):
        raise RuntimeError(
            "Unsupported OpenSpiel Hanabi observation format: malformed partner hand"
        )
    return cards


def _touched_positions(observation: str, action_text: str) -> list[int] | None:
    match = _REVEAL_COLOR_RE.match(action_text)
    field = 0
    if match is None:
        match = _REVEAL_RANK_RE.match(action_text)
        field = 1
    if match is None:
        return None
    target = match.group(1)
    cards = _partner_hand_from_observation(observation)
    positions = [idx for idx, card in enumerate(cards) if card[field] == target]
    if not positions:
        raise RuntimeError(f"Reveal action {action_text!r} matched no partner cards")
    return positions


class Game:
    """World-side full-Hanabi engine with deterministic physical deck dealing."""

    def __init__(self, deck_id: int) -> None:
        self._deck = _build_deck(deck_id)
        self._deck_cursor = 0
        self._deck_sha256 = _canonical_sha256(list(self._deck))
        self._game = pyspiel.load_game("hanabi", _HANABI_CONFIG)
        self._max_decisions = int(self._game.max_game_length())
        self._state = self._game.new_initial_state()
        self._decisions = 0
        self._last_hint_positions: tuple[int, ...] | None = None
        self._advance_chance()

    @property
    def deck_sha256(self) -> str:
        return self._deck_sha256

    @property
    def player(self) -> int | None:
        if self._state.is_terminal():
            return None
        current = int(self._state.current_player())
        if current not in (0, 1):
            raise RuntimeError(f"Expected active player 0 or 1, got {current}")
        return current

    @property
    def turn(self) -> int:
        return self._decisions + 1

    def view(self) -> dict[str, Any]:
        actor = self.player
        if actor is None:
            raise RuntimeError("Cannot build actor view for a terminal game")
        legal = [
            {"id": int(a), "text": self._state.action_to_string(actor, a)}
            for a in self._state.legal_actions(actor)
        ]
        last_hint = (
            None
            if self._last_hint_positions is None
            else {"positions": list(self._last_hint_positions)}
        )
        return {
            "observation": self._state.observation_string(actor),
            "legal": legal,
            "last_hint": last_hint,
        }

    def apply(self, action_id: int) -> None:
        actor = self.player
        if actor is None:
            raise RuntimeError("Cannot apply action to a terminal game")
        if type(action_id) is not int:
            raise ValueError(f"action_id must be an int, got {action_id!r}")
        if action_id not in self._state.legal_actions(actor):
            raise ValueError(f"Illegal action {action_id} for player {actor}")
        if self._decisions >= self._max_decisions:
            raise RuntimeError(
                f"Decision count exceeded native max_game_length ({self._max_decisions})"
            )
        action_text = self._state.action_to_string(actor, action_id)
        touched = _touched_positions(self._state.observation_string(actor), action_text)
        self._state.apply_action(action_id)
        self._decisions += 1
        self._last_hint_positions = None if touched is None else tuple(touched)
        self._advance_chance()

    def scores(self) -> list[int]:
        if not self._state.is_terminal():
            raise RuntimeError("Game is not terminal; no final score exists")
        returns = self._state.returns()
        if len(returns) != 2:
            raise RuntimeError(f"Expected 2 player returns, got {len(returns)}")
        result: list[int] = []
        for value in returns:
            rounded = round(float(value))
            if abs(float(value) - rounded) > 1e-9 or not 0 <= rounded <= 25:
                raise RuntimeError(f"Unexpected Hanabi terminal return: {value!r}")
            result.append(int(rounded))
        return result

    def _advance_chance(self) -> None:
        while not self._state.is_terminal() and self._state.is_chance_node():
            if self._deck_cursor >= len(self._deck):
                raise RuntimeError("Physical deck exhausted at OpenSpiel chance node")
            chance_action = self._deck[self._deck_cursor]
            legal_chance = {int(a) for a, _ in self._state.chance_outcomes()}
            if chance_action not in legal_chance:
                raise RuntimeError(
                    f"Deck card {chance_action} at index {self._deck_cursor} is illegal"
                )
            self._state.apply_action(chance_action)
            self._deck_cursor += 1
