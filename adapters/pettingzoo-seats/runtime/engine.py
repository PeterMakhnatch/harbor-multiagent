"""Fixed-seat PettingZoo world. Each view is only that actor's observation.

Fixed-role restrictions: ``roles`` is the whole episode population. Harbor names
are fixed service names (``p0``, ``p1``, ...), 2–8 of them. Agent ids must
equal ``possible_agents``, and every seat must be alive at reset. This world
never adds, renames, or respawns a seat. Removal is allowed only after that
seat's terminated or truncated flag has been copied. ``aec-turns`` is only for
a genuine turn-taking game (``metadata["is_parallelizable"] is False``). A
simultaneous game, including an AEC wrapper of one, must use ``api="parallel"``.
Actions must be Discrete. A world-step limit raises; it is not a terminus.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import re
from typing import Any

import numpy as np
from gymnasium.spaces import Discrete
from pettingzoo.utils.env import AECEnv, ParallelEnv

__all__ = ["NATIVE_SCHEMA", "WORLD_SPEC_SCHEMA", "World"]

WORLD_SPEC_SCHEMA = "seatlab.pz-world/v1"
NATIVE_SCHEMA = "seatlab.pz-native/v1"
_SPEC_KEYS = frozenset(
    {"schema", "api", "factory", "kwargs", "roles", "seed", "max_world_steps"}
)
_ROLE = re.compile(r"p[0-9]+$")
_FACTORY = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("value is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _number(value: object) -> int | float:
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, np.generic) and not isinstance(value, np.bool_):
        return _number(value.item())
    raise ValueError(f"expected a finite number, got {type(value).__name__}")


def _normalize(value: object) -> object:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        return _number(value)
    if isinstance(value, np.ndarray):
        if value.dtype == object or not (
            np.issubdtype(value.dtype, np.number) or value.dtype == np.bool_
        ):
            raise ValueError("observation array is not numeric")
        return _normalize(value.tolist())
    if isinstance(value, np.generic):
        return _normalize(value.item())
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("observation keys must be strings")
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise ValueError(f"observation is not JSON-safe: {type(value).__name__}")


def _legal(space: object, mask: object) -> list[int]:
    if not isinstance(space, Discrete):
        raise ValueError("only Discrete action spaces are supported")
    start, count = int(space.start), int(space.n)
    if mask is None:
        return list(range(start, start + count))
    if (
        not isinstance(mask, list)
        or len(mask) != count
        or any(bit not in (0, 1) for bit in mask)
    ):
        raise ValueError("action_mask must be 0/1 and match Discrete.n")
    return [start + offset for offset, bit in enumerate(mask) if bit == 1]


def _load_factory(path: str) -> Any:
    if not isinstance(path, str) or _FACTORY.fullmatch(path) is None:
        raise ValueError("factory must be 'module:callable'")
    module_name, attr = path.split(":", 1)
    try:
        factory = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot import factory {path}") from exc
    if not callable(factory):
        raise ValueError("factory is not callable")
    return factory


def _roles(raw: object) -> dict[str, str]:
    if type(raw) is not dict or not 2 <= len(raw) <= 8:
        raise ValueError("roles must map 2–8 fixed Harbor seats")
    roles: dict[str, str] = {}
    for role, agent in raw.items():
        if type(role) is not str or _ROLE.fullmatch(role) is None:
            raise ValueError(f"role {role!r} is not a fixed pN service name")
        if (
            type(agent) is not str
            or not agent
            or any(char in agent for char in "/\\\x00")
        ):
            raise ValueError(f"agent id for {role} is not a fixed seat name")
        roles[role] = agent
    if len(set(roles.values())) != len(roles):
        raise ValueError("agent ids must be unique")
    return roles


class World:
    """One PettingZoo episode addressed by fixed Harbor roles."""

    def __init__(self, spec: dict[str, Any]) -> None:
        if (
            type(spec) is not dict
            or spec.keys() != _SPEC_KEYS
            or spec["schema"] != WORLD_SPEC_SCHEMA
        ):
            raise ValueError("world spec does not match seatlab.pz-world/v1")
        if spec["api"] not in ("parallel", "aec-turns"):
            raise ValueError("api must be 'parallel' or 'aec-turns'")
        if type(spec["seed"]) is not int or type(spec["max_world_steps"]) is not int:
            raise ValueError("seed and max_world_steps must be ints")
        if spec["max_world_steps"] < 1:
            raise ValueError("max_world_steps must be positive")
        if type(spec["kwargs"]) is not dict:
            raise ValueError("kwargs must be an object")
        _canonical(spec)
        self.case_sha256 = _sha256(spec)
        self._roles = _roles(spec["roles"])
        self._role_of = {agent: role for role, agent in self._roles.items()}
        self._api = str(spec["api"])
        self._seed = spec["seed"]
        self._limit = spec["max_world_steps"]
        self._steps = 0
        self._log: list[dict[str, Any]] = []
        self._returns = {role: 0 for role in self._roles}
        self._terminated = {role: False for role in self._roles}
        self._truncated = {role: False for role in self._roles}
        self._pending: dict[str, dict[str, Any]] = {}
        self._digests: dict[str, str] = {}
        self._observations: dict[str, Any] = {}
        self._infos: dict[str, Any] = {}
        self._done = False
        self._closed = False
        self._env: Any = _load_factory(spec["factory"])(**dict(spec["kwargs"]))
        try:
            self._boot()
        except Exception:
            self.close()
            raise

    def pending(self) -> dict[str, dict[str, Any]]:
        """Return isolated actor views, or {} only when the episode is naturally done."""
        self._require_open()
        if self._done:
            return {}
        if self._steps >= self._limit:
            raise RuntimeError(
                "world-step limit exceeded; this is not a natural terminus"
            )
        return copy.deepcopy(self._pending)

    def advance(self, actions: dict[str, int]) -> None:
        """Apply one joint or turn decision. Illegal input does not step the env."""
        self._require_open()
        if self._done:
            raise RuntimeError("world is complete")
        if self._steps >= self._limit:
            raise RuntimeError(
                "world-step limit exceeded; this is not a natural terminus"
            )
        if not isinstance(actions, dict) or set(actions) != set(self._pending):
            raise ValueError(
                "actions must match the active seats exactly; partial joint steps are rejected"
            )
        chosen = {}
        for role, action in actions.items():
            if (
                type(action) is not int
                or action not in self._pending[role]["legal_actions"]
            ):
                raise ValueError(
                    f"illegal action {action!r} for {role}; not in legal_actions"
                )
            chosen[role] = action
        self._apply(chosen)
        self._steps += 1
        self._log.append({"step": self._steps, "decisions": self._record(chosen)})
        self._pending = {}
        self._digests = {}
        self._refresh()

    def finish(self) -> dict[str, Any]:
        if not self._done:
            raise RuntimeError(
                "world is closed" if self._closed else "world is not complete"
            )
        return {
            "schema": NATIVE_SCHEMA,
            "case_sha256": self.case_sha256,
            "roles": dict(self._roles),
            "steps": copy.deepcopy(self._log),
            "returns": dict(self._returns),
            "terminated": dict(self._terminated),
            "truncated": dict(self._truncated),
        }

    def close(self) -> None:
        env, self._env = self._env, None
        self._closed = True
        if env is not None:
            env.close()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("world is closed")

    def _record(self, actions: dict[str, int]) -> dict[str, dict[str, Any]]:
        return {
            role: {
                "view": copy.deepcopy(self._pending[role]),
                "view_sha256": self._digests[role],
                "action": actions[role],
            }
            for role in sorted(actions)
        }

    def _boot(self) -> None:
        env = self._env
        if self._api == "parallel":
            if not isinstance(env, ParallelEnv):
                raise ValueError("parallel api requires a ParallelEnv")
            self._observations, infos = env.reset(seed=self._seed)
            self._infos = infos if isinstance(infos, dict) else {}
        else:
            if not isinstance(env, AECEnv):
                raise ValueError("aec-turns api requires an AECEnv")
            # Simultaneous games stay on the Parallel API, even behind an AEC wrapper.
            if getattr(env, "metadata", {}).get("is_parallelizable") is not False:
                raise ValueError(
                    "AEC metadata.is_parallelizable must be false; "
                    "use the Parallel API for simultaneous games"
                )
            env.reset(seed=self._seed)
        self._check_roster(at_reset=True)
        self._refresh()

    def _apply(self, actions: dict[str, int]) -> None:
        env = self._env
        if self._api == "parallel":
            mapped = {self._roles[role]: action for role, action in actions.items()}
            obs, rewards, terms, truncs, infos = env.step(mapped)
            self._observations = obs
            self._infos = infos if isinstance(infos, dict) else {}
        else:
            # Instantaneous env.rewards only. last() is cumulative and is not the return.
            env.step(next(iter(actions.values())))
            rewards, terms, truncs = env.rewards, env.terminations, env.truncations
        self._add_rewards(dict(rewards))
        self._absorb(dict(terms), dict(truncs))

    def _refresh(self) -> None:
        if self._api == "aec-turns":
            self._absorb(dict(self._env.terminations), dict(self._env.truncations))
            self._drain_dead()
        self._check_roster(at_reset=False)
        live = self._live()
        if not live:
            missing = [
                role
                for role in self._roles
                if not (self._terminated[role] or self._truncated[role])
            ]
            if missing:
                raise RuntimeError(
                    "missing terminal status for " + ", ".join(sorted(missing))
                )
            self._done = True
            return
        pending = {self._role_of[agent]: self._view(agent) for agent in live}
        self._pending = pending
        self._digests = {role: _sha256(view) for role, view in pending.items()}

    def _drain_dead(self) -> None:
        """Drop terminal AEC seats without frames, after their status is copied."""
        env = self._env
        for _ in self._roles:
            if not env.agents:
                return
            agent = env.agent_selection
            if not (env.terminations[agent] or env.truncations[agent]):
                return
            env.step(None)
        if env.agents and (
            env.terminations[env.agent_selection]
            or env.truncations[env.agent_selection]
        ):
            raise RuntimeError("failed to drain terminal AEC seats")

    def _live(self) -> list[str]:
        env = self._env
        live = []
        for agent in list(env.agents):
            role = self._role_of.get(agent)
            if role is None:
                raise RuntimeError(f"fixed-role world rejected agent birth: {agent!r}")
            terminated, truncated = self._terminated[role], self._truncated[role]
            if self._api == "aec-turns":
                terminated = terminated or bool(env.terminations[agent])
                truncated = truncated or bool(env.truncations[agent])
            if not terminated and not truncated:
                live.append(agent)
        if self._api != "aec-turns":
            live.sort(key=self._role_of.__getitem__)
            return live
        if not env.agents:
            return []
        selected = env.agent_selection
        if selected not in live:
            raise RuntimeError("AEC selection is not a live seat")
        return [selected]

    def _view(self, agent: str) -> dict[str, Any]:
        if self._api == "parallel":
            if agent not in self._observations:
                raise RuntimeError(f"no isolated observation for {agent!r}")
            raw, info = self._observations[agent], self._infos.get(agent, {})
        else:
            raw, info = self._env.observe(agent), self._env.infos.get(agent, {})
        observation = _normalize(raw)
        mask = observation.get("action_mask") if isinstance(observation, dict) else None
        if mask is None and isinstance(info, dict) and "action_mask" in info:
            mask = _normalize(info["action_mask"])
        return {
            "observation": observation,
            "legal_actions": _legal(self._env.action_space(agent), mask),
        }

    def _add_rewards(self, rewards: dict[Any, Any]) -> None:
        for agent, reward in rewards.items():
            role = self._role_of.get(agent)
            if role is None:
                raise RuntimeError(f"reward for unknown agent {agent!r}")
            self._returns[role] = _number(self._returns[role] + _number(reward))

    def _absorb(
        self, terminations: dict[Any, Any], truncations: dict[Any, Any]
    ) -> None:
        for label, flags, sink in (
            ("terminations", terminations, self._terminated),
            ("truncations", truncations, self._truncated),
        ):
            for agent, flag in flags.items():
                role = self._role_of.get(agent)
                if role is None:
                    raise RuntimeError(f"unknown agent in {label}: {agent!r}")
                if flag:
                    sink[role] = True

    def _check_roster(self, *, at_reset: bool) -> None:
        env = self._env
        declared = set(self._role_of)
        if set(env.possible_agents) != declared:
            raise ValueError(
                "declared roles must equal possible_agents; the roster is fixed"
            )
        current = set(env.agents)
        if at_reset and current != declared:
            raise ValueError("all declared seats must be present at reset")
        unknown = current - declared
        if unknown:
            raise RuntimeError(
                f"fixed-role world rejected agent birth: {sorted(unknown)!r}"
            )
        for agent, role in self._role_of.items():
            if agent not in current and not (
                self._terminated[role] or self._truncated[role]
            ):
                raise RuntimeError(
                    f"seat {role} removed before terminal status was recorded"
                )
