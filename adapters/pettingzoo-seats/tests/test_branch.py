"""Prefix branch checks. A changed future is computed, not replayed."""

from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
_LOC = importlib.util.spec_from_file_location("pz_branch", ROOT / "branch.py")
branch = importlib.util.module_from_spec(_LOC)
assert _LOC and _LOC.loader
sys.modules["pz_branch"] = branch
_LOC.loader.exec_module(branch)
TID = "11111111-2222-4333-8444-5555555555ab"


def _factory() -> tuple[str, dict]:
    try:
        import pettingzoo.classic.rps_v2  # noqa: F401

        return "pettingzoo.classic.rps_v2:parallel_env", {"max_cycles": 2}
    except ImportError:
        import types
        from gymnasium.spaces import Discrete
        from pettingzoo.utils.env import ParallelEnv

        class Tiny(ParallelEnv):
            metadata = {"is_parallelizable": True}

            def __init__(self, **_k: object) -> None:
                self.possible_agents = ["player_0", "player_1"]
                self.agents = list(self.possible_agents)

            def reset(self, seed: int | None = None, options: dict | None = None):
                self.agents = list(self.possible_agents)
                return {a: 0 for a in self.agents}, {}

            def step(self, actions: dict):
                self.n = getattr(self, "n", 0) + 1
                done, names = self.n >= 2, self.possible_agents
                if done:
                    self.agents = []
                obs = {a: int(actions[a]) for a in names}
                return (
                    obs,
                    dict.fromkeys(names, 0),
                    dict.fromkeys(names, done),
                    dict.fromkeys(names, False),
                    {},
                )

            def action_space(self, _agent: str):
                return Discrete(2)

        module = types.ModuleType("branch_tiny_env")
        module.env = Tiny
        sys.modules["branch_tiny_env"] = module
        return "branch_tiny_env:env", {}


def _source(
    root: Path, *, bad: bool = False, plant: int | None = None
) -> tuple[Path, Path, str]:
    try:
        factory, kwargs = _factory()
    except ImportError:
        pytest.skip("pettingzoo is not installed")
    spec = {
        "schema": "seatlab.pz-world/v1",
        "api": "parallel",
        "factory": factory,
        "kwargs": kwargs,
        "roles": {"p0": "player_0", "p1": "player_1"},
        "seed": 19,
        "max_world_steps": 8,
    }
    path = root / "spec.json"
    path.write_text(json.dumps(spec))
    sys.path.insert(0, str(ROOT / "runtime"))
    from actor import choose
    from engine import World
    from seatlab.author import accepted_frame, episode_from_frames
    from seatlab.tape import canonical, digest, write_lines
    from seatlab.world_order import WORLD_ORDER_SCHEMA

    world = World(json.loads(path.read_text()))
    try:
        live = world.pending()
        while live:
            world.advance({role: choose("low", view) for role, view in live.items()})
            live = world.pending()
        trace = world.finish()
    finally:
        world.close()
    frames, groups = {role: [] for role in trace["roles"]}, []
    for step in trace["steps"]:
        group = []
        for role, item in sorted(step["decisions"].items()):
            view, action = dict(item["view"]), item["action"]
            if bad and step["step"] == 1 and role == "p0":
                view = {**view, "observation": "mismatch"}
            if plant is not None and step["step"] > 1 and role == "p1":
                action = plant
            frames[role].append(
                accepted_frame(
                    turn=len(frames[role]) + 1,
                    view=view,
                    acknowledged_sha256=digest(view),
                    action=action,
                )
            )
            group.append(role)
        groups.append(group)
    episodes = [
        episode_from_frames(
            id=f"pz:{TID}:{role}",
            case_sha256=trace["case_sha256"],
            role=role,
            native_trial_id=TID,
            closed=True,
            frames=frames[role],
        )
        for role in sorted(frames)
    ]
    out = root / "source"
    out.mkdir()
    write_lines(out / "episodes.jsonl", episodes)
    (out / "order.json").write_bytes(
        canonical(
            {
                "schema": WORLD_ORDER_SCHEMA,
                "case_sha256": trace["case_sha256"],
                "native_trial_id": TID,
                "episodes": {ep["role"]: ep["episode_sha256"] for ep in episodes},
                "steps": groups,
            }
        )
        + b"\n"
    )
    return path, out, factory


def test_prefix_mismatch_writes_nothing(tmp_path: Path) -> None:
    spec, source, factory = _source(tmp_path, bad=True)
    out = tmp_path / "branch.json"
    with pytest.raises(ValueError, match="bound view"):
        branch.run(spec, source, 2, ["p0=low", "p1=high"], factory, out)
    assert not out.exists()


def test_changed_future_is_computed(tmp_path: Path) -> None:
    spec, source, factory = _source(tmp_path, plant=99)
    out = tmp_path / "branch.json"
    report = branch.run(spec, source, 2, ["p0=low", "p1=high"], factory, out)
    step = report["simulated_steps"][0]
    action = step["decisions"]["p1"]["action"]
    assert (
        step["step"] == 2
        and action != 99
        and action == step["decisions"]["p1"]["view"]["legal_actions"][-1]
    )
    assert (
        report["schema"] == "seatlab.pz-branch/v1"
        and report["matched_prefix_steps"] == 1
        and "reward" not in report
    )
    assert json.loads(out.read_text())["source_native_trial_id"] == TID


def test_missing_policy_and_trust_write_nothing(tmp_path: Path) -> None:
    spec = {
        "schema": "seatlab.pz-world/v1",
        "api": "parallel",
        "factory": "branch_untrusted_sentinel:build",
        "kwargs": {},
        "roles": {"p0": "player_0", "p1": "player_1"},
        "seed": 19,
        "max_world_steps": 8,
    }
    path, out = tmp_path / "spec.json", tmp_path / "out.json"
    path.write_text(json.dumps(spec))
    kept = tmp_path / "kept.json"
    kept.write_text("keep")
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to("missing-evidence")
    for occupied in (kept, dangling):
        with pytest.raises(FileExistsError):
            branch.run(path, tmp_path, 2, ["p0=low", "p1=high"], "os:system", occupied)
    assert (
        kept.read_text() == "keep" and dangling.is_symlink() and not dangling.exists()
    )
    with pytest.raises(ValueError, match="trust-factory"):
        branch.run(
            path, tmp_path / "source", 2, ["p0=low", "p1=high"], "os:system", out
        )
    with pytest.raises(ValueError, match="policy"):
        branch.run(path, tmp_path / "source", 2, ["p0=low"], spec["factory"], out)
    assert "branch_untrusted_sentinel" not in sys.modules and not out.exists()
