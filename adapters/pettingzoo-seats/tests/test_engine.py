"""World tests against installed PettingZoo, not a hand-rolled game."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
_FILE = Path(__file__).resolve().parents[1] / "runtime" / "engine.py"
_LOADED = importlib.util.spec_from_file_location("pz_seats_engine", _FILE)
engine = importlib.util.module_from_spec(_LOADED)
assert _LOADED is not None and _LOADED.loader is not None
_LOADED.loader.exec_module(engine)


def _spec(api: str, factory: str, **kwargs: object) -> dict[str, object]:
    return {
        "schema": "seatlab.pz-world/v1",
        "api": api,
        "factory": factory,
        "kwargs": kwargs,
        "roles": {"p0": "player_0", "p1": "player_1"},
        "seed": 19,
        "max_world_steps": 128,
    }


def _sha(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def test_parallel_rps_matches_library_returns() -> None:
    spec = _spec("parallel", "pettingzoo.classic.rps_v2:parallel_env", max_cycles=3)
    script = [(0, 1), (1, 1), (2, 0)]
    world = engine.World(spec)
    try:
        assert set(world.pending()) == {"p0", "p1"}
        for a0, a1 in script:
            world.advance({"p0": a0, "p1": a1})
        assert world.pending() == {}
        done = world.finish()
    finally:
        world.close()
    from pettingzoo.classic.rps_v2 import parallel_env

    ref = parallel_env(max_cycles=3)
    try:
        ref.reset(seed=19)
        totals = {"player_0": 0, "player_1": 0}
        truncs: dict[str, bool] = {}
        for a0, a1 in script:
            _, rewards, _, truncs, _ = ref.step({"player_0": a0, "player_1": a1})
            for agent, reward in rewards.items():
                totals[agent] += reward
    finally:
        ref.close()
    assert done["case_sha256"] == _sha(spec)
    assert done["returns"] == {"p0": totals["player_0"], "p1": totals["player_1"]}
    assert done["terminated"] == {"p0": False, "p1": False}
    assert done["truncated"] == {
        "p0": bool(truncs["player_0"]),
        "p1": bool(truncs["player_1"]),
    }
    assert [row["step"] for row in done["steps"]] == [1, 2, 3]
    first = done["steps"][0]["decisions"]["p0"]
    assert first["view_sha256"] == _sha(first["view"]) and first["view"][
        "legal_actions"
    ] == [0, 1, 2]
    seen = done["steps"][1]["decisions"]
    assert (
        seen["p0"]["view"]["observation"] == 1
        and seen["p1"]["view"]["observation"] == 0
    )


def test_connect_four_aec_preserves_status_and_sums_step_rewards() -> None:
    spec = _spec("aec-turns", "pettingzoo.classic.connect_four_v3:env")
    # p0 sorts first but is not the native starter. Selection must win.
    spec["roles"] = {"p0": "player_1", "p1": "player_0"}
    moves = [0, 1, 0, 1, 0, 1, 0]
    world = engine.World(spec)
    try:
        assert set(world.pending()) == {"p1"}
        world.advance({"p1": moves[0]})
        assert set(world.pending()) == {"p0"}
        world.advance({"p0": moves[1]})
        for action in moves[2:]:
            world.advance({next(iter(world.pending())): action})
        assert world.pending() == {}
        done = world.finish()
    finally:
        world.close()
    from pettingzoo.classic.connect_four_v3 import env as cf_env

    ref = cf_env()
    totals = {"player_0": 0, "player_1": 0}
    try:
        ref.reset(seed=19)
        for action in moves:
            ref.step(action)
            for agent, reward in ref.rewards.items():
                totals[agent] += reward
            while ref.agents and (
                ref.terminations[ref.agent_selection]
                or ref.truncations[ref.agent_selection]
            ):
                ref.step(None)
    finally:
        ref.close()
    assert totals == {"player_0": 1, "player_1": -1}
    assert done["returns"] == {"p1": 1, "p0": -1}
    assert done["terminated"] == {"p0": True, "p1": True}
    assert done["truncated"] == {"p0": False, "p1": False}
    assert [next(iter(row["decisions"])) for row in done["steps"]] == [
        "p1",
        "p0",
    ] * 3 + ["p1"]


def test_simultaneous_aec_wrapper_is_rejected() -> None:
    with pytest.raises(ValueError, match="is_parallelizable"):
        engine.World(_spec("aec-turns", "pettingzoo.classic.rps_v2:env", max_cycles=2))


def test_illegal_partial_and_masked_actions_do_not_step() -> None:
    parallel = engine.World(
        _spec("parallel", "pettingzoo.classic.rps_v2:parallel_env", max_cycles=2)
    )
    try:
        before = parallel.pending()
        with pytest.raises(ValueError, match="active seats"):
            parallel.advance({"p0": 0})
        assert parallel.pending() == before
    finally:
        parallel.close()
    world = engine.World(_spec("aec-turns", "pettingzoo.classic.connect_four_v3:env"))
    try:
        for _ in range(3):
            world.advance({"p0": 0})
            world.advance({"p1": 0})
        before = world.pending()
        assert set(before) == {"p0"} and 0 not in before["p0"]["legal_actions"]
        with pytest.raises(ValueError, match="illegal"):
            world.advance({"p0": 0})
        assert world.pending() == before
        world.advance({"p0": before["p0"]["legal_actions"][0]})
    finally:
        world.close()
