"""Memoryless speaker-listener policy. One raw actor view on stdin.

python actor.py plain|rotate|blind

The codebook is the executable argument. The view supplies the observation
only. A 3-vector is the speaker. An 11-vector is the listener. stdout is
{"action": int}. The process keeps no state.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any

CODEBOOKS = ("plain", "rotate", "blind")
OFFSET = {"plain": 0, "rotate": 1}
SPEAKER_LENGTH = 3
LISTENER_LENGTH = 11
VELOCITY_SCALE = 0.6
WAIT_LIMIT = 0.06


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in items:
        if key in out:
            raise ValueError(f"duplicate JSON key: {key!r}")
        out[key] = item
    return out


def _reject_constant(token: str) -> Any:
    raise ValueError(token)


def load(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid actor view") from exc


def _component(value: object) -> float:
    if type(value) is bool or type(value) not in (int, float):
        raise ValueError("observation component is not a finite number")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("observation component is not a finite number")
    return float(value)


def _vector(value: object) -> list[float]:
    if type(value) is not list:
        raise ValueError("observation must be a list")
    return [_component(item) for item in value]


def _unique_argmax(values: list[float]) -> int:
    best = max(values)
    indexes = [index for index, item in enumerate(values) if item == best]
    if len(indexes) != 1:
        raise ValueError("argmax is not unique")
    return indexes[0]


def _move(vx: float, vy: float, rel_x: float, rel_y: float) -> int:
    dx = rel_x - VELOCITY_SCALE * vx
    dy = rel_y - VELOCITY_SCALE * vy
    if max(abs(dx), abs(dy)) < WAIT_LIMIT:
        return 0
    if abs(dx) >= abs(dy):
        return 1 if dx < 0 else 2
    return 3 if dy < 0 else 4


def _speaker(observation: list[float], codebook: str) -> int:
    if codebook not in OFFSET:
        raise ValueError("blind is a listener codebook")
    return (_unique_argmax(observation) + OFFSET[codebook]) % 3


def _listener(observation: list[float], codebook: str) -> int:
    signal = observation[8:11]
    if all(item == 0 for item in signal):
        return 0
    if codebook == "blind":
        index = 1
    else:
        index = (_unique_argmax(signal) - OFFSET[codebook]) % 3
    base = 2 + 2 * index
    return _move(
        observation[0], observation[1], observation[base], observation[base + 1]
    )


def decide(view: object, codebook: str) -> int:
    """Return one legal discrete action. Only view['observation'] is read."""
    if codebook not in CODEBOOKS:
        raise ValueError("codebook must be plain, rotate, or blind")
    if type(view) is not dict or "observation" not in view:
        raise ValueError("view must contain an observation")
    observation = _vector(view["observation"])
    if len(observation) == SPEAKER_LENGTH:
        return _speaker(observation, codebook)
    if len(observation) == LISTENER_LENGTH:
        return _listener(observation, codebook)
    raise ValueError("observation length is not a speaker or listener view")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codebook", choices=CODEBOOKS)
    args = parser.parse_args(argv)
    try:
        action = decide(load(sys.stdin.read()), args.codebook)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    sys.stdout.write(
        json.dumps({"action": action}, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
