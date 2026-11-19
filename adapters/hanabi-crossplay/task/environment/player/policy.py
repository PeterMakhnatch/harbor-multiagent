"""Deterministic memoryless Hanabi cross-play convention policies (L and R)."""

from __future__ import annotations

import re
from typing import Any

_FIREWORKS_RE = re.compile(r"^Fireworks:\s*(.*)$", re.MULTILINE)
_FIREWORK_ITEM_RE = re.compile(r"([RYGWB])([0-5])")
_CARD_RE = re.compile(r"^([RYGWB])([1-5])\s*\|\|")
_PLAY_RE = re.compile(r"^\(Play (\d+)\)$")
_DISCARD_RE = re.compile(r"^\(Discard (\d+)\)$")
_REVEAL_COLOR_RE = re.compile(r"^\(Reveal player \+1 color ([RYGWB])\)$")
_REVEAL_RANK_RE = re.compile(r"^\(Reveal player \+1 rank ([1-5])\)$")


def _select_position(positions: list[int], convention: str) -> int:
    return min(positions) if convention == "L" else max(positions)


def _parse_observation(
    observation: str,
) -> tuple[dict[str, int], list[tuple[str, int]]]:
    fw_match = _FIREWORKS_RE.search(observation)
    if fw_match is None:
        raise ValueError("Missing Fireworks line in observation")
    fireworks = {
        color: int(rank) for color, rank in _FIREWORK_ITEM_RE.findall(fw_match.group(1))
    }
    if len(fireworks) != 5:
        raise ValueError(f"Expected 5 fireworks entries, parsed {len(fireworks)}")

    lines = observation.splitlines()
    try:
        sep_idx = lines.index("-----")
    except ValueError as exc:
        raise ValueError(
            "Missing partner hand separator '-----' in observation"
        ) from exc

    partner_cards: list[tuple[str, int]] = []
    for line in lines[sep_idx + 1 :]:
        if line.startswith("Deck size:"):
            break
        if line.strip() == "Cur player":
            continue
        card_match = _CARD_RE.match(line)
        if card_match is None:
            raise ValueError(f"Unexpected partner hand line: {line!r}")
        partner_cards.append((card_match.group(1), int(card_match.group(2))))

    return fireworks, partner_cards


def choose(view: dict[str, Any], convention: str) -> int:
    """Return a legal action ID from the committed actor view under convention 'L' or 'R'."""
    if convention not in ("L", "R"):
        raise ValueError(f"Unsupported convention: {convention!r}")

    legal = view.get("legal")
    if not isinstance(legal, list) or not legal:
        raise ValueError("Committed view has no legal actions")

    plays: dict[int, int] = {}
    discards: dict[int, int] = {}
    reveals: list[tuple[int, str, str | int]] = []
    for entry in legal:
        action_id = int(entry["id"])
        text = str(entry["text"])
        if m := _PLAY_RE.match(text):
            plays[int(m.group(1))] = action_id
        elif m := _DISCARD_RE.match(text):
            discards[int(m.group(1))] = action_id
        elif m := _REVEAL_COLOR_RE.match(text):
            reveals.append((action_id, "color", m.group(1)))
        elif m := _REVEAL_RANK_RE.match(text):
            reveals.append((action_id, "rank", int(m.group(1))))

    last_hint = view.get("last_hint")
    if isinstance(last_hint, dict):
        raw_positions = last_hint.get("positions")
        if isinstance(raw_positions, list) and raw_positions:
            target_pos = _select_position([int(p) for p in raw_positions], convention)
            if target_pos in plays:
                return plays[target_pos]

    observation = view.get("observation")
    if not isinstance(observation, str):
        raise ValueError("Committed view missing string observation")
    fireworks, partner_cards = _parse_observation(observation)

    playable_reveals: list[int] = []
    for action_id, kind, value in reveals:
        if kind == "color":
            touched = [
                i for i, (color, _rank) in enumerate(partner_cards) if color == value
            ]
        else:
            touched = [
                i for i, (_color, rank) in enumerate(partner_cards) if rank == value
            ]
        if not touched:
            continue
        indicated = _select_position(touched, convention)
        color, rank = partner_cards[indicated]
        if rank == fireworks[color] + 1:
            playable_reveals.append(action_id)

    if playable_reveals:
        return min(playable_reveals)
    if discards:
        return discards[min(discards)]
    if plays:
        return plays[min(plays)]

    raise ValueError("No legal play, discard, or reveal action found in view")
