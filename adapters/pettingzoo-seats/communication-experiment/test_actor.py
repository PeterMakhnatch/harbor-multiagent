"""Actor tests for observation shape, direction, and codebook. No world runs."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent / "actor.py"
_SPEC = importlib.util.spec_from_file_location("communication_experiment_actor", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
actor = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(actor)

# Installed mpe2 1.1.1 landmark colors. Unique argmax is the target index.
COLORS = (
    [0.65, 0.15, 0.15],
    [0.15, 0.65, 0.15],
    [0.15, 0.15, 0.65],
)


def listener(
    vx: float,
    vy: float,
    landmarks: list[tuple[float, float]],
    comm: list[float],
) -> list[float]:
    observation: list[float] = [vx, vy]
    for rel_x, rel_y in landmarks:
        observation.extend((rel_x, rel_y))
    observation.extend(comm)
    return observation


def test_observation_uses_shape_not_extra_keys() -> None:
    speaker = {"observation": [1, 9, 1]}
    planted = {
        "observation": [1, 9, 1],
        "legal_actions": [0, 1, 2],
        "role": "listener_0",
        "goal": 0,
        "codebook": "rotate",
        "world": {"seed": 5},
        "case": "s5-plain-plain",
        "action": 9,
    }
    assert actor.decide(speaker, "plain") == 1
    assert actor.decide(planted, "plain") == actor.decide(speaker, "plain")
    assert actor.decide({"observation": COLORS[0]}, "plain") == 0
    assert actor.decide({"observation": COLORS[2]}, "rotate") == 0
    with pytest.raises(ValueError):
        actor.decide({"goal": 1, "legal_actions": [0, 1, 2]}, "plain")
    with pytest.raises(ValueError):
        actor.decide({"observation": [True, 0, 0]}, "plain")
    with pytest.raises(ValueError):
        actor.decide({"observation": [0.5, 0.5, 0.1]}, "plain")
    with pytest.raises(ValueError):
        actor.decide({"observation": [0.0] * 4}, "plain")


def test_direction_threshold_ties_and_velocity() -> None:
    far = [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    comm = [1.0, 0.0, 0.0]
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.1, 0.0), (0, 0), (0, 0)], comm)}, "plain"
        )
        == 2
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.059, 0.0), (0, 0), (0, 0)], comm)},
            "plain",
        )
        == 0
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.06, 0.0), (0, 0), (0, 0)], comm)},
            "plain",
        )
        == 2
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(-0.1, 0.1), (0, 0), (0, 0)], comm)},
            "plain",
        )
        == 1
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.1, -0.1), (0, 0), (0, 0)], comm)},
            "plain",
        )
        == 2
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.0, 0.1), (0, 0), (0, 0)], comm)}, "plain"
        )
        == 4
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.0, -0.1), (0, 0), (0, 0)], comm)},
            "plain",
        )
        == 3
    )
    assert (
        actor.decide(
            {"observation": listener(0, 0, [(0.01, -0.1), (0, 0), (0, 0)], comm)},
            "plain",
        )
        == 3
    )
    cancelled = listener(1.0, 0.0, [(0.6, 0.0), (9.0, 9.0), (9.0, 9.0)], comm)
    steered = listener(1.0, 0.0, [(0.6, 0.1), (0.0, 0.0), (0.0, 0.0)], comm)
    assert actor.decide({"observation": cancelled}, "plain") == 0
    assert actor.decide({"observation": steered}, "plain") == 4
    assert actor.decide({"observation": listener(0, 0, far, comm)}, "blind") == 0


def test_codebook_offsets_and_exact_zero_signal() -> None:
    idle = listener(0, 0, [(1.0, 0.0), (0.0, 1.0), (0.0, -1.0)], [0.0, 0.0, -0.0])
    for codebook in ("plain", "rotate", "blind"):
        assert actor.decide({"observation": idle}, codebook) == 0
    right = [(1.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    up = [(0.0, 0.0), (0.0, 0.0), (0.0, 1.0)]
    # Channel 0 with rotate selects landmark 2.
    rotated = listener(0, 0, [(1.0, 0.0), (0.0, 0.0), (0.0, 1.0)], [1.0, 0.0, 0.0])
    assert actor.decide({"observation": rotated}, "rotate") == 4
    assert actor.decide({"observation": rotated}, "plain") == 2
    # Blind ignores a live channel and pursues landmark 0.
    blinded = listener(0, 0, [(1.0, 0.0), (0.0, 1.0), (0.0, -1.0)], [0.0, 0.0, 1.0])
    assert actor.decide({"observation": blinded}, "blind") == 2
    # A nonzero component is not the all-zero wait, even if tiny.
    tiny = listener(0, 0, up, [0.0, 0.0, 1e-12])
    assert actor.decide({"observation": tiny}, "plain") == 4
    assert (
        actor.decide({"observation": listener(0, 0, right, [0.0, 1.0, 0.0])}, "plain")
        == 0
    )
    assert actor.decide({"observation": COLORS[1]}, "rotate") == 2
    assert actor.decide({"observation": COLORS[2]}, "plain") == 2
    with pytest.raises(ValueError):
        actor.decide({"observation": COLORS[0]}, "blind")
    with pytest.raises(ValueError):
        actor.decide({"observation": listener(0, 0, right, [1.0, 1.0, 0.0])}, "plain")


def test_stdin_ignores_extra_keys_and_keeps_no_process_state(tmp_path: Path) -> None:
    (tmp_path / "observation.json").write_text(
        '{"observation":[9,0,0]}\n', encoding="utf-8"
    )
    first = {
        "observation": COLORS[1],
        "goal": 0,
        "role": "speaker_0",
        "legal_actions": [4],
    }
    second = {"observation": COLORS[0], "codebook": "rotate", "world": {"seed": 73}}
    outputs = []
    for view, codebook in ((first, "plain"), (second, "rotate")):
        result = subprocess.run(
            [sys.executable, str(_PATH), codebook],
            input=json.dumps(view).encode("utf-8"),
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert (
            result.stdout
            == json.dumps(
                {"action": actor.decide(view, codebook)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        outputs.append(result.stdout)
    assert outputs == [b'{"action":1}\n', b'{"action":1}\n']
    again = actor.decide(first, "plain")
    assert again == 1
    assert actor.decide(second, "rotate") == 1
    failed = subprocess.run(
        [sys.executable, str(_PATH), "plain"],
        input=b'{"goal": 1, "legal_actions": [0, 1, 2]}\n',
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert failed.returncode != 0
    assert failed.stdout == b""
    source = _PATH.read_text(encoding="utf-8")
    for banned in ("mpe2", "numpy", "pettingzoo", "program_seat", "reference"):
        assert banned not in source
