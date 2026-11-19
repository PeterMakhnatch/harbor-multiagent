"""Independent direct-engine reference for the speaker-listener cells.

python reference.py ROOT --out NEW.json [--cohort initial|held-out]

ROOT is a directory written by ``study.py prepare`` and, optionally, ``run``:
``plan.json`` (schema ``seatlab.pz-communication-plan/v1``), ``configs/<id>.json``,
``attempts/<id>/exit.json`` and ``trials/<trial_name>/`` with ``result.json`` and
``artifacts/logs/world/pettingzoo.json``. ROOT may be missing or partial: every
registered cell is still reported. Nothing is rerun, retried or scored.

Cohorts: ``initial`` is the original 15 cells (seeds 5, 19, 73). ``held-out`` is a
separate preregistered 60-cell cohort (seeds 101..112 x the same 5 teams). The
default is ``initial`` and its report is unchanged.

The reference drives the installed ``mpe2.simple_speaker_listener_v4`` Parallel
API itself and implements the written policy from the raw observation vector.
It does not import the adapter world, the seat actor, the study runner or any
program seat. Floats use absolute tolerance 1e-6; the measured maxima are
reported. Absent evidence stays null. The JSON holds no host paths.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

SCHEMA = "seatlab.pz-communication-reference/v1"
PLAN_SCHEMA = "seatlab.pz-communication-plan/v1"
NATIVE_SCHEMA = "seatlab.pz-native/v1"
FACTORY = "mpe2.simple_speaker_listener_v4:parallel_env"
GAME = "simple_speaker_listener_v4"
ROLES = {"p0": "speaker_0", "p1": "listener_0"}
KWARGS = {"continuous_actions": False, "max_cycles": 20}
MAX_WORLD_STEPS = 128
PINS = {"mpe2": "1.1.1", "pettingzoo": "1.26.1"}
SEEDS = (5, 19, 73)
HELD_OUT_SEEDS = tuple(range(101, 113))
TEAMS = (
    ("plain-plain", "plain", "plain"),
    ("rotate-rotate", "rotate", "rotate"),
    ("plain-rotate", "plain", "rotate"),
    ("rotate-plain", "rotate", "plain"),
    ("plain-blind", "plain", "blind"),
)


def _cells_for(seeds: tuple[int, ...]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (f"s{seed}-{team}", seed, team, speaker, listener)
        for seed in seeds
        for team, speaker, listener in TEAMS
    )


CELLS = _cells_for(SEEDS)
HELD_OUT_CELLS = _cells_for(HELD_OUT_SEEDS)
DEFAULT_COHORT = "initial"
COHORTS = {"initial": (SEEDS, CELLS), "held-out": (HELD_OUT_SEEDS, HELD_OUT_CELLS)}

BASELINE = "plain-plain"
KINDS = {"rotate-rotate": "matched", "plain-rotate": "cross"}
KINDS |= {"rotate-plain": "cross", "plain-blind": "blind"}
OFFSET = {"plain": 0, "rotate": 1}
TOLERANCE = 1e-6
LEAD, DEADBAND = 0.6, 0.06
SPEAKER_DIM, LISTENER_DIM, PHYSICAL = 3, 11, 8
MAX_MISMATCHES = 32
WORLD_KEYS = frozenset(
    {"schema", "case_sha256", "roles", "steps", "returns", "terminated", "truncated"}
)
Factory = Callable[[], Any]


def _cohort(name: object) -> Any:
    """Look up a registered cohort; an unknown name is an error, never a default."""
    if not isinstance(name, str) or name not in COHORTS:
        raise ValueError(f"unknown cohort: {name!r}")
    return COHORTS[name]


class RefError(Exception):
    """A named failure. The token is public; no message text is published."""

    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.token = token


# ---------------------------------------------------------------- policy


def _vector(value: object, size: int) -> list[float]:
    if type(value) is not list or len(value) != size:
        raise RefError("observation_shape")
    for item in value:
        if type(item) not in (int, float) or not math.isfinite(item):
            raise RefError("observation_value")
    return [float(item) for item in value]


def _unique_argmax(values: list[float]) -> int:
    top = max(values)
    hits = [index for index, value in enumerate(values) if value == top]
    if len(hits) != 1:
        raise RefError("argmax_not_unique")
    return hits[0]


def speak(codebook: str, observation: object) -> int:
    """Send the target colour index, shifted by the speaker codebook."""
    colour = _vector(observation, SPEAKER_DIM)
    return (_unique_argmax(colour) + OFFSET[codebook]) % 3


def channel(observation: object) -> int | None:
    """Raw received channel, or None when all 3 components are zero."""
    signal = _vector(observation, LISTENER_DIM)[PHYSICAL:]
    if all(value == 0 for value in signal):
        return None
    return _unique_argmax(signal)


def target(codebook: str, observation: object) -> int | None:
    """Landmark the listener pursues, or None while it waits for a signal."""
    raw = channel(observation)
    if raw is None:
        return None
    return 0 if codebook == "blind" else (raw - OFFSET[codebook]) % 3


def listen(codebook: str, observation: object) -> int:
    """Wait 0, left 1, right 2, down 3, up 4."""
    goal = target(codebook, observation)
    if goal is None:
        return 0
    vec = _vector(observation, LISTENER_DIM)
    dx = vec[2 + 2 * goal] - LEAD * vec[0]
    dy = vec[3 + 2 * goal] - LEAD * vec[1]
    if max(abs(dx), abs(dy)) < DEADBAND:
        return 0
    if abs(dx) >= abs(dy):
        return 1 if dx < 0 else 2
    return 3 if dy < 0 else 4


# ---------------------------------------------------------------- engine


def _plain(value: object) -> object:
    """numpy -> JSON-safe Python, without importing numpy here."""
    if hasattr(value, "tolist") and not isinstance(value, (list, tuple, dict)):
        return _plain(value.tolist())
    if hasattr(value, "item") and not isinstance(value, (int, float, bool, str)):
        return _plain(value.item())
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    raise RefError("value_type")


def _reward(value: object) -> float | int:
    out = _plain(value)
    if type(out) not in (int, float) or not math.isfinite(out):
        raise RefError("reward_value")
    return out


def _legal(space: object, info: object) -> list[int]:
    count, start = getattr(space, "n", None), getattr(space, "start", 0)
    count, start = _plain(count), _plain(start)
    if type(count) is not int or type(start) is not int or count < 1:
        raise RefError("action_space")
    mask = info.get("action_mask") if isinstance(info, dict) else None
    if mask is None:
        return list(range(start, start + count))
    bits = _plain(mask)
    if type(bits) is not list or len(bits) != count:
        raise RefError("action_mask")
    return [start + i for i, bit in enumerate(bits) if bit == 1]


def official_factory() -> Factory:
    """The installed MPE2 Parallel API with the registered kwargs."""
    from mpe2.simple_speaker_listener_v4 import parallel_env

    return lambda: parallel_env(**KWARGS)


def simulate(factory: Factory, seed: int, speaker: str, listener: str) -> dict:
    """One episode on the official engine; each policy sees only its vector."""
    names = dict(ROLES)
    env = factory()
    try:
        if set(_plain(list(env.possible_agents))) != set(names.values()):
            raise RefError("roster")
        obs, infos = env.reset(seed=seed)
        returns: dict[str, float | int] = dict.fromkeys(names, 0)
        ended, cut = dict.fromkeys(names, False), dict.fromkeys(names, False)
        steps: list[dict[str, Any]] = []
        while True:
            agents = set(_plain(list(env.agents)))
            live = [r for r in sorted(names) if names[r] in agents]
            live = [r for r in live if not (ended[r] or cut[r])]
            if not live:
                if not all(ended[r] or cut[r] for r in names):
                    raise RefError("missing_terminal_status")
                break
            if len(steps) >= MAX_WORLD_STEPS:
                raise RefError("world_step_limit")
            views, actions = {}, {}
            for role in live:
                agent = names[role]
                if agent not in obs:
                    raise RefError("missing_observation")
                info = infos.get(agent, {}) if isinstance(infos, dict) else {}
                view = {
                    "observation": _plain(obs[agent]),
                    "legal_actions": _legal(env.action_space(agent), info),
                }
                if role == "p0":
                    action = speak(speaker, view["observation"])
                else:
                    action = listen(listener, view["observation"])
                if action not in view["legal_actions"]:
                    raise RefError("illegal_action")
                views[role], actions[role] = view, action
            obs, rewards, terms, truncs, infos = env.step(
                {names[role]: action for role, action in actions.items()}
            )
            step_rewards: dict[str, float | int | None] = {}
            for role, agent in names.items():
                got = rewards.get(agent) if isinstance(rewards, dict) else None
                step_rewards[role] = None if got is None else _reward(got)
                if got is not None:
                    returns[role] = returns[role] + step_rewards[role]
                ended[role] = ended[role] or bool(terms.get(agent, False))
                cut[role] = cut[role] or bool(truncs.get(agent, False))
            steps.append(
                {
                    "group": live,
                    "views": views,
                    "actions": actions,
                    "rewards": step_rewards,
                }
            )
        return {
            "names": names,
            "steps": steps,
            "returns": returns,
            "terminated": ended,
            "truncated": cut,
        }
    finally:
        env.close()


def case_sha256(seed: int) -> str:
    spec = {
        "schema": "seatlab.pz-world/v1",
        "api": "parallel",
        "factory": FACTORY,
        "kwargs": dict(KWARGS),
        "roles": dict(ROLES),
        "seed": seed,
        "max_world_steps": MAX_WORLD_STEPS,
    }
    raw = json.dumps(
        spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


# ---------------------------------------------------------------- native


def _json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value and parsed.int else None


def _safe(name: object) -> bool:
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\\" not in name
    )


def _plan(
    root: Path, cells_registered: tuple[tuple[Any, ...], ...] = CELLS
) -> tuple[str, dict[str, str]]:
    """Return (plan status token, cell id -> trial name) for matching cells."""
    raw = _json(root / "plan.json")
    if raw is None:
        return "missing_plan", {}
    cells = raw.get("cells") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or raw.get("schema") != PLAN_SCHEMA:
        return "wrong_plan_schema", {}
    if not isinstance(cells, list):
        return "bad_plan", {}
    want = {cid: (seed, team, sp, li) for cid, seed, team, sp, li in cells_registered}
    names: dict[str, str] = {}
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("id") not in want:
            continue
        cid = cell["id"]
        seed, team, speaker, listener = want[cid]
        same = (
            cell.get("seed") == seed
            and cell.get("team") == team
            and cell.get("speaker") == speaker
            and cell.get("listener") == listener
            and cell.get("game") == GAME
        )
        if same and _safe(cell.get("trial_name")) and cid not in names:
            names[cid] = cell["trial_name"]
    return "ok", names


def _valid_world(doc: object) -> bool:
    if not isinstance(doc, dict) or set(doc) != WORLD_KEYS:
        return False
    if doc["schema"] != NATIVE_SCHEMA or not isinstance(doc["steps"], list):
        return False
    for key in ("roles", "returns", "terminated", "truncated"):
        if not isinstance(doc[key], dict):
            return False
    for step in doc["steps"]:
        decisions = step.get("decisions") if isinstance(step, dict) else None
        if not isinstance(decisions, dict) or type(step.get("step")) is not int:
            return False
        for item in decisions.values():
            view = item.get("view") if isinstance(item, dict) else None
            if not isinstance(view, dict) or type(item.get("action")) is not int:
                return False
    return True


def _native(root: Path, cid: str, name: str | None) -> dict[str, Any]:
    """Load one native cell. Returns public fields plus a private ``_doc``."""
    row: dict[str, Any] = {
        "status": "unregistered",
        "reason": "not_in_plan",
        "native_trial_id": None,
        "exit_code": None,
        "world_steps": None,
        "decisions": None,
        "returns": None,
        "terminated": None,
        "truncated": None,
        "_doc": None,
    }
    if name is None:
        return row
    claim = root / "attempts" / cid
    attempted = claim.is_dir() and not claim.is_symlink()
    exit_doc = _json(claim / "exit.json") if attempted else None
    if isinstance(exit_doc, dict) and type(exit_doc.get("exit_code")) is int:
        row["exit_code"] = exit_doc["exit_code"]
    trial = root / "trials" / name
    row.update(status="unattempted", reason="not_started")
    if trial.is_symlink():
        row.update(status="invalid", reason="trial_symlink")
        return row
    if not trial.is_dir():
        if attempted:
            row.update(status="invalid", reason="missing_trial")
        return row
    if not attempted:
        row.update(status="invalid", reason="unclaimed_trial")
        return row
    config = _json(root / "configs" / f"{cid}.json")
    if not isinstance(config, dict) or config.get("trial_name") != name:
        row.update(status="invalid", reason="config_mismatch")
        return row
    result = _json(trial / "result.json")
    if not isinstance(result, dict):
        row.update(status="invalid", reason="missing_result")
        return row
    row["native_trial_id"] = _uuid(result.get("id"))
    if result.get("exception_info") is not None:
        row.update(status="invalid", reason="native_exception")
        return row
    doc = _json(trial / "artifacts" / "logs" / "world" / "pettingzoo.json")
    if doc is None:
        row.update(status="invalid", reason="missing_world")
        return row
    if not _valid_world(doc):
        row.update(status="invalid", reason="bad_world")
        return row
    row.update(
        status="complete",
        reason=None,
        world_steps=len(doc["steps"]),
        decisions=sum(len(step["decisions"]) for step in doc["steps"]),
        returns=doc["returns"],
        terminated=doc["terminated"],
        truncated=doc["truncated"],
        _doc=doc,
    )
    return row


def native_trace(doc: dict[str, Any]) -> dict[str, Any]:
    """The native world log in the reference trace shape (no step rewards)."""
    steps = []
    for step in doc["steps"]:
        decisions = step["decisions"]
        steps.append(
            {
                "group": sorted(decisions),
                "views": {r: decisions[r]["view"] for r in decisions},
                "actions": {r: decisions[r]["action"] for r in decisions},
                "rewards": None,
            }
        )
    return {
        "names": doc["roles"],
        "steps": steps,
        "returns": doc["returns"],
        "terminated": doc["terminated"],
        "truncated": doc["truncated"],
    }


# ---------------------------------------------------------------- compare


def _number(value: object) -> float | None:
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    return None


class _Ledger:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.count = 0

    def add(self, where: str, step: int | None = None, role: str | None = None) -> None:
        self.count += 1
        if len(self.items) < MAX_MISMATCHES:
            self.items.append({"where": where, "step": step, "role": role})


def _top(current: float | None, value: float) -> float:
    return value if current is None else max(current, value)


def compare(doc: dict[str, Any], trace: dict[str, Any], seed: int) -> dict[str, Any]:
    """Field-by-field native vs reference. Exact except floats (abs 1e-6)."""
    miss = _Ledger()
    exact = dict.fromkeys(
        (
            "case",
            "names",
            "step_count",
            "step_index",
            "groups",
            "legal_actions",
            "actions",
            "terminated",
            "truncated",
        ),
        True,
    )

    def fail(key: str, where: str, step: int | None = None, role: str | None = None):
        exact[key] = False
        miss.add(where, step, role)

    if doc["case_sha256"] != case_sha256(seed):
        fail("case", "case_sha256")
    if doc["roles"] != trace["names"]:
        fail("names", "roles")
    native_steps, ref_steps = doc["steps"], trace["steps"]
    if len(native_steps) != len(ref_steps):
        fail("step_count", "step_count")
    obs_max: float | None = None
    obs_count = decisions = obs_failed = 0
    for index, (got, exp) in enumerate(zip(native_steps, ref_steps), 1):
        if got["step"] != index:
            fail("step_index", "step_index", index)
        group = sorted(got["decisions"])
        if group != exp["group"]:
            fail("groups", "group", index)
        for role in exp["group"]:
            item = got["decisions"].get(role)
            if item is None:
                continue
            decisions += 1
            view, want = item["view"], exp["views"][role]
            if set(view) != {"observation", "legal_actions"}:
                obs_failed += 1
                miss.add("view_keys", index, role)
            if view.get("legal_actions") != want["legal_actions"]:
                fail("legal_actions", "legal_actions", index, role)
            seen, wanted = view.get("observation"), want["observation"]
            if type(seen) is not list or len(seen) != len(wanted):
                obs_failed += 1
                miss.add("observation_shape", index, role)
            else:
                worst = None
                for a, b in zip(seen, wanted):
                    left = _number(a)
                    if left is None:
                        worst = None
                        break
                    worst = _top(worst, abs(left - float(b)))
                if worst is None:
                    obs_failed += 1
                    miss.add("observation_value", index, role)
                else:
                    obs_count += len(wanted)
                    obs_max = _top(obs_max, worst)
                    if worst > TOLERANCE:
                        obs_failed += 1
                        miss.add("observation", index, role)
            if item["action"] != exp["actions"][role]:
                fail("actions", "action", index, role)
    pay_max: float | None = None
    payoffs = pay_failed = 0
    for role in sorted(ROLES):
        left = _number(doc["returns"].get(role))
        right = _number(trace["returns"].get(role))
        if left is None or right is None:
            pay_failed += 1
            miss.add("returns", None, role)
            continue
        payoffs += 1
        gap = abs(left - right)
        pay_max = _top(pay_max, gap)
        if gap > TOLERANCE:
            pay_failed += 1
            miss.add("returns", None, role)
    for key in ("terminated", "truncated"):
        block = doc[key]
        for role in sorted(ROLES):
            if type(block.get(role)) is not bool or block[role] != trace[key][role]:
                fail(key, key, None, role)
    agreed = all(exact.values()) and not obs_failed and not pay_failed
    return {
        "verdict": "agreed" if agreed else "disagreed",
        "tolerance": TOLERANCE,
        "steps_compared": min(len(native_steps), len(ref_steps)),
        "native_steps": len(native_steps),
        "reference_steps": len(ref_steps),
        "decisions_compared": decisions,
        "observation_components_compared": obs_count,
        "observations_outside_tolerance": obs_failed,
        "max_abs_observation_difference": obs_max,
        "payoffs_compared": payoffs,
        "payoffs_outside_tolerance": pay_failed,
        "max_abs_payoff_difference": pay_max,
        "exact": exact,
        "mismatch_count": miss.count,
        "mismatches": miss.items,
        "first_mismatch": miss.items[0] if miss.items else None,
    }


# ---------------------------------------------------------------- contrasts


def _codebook(team: str) -> str:
    return team.split("-", 1)[1]


def _safe_channel(view: dict[str, Any]) -> int | None:
    try:
        return channel(view.get("observation"))
    except RefError:
        return None


def _physical(view: dict[str, Any]) -> list[float] | None:
    obs = view.get("observation")
    if type(obs) is not list or len(obs) != LISTENER_DIM:
        return None
    values = [_number(item) for item in obs[:PHYSICAL]]
    return None if any(v is None for v in values) else values  # type: ignore[misc]


def _get(step: dict[str, Any], kind: str, role: str) -> Any:
    return step[kind].get(role) if step[kind] is not None else None


def contrast(base: dict, other: dict, team: str) -> dict[str, Any]:
    """Compare one team with plain-plain in world order, listener channel apart."""
    left, right = base["steps"], other["steps"]
    shared = min(len(left), len(right))
    phys_max: float | None = None
    phys_count = phys_bad = 0
    act_diff = 0
    rew_max: float | None = None
    rew_count = 0
    first: dict[str, Any] = dict.fromkeys(
        (
            "first_changed_speaker_action",
            "first_listener_channel",
            "first_changed_listener_channel",
            "first_changed_listener_physical",
            "first_changed_listener_action",
        )
    )
    book = {"baseline": _codebook(BASELINE), "team": _codebook(team)}
    for index in range(shared):
        step_no, a, b = index + 1, left[index], right[index]
        sa, sb = _get(a, "actions", "p0"), _get(b, "actions", "p0")
        if first["first_changed_speaker_action"] is None and sa != sb:
            first["first_changed_speaker_action"] = {
                "step": step_no,
                "baseline": sa,
                "team": sb,
            }
        va, vb = a["views"].get("p1"), b["views"].get("p1")
        if va is None or vb is None:
            phys_bad += 1
            continue
        ca, cb = _safe_channel(va), _safe_channel(vb)
        if first["first_listener_channel"] is None and (ca, cb) != (None, None):
            first["first_listener_channel"] = {
                "step": step_no,
                "baseline": ca,
                "team": cb,
                "baseline_target": _target_or_none(book["baseline"], va),
                "team_target": _target_or_none(book["team"], vb),
            }
        if first["first_changed_listener_channel"] is None and ca != cb:
            first["first_changed_listener_channel"] = {
                "step": step_no,
                "baseline": ca,
                "team": cb,
            }
        pa, pb = _physical(va), _physical(vb)
        if pa is None or pb is None:
            phys_bad += 1
        else:
            gap = max(abs(x - y) for x, y in zip(pa, pb))
            phys_count += PHYSICAL
            phys_max = _top(phys_max, gap)
            if gap > TOLERANCE and first["first_changed_listener_physical"] is None:
                first["first_changed_listener_physical"] = {
                    "step": step_no,
                    "max_abs_difference": gap,
                }
        la, lb = _get(a, "actions", "p1"), _get(b, "actions", "p1")
        if la != lb:
            act_diff += 1
            if first["first_changed_listener_action"] is None:
                first["first_changed_listener_action"] = {
                    "step": step_no,
                    "baseline": la,
                    "team": lb,
                }
        ra, rb = _get(a, "rewards", "p1"), _get(b, "rewards", "p1")
        ra, rb = _number(ra), _number(rb)
        if ra is not None and rb is not None:
            rew_count += 1
            rew_max = _top(rew_max, abs(ra - rb))
    lengths = len(left) == len(right)
    phys_done = lengths and phys_bad == 0 and phys_count == PHYSICAL * shared
    rew_done = lengths and rew_count == shared
    ret_a = _number(base["returns"].get("p1"))
    ret_b = _number(other["returns"].get("p1"))
    return {
        "team": team,
        "baseline": BASELINE,
        "measured": True,
        "kind": KINDS[team],
        "steps": {"baseline": len(left), "team": len(right), "compared": shared},
        "listener_physical": {
            "components_compared": phys_count,
            "channel_components_excluded": (LISTENER_DIM - PHYSICAL) * shared,
            "max_abs_difference": phys_max,
            "matched": (phys_max is not None and phys_max <= TOLERANCE)
            if phys_done
            else None,
        },
        "listener_actions": {
            "compared": shared,
            "differing": act_diff,
            "matched": act_diff == 0 if lengths else None,
        },
        "listener_step_rewards": {
            "compared": rew_count,
            "max_abs_difference": rew_max,
            "matched": (rew_max is not None and rew_max <= TOLERANCE)
            if rew_done and shared
            else None,
        },
        "listener_return": {
            "baseline": ret_a,
            "team": ret_b,
            "abs_difference": None
            if ret_a is None or ret_b is None
            else abs(ret_a - ret_b),
        },
        **first,
    }


def _target_or_none(book: str, view: dict[str, Any]) -> int | None:
    try:
        return target(book, view.get("observation"))
    except RefError:
        return None


def lag(trace: dict[str, Any]) -> int | None:
    """1 if every received channel is the previous speaker symbol, 0 if same-step."""
    steps, prev_ok, same_ok, seen = trace["steps"], True, True, 0
    for index, step in enumerate(steps):
        view = step["views"].get("p1")
        got = _safe_channel(view) if view is not None else None
        before = steps[index - 1]["actions"].get("p0") if index else None
        now = step["actions"].get("p0")
        if got is None:
            prev_ok = prev_ok and before is None
            same_ok = same_ok and now is None
            continue
        seen += 1
        prev_ok = prev_ok and got == before
        same_ok = same_ok and got == now
    if not seen:
        return None
    if prev_ok and not same_ok:
        return 1
    if same_ok and not prev_ok:
        return 0
    return None


# ---------------------------------------------------------------- report


def _summary(trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace["steps"]
    listener = [s["views"].get("p1") for s in steps]
    return {
        "status": "ok",
        "reason": None,
        "world_steps": len(steps),
        "decisions": sum(len(s["group"]) for s in steps),
        "returns": trace["returns"],
        "terminated": trace["terminated"],
        "truncated": trace["truncated"],
        "communication_lag": lag(trace),
        "speaker_actions": [s["actions"].get("p0") for s in steps],
        "listener_channels": [
            _safe_channel(v) if v is not None else None for v in listener
        ],
        "listener_actions": [s["actions"].get("p1") for s in steps],
        "speaker_step_rewards": [s["rewards"].get("p0") for s in steps],
        "listener_step_rewards": [s["rewards"].get("p1") for s in steps],
    }


_SUMMARY_KEYS = (
    "world_steps",
    "decisions",
    "returns",
    "terminated",
    "truncated",
    "communication_lag",
    "speaker_actions",
    "listener_channels",
    "listener_actions",
    "speaker_step_rewards",
    "listener_step_rewards",
)


def _failed(reason: str) -> dict[str, Any]:
    return {"status": "failed", "reason": reason, **dict.fromkeys(_SUMMARY_KEYS)}


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _engine(factory: Factory | None) -> tuple[Factory | None, dict[str, Any]]:
    if factory is not None:
        info = {"source": "injected", "available": True, "reason": None}
        info |= {"mpe2": None, "pettingzoo": None, "pins_ok": None}
        return factory, {**info, "kwargs": dict(KWARGS)}
    versions = {name: _version(name) for name in PINS}
    info: dict[str, Any] = {"source": FACTORY, "kwargs": dict(KWARGS), **versions}
    info["pins_ok"] = versions == PINS
    try:
        made = official_factory()
    except Exception as exc:  # noqa: BLE001 - recorded as a token only
        info.update(available=False, reason=f"import_failed:{type(exc).__name__}")
        return None, info
    info.update(available=True, reason=None)
    return made, info


def build(
    root: Path, factory: Factory | None = None, *, cohort: str = DEFAULT_COHORT
) -> dict[str, Any]:
    """Register every cohort cell, measure what exists, and never fill gaps."""
    seeds, registered_cells = _cohort(cohort)
    made, engine = _engine(factory)
    plan_status, names = _plan(root, registered_cells)
    owners: dict[str, list[str]] = {}
    cells, traces, natives = [], {}, {}
    for cid, seed, team, speaker, listener in registered_cells:
        native = _native(root, cid, names.get(cid))
        if native["native_trial_id"]:
            owners.setdefault(native["native_trial_id"], []).append(cid)
        natives[cid] = native
        if made is None:
            independent = _failed("engine_unavailable")
        else:
            try:
                traces[cid] = simulate(made, seed, speaker, listener)
                independent = _summary(traces[cid])
            except RefError as exc:
                independent = _failed(exc.token)
            except Exception as exc:  # noqa: BLE001 - token only
                independent = _failed(f"engine_error:{type(exc).__name__}")
        cells.append(
            {
                "id": cid,
                "seed": seed,
                "team": team,
                "speaker": speaker,
                "listener": listener,
                "independent": independent,
            }
        )
    for cid_list in owners.values():
        if len(cid_list) > 1:
            for cid in cid_list:
                natives[cid].update(
                    status="invalid", reason="duplicate_uuid", _doc=None
                )
                for key in ("world_steps", "decisions", "returns"):
                    natives[cid][key] = None
                natives[cid]["terminated"] = natives[cid]["truncated"] = None
    for row in cells:
        cid, native = row["id"], natives[row["id"]]
        doc = native.pop("_doc")
        row["native"] = native
        row["comparison"] = None
        if doc is not None and cid in traces:
            row["comparison"] = compare(doc, traces[cid], row["seed"])
        natives[cid] = doc
    by_seed = []
    for seed in seeds:
        base_id = f"s{seed}-{BASELINE}"
        entry: dict[str, Any] = {"seed": seed, "independent": [], "native": []}
        for team, _speaker, _listener in TEAMS[1:]:
            other_id = f"s{seed}-{team}"
            if base_id in traces and other_id in traces:
                item = contrast(traces[base_id], traces[other_id], team)
            else:
                item = {"team": team, "baseline": BASELINE, "kind": KINDS[team]}
                item["measured"] = False
            entry["independent"].append(item)
            pair = (natives.get(base_id), natives.get(other_id))
            if all(doc is not None for doc in pair):
                native_item = contrast(
                    native_trace(pair[0]), native_trace(pair[1]), team
                )
            else:
                native_item = {"team": team, "baseline": BASELINE}
                native_item |= {"kind": KINDS[team], "measured": False}
            entry["native"].append(native_item)
        by_seed.append(entry)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "game": GAME,
        "engine": engine,
        "plan": plan_status,
        "registered": {
            "cells": len(registered_cells),
            "seeds": list(seeds),
            "teams": [team for team, _s, _l in TEAMS],
            "cycles": KWARGS["max_cycles"],
            "roles": dict(ROLES),
            "tolerance": TOLERANCE,
        },
        "denominators": _denominators(cells, by_seed, seeds),
        "cells": cells,
        "by_seed": by_seed,
    }
    if cohort != DEFAULT_COHORT:
        report["cohort"] = cohort
    return report


def _denominators(
    cells: list[dict], by_seed: list[dict], seeds: tuple[int, ...] = SEEDS
) -> dict[str, Any]:
    statuses = ("unregistered", "unattempted", "invalid", "complete")
    native = {s: sum(c["native"]["status"] == s for c in cells) for s in statuses}
    compared = [c["comparison"] for c in cells if c["comparison"] is not None]
    obs = [c["max_abs_observation_difference"] for c in compared]
    pay = [c["max_abs_payoff_difference"] for c in compared]
    obs = [v for v in obs if v is not None]
    pay = [v for v in pay if v is not None]

    def measured(kind: str) -> int:
        return sum(bool(item["measured"]) for entry in by_seed for item in entry[kind])

    return {
        "registered_cells": len(cells),
        "native": native,
        "independent": {
            "ok": sum(c["independent"]["status"] == "ok" for c in cells),
            "failed": sum(c["independent"]["status"] == "failed" for c in cells),
        },
        "compared_cells": len(compared),
        "agreed_cells": sum(c["verdict"] == "agreed" for c in compared),
        "disagreed_cells": sum(c["verdict"] == "disagreed" for c in compared),
        "compared_world_steps": sum(c["steps_compared"] for c in compared),
        "compared_decisions": sum(c["decisions_compared"] for c in compared),
        "compared_observation_components": sum(
            c["observation_components_compared"] for c in compared
        ),
        "compared_payoffs": sum(c["payoffs_compared"] for c in compared),
        "max_abs_observation_difference": max(obs) if obs else None,
        "max_abs_payoff_difference": max(pay) if pay else None,
        "contrasts": {
            "registered": len(seeds) * (len(TEAMS) - 1),
            "independent_measured": measured("independent"),
            "native_measured": measured("native"),
        },
    }


def _public(value: object) -> bool:
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, str):
        return not (
            value.startswith(("/", "\\", "~")) or ".." in value or "\\" in value
        )
    if isinstance(value, list):
        return all(_public(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _public(v) for k, v in value.items())
    return False


def write_report(
    root: Path,
    out: Path,
    factory: Factory | None = None,
    *,
    cohort: str = DEFAULT_COHORT,
) -> dict:
    report = build(root, factory, cohort=cohort)
    data = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    needles = {str(root.resolve())}
    if os.sep in str(root):
        needles.add(str(root))
    if not _public(report) or any(needle in data for needle in needles):
        raise ValueError("report would publish a host path")
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(data)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, metavar="ROOT")
    parser.add_argument("--out", required=True, type=Path, metavar="NEW.json")
    parser.add_argument(
        "--cohort",
        choices=sorted(COHORTS),
        default=DEFAULT_COHORT,
        help="registered cohort (default: initial)",
    )
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        print("--out already exists", file=sys.stderr)
        return 2
    if not args.out.parent.is_dir():
        print("--out parent does not exist", file=sys.stderr)
        return 2
    report = write_report(args.root, args.out, cohort=args.cohort)
    if not report["engine"]["available"]:
        print("engine unavailable; cells kept unmeasured", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
