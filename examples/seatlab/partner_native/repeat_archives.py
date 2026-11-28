import hashlib
import json
from pathlib import Path
from typing import Any

from ..calendar_notices.repeat_summary import _grid
from ..calendar_notices.visible_replay_summary import _digest, _unique_object
from .repeat_math import Quartet, summarize

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_ROLE_SHA256 = "fadde7ef1bb075367565f18012b0effcc690af3d608f2f28809099e3e4784209"
_NOTICE_SHA256 = "3650bd3235d8634272e3fc84560420788736f56a7d374c1a584a96f67ad555a3"
_ROLE_ARMS = ("sender", "receiver")
_ROOMS = ("blind", "peer")
_ROLE_CASES = tuple(
    (world, direction, room)
    for world in range(12)
    for direction in range(2)
    for room in _ROOMS
)
_ROLE_CELLS = {
    (world, direction, room, arm, repeat)
    for world, direction, room in _ROLE_CASES
    for arm in _ROLE_ARMS
    for repeat in range(2)
}


def _receipt(name: str, digest: str) -> dict[str, Any]:
    data = (_RESULTS / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"retained receipt digest mismatch: {name}")
    return json.loads(data, object_pairs_hook=_unique_object)


def _role_key(row: dict[str, Any], room: str) -> tuple[int, int, str, str, int]:
    world, direction, repeat = row["world"], row["direction"], row["repetition"]
    if any(type(value) is not int for value in (world, direction, repeat)):
        raise ValueError("invalid role allocation coordinates")
    key = (world, direction, room, row["policy"], repeat)
    if key not in _ROLE_CELLS:
        raise ValueError("unknown role allocation cell")
    return key


def _role_reward(grade: dict[str, Any]) -> int:
    reward = grade["reward"]
    if type(reward) is not int or reward not in (0, 1):
        raise ValueError("expected binary role allocation reward")
    return reward


def _role_proofs(
    receipt: dict[str, Any],
) -> dict[tuple[int, int, str, str, int], tuple[int, int]]:
    proofs = {}
    seen_trials: set[str] = set()
    sources: dict[int, tuple[str, str]] = {}
    for policy in receipt["policies"]:
        native = policy["native"]
        if (
            policy["protocol_status"] != "complete"
            or native["schema"] != "seatlab.calendar-replica-report/v1"
            or native["status"] != "complete"
            or native["arms"].keys() != set(_ROOMS)
            or any(native[k] != policy[k] for k in ("world", "direction", "repetition"))
        ):
            raise ValueError("invalid retained role policy proof")
        source = (_digest(native["native_sha256"]), _digest(native["semantics_sha256"]))
        if sources.setdefault(policy["world"], source) != source:
            raise ValueError("mixed role allocation sources")
        for room, grade in native["arms"].items():
            key = _role_key(policy, room)
            trial = _digest(grade["trial_id_sha256"])
            if key in proofs or trial in seen_trials:
                raise ValueError("duplicate native role allocation grade")
            if (
                grade["native_timeline_verified"] is not True
                or grade["status"] != "complete"
            ):
                raise ValueError("unverified native role allocation grade")
            seen_trials.add(trial)
            proofs[key] = (_role_reward(grade), grade["slot"])
    if proofs.keys() != _ROLE_CELLS:
        raise ValueError("incomplete native role allocation grid")
    return proofs


def _role_cases(receipt: dict[str, Any]) -> list[Quartet]:
    if (
        receipt["schema"] != "seatlab.calendar-role-tools/v1"
        or receipt["reused_trial_id_sha256"]
        or receipt["mixed_source_worlds"]
    ):
        raise ValueError("invalid retained role allocation receipt")
    proofs = _role_proofs(receipt)
    rewards = {}
    for row in receipt["rows"]:
        key = _role_key(row, row["room"])
        reward = _role_reward(row)
        if (
            key in rewards
            or row["native_status"] != "complete"
            or row["protocol_bound"] is not True
            or (reward, row["slot"]) != proofs[key]
        ):
            raise ValueError("role allocation row does not match native grade")
        rewards[key] = reward
    if rewards.keys() != _ROLE_CELLS:
        raise ValueError("incomplete role allocation rows")
    return [
        (
            rewards[world, direction, room, "sender", 0],
            rewards[world, direction, room, "sender", 1],
            rewards[world, direction, room, "receiver", 0],
            rewards[world, direction, room, "receiver", 1],
        )
        for world, direction, room in _ROLE_CASES
    ]


def _notice_cases(receipt: dict[str, Any]) -> list[Quartet]:
    teams = _grid(receipt)
    return [
        (
            teams[world, "lists", 1].reward,
            teams[world, "lists", 2].reward,
            teams[world, "checked", 1].reward,
            teams[world, "checked", 2].reward,
        )
        for world in sorted({cell[0] for cell in teams})
    ]


def report() -> dict[str, Any]:
    role = _role_cases(_receipt("role-allocation-live.json", _ROLE_SHA256))
    notices = _notice_cases(
        _receipt("calendar-native-repeatability.json", _NOTICE_SHA256)
    )
    return {
        "schema": "seatlab.retained-repeat-audit/v1",
        "source": "published_native_rewards",
        "studies": {
            "role_allocation": {
                "receipt_sha256": _ROLE_SHA256,
                "worlds": 12,
                "teams": 4 * len(role),
                "arms": list(_ROLE_ARMS),
                "scope": (
                    "Retrospective tool assignment from sender to receiver, "
                    "not partner swaps. "
                    "48 world-direction-room cases in 12 fixed worlds; both rooms "
                    "share one policy's opening lists, so cases are not independent."
                ),
                "metrics": summarize(role),
            },
            "calendar_notices": {
                "receipt_sha256": _NOTICE_SHA256,
                "worlds": len(notices),
                "teams": 4 * len(notices),
                "arms": ["lists", "checked"],
                "scope": (
                    "Retrospective intervention from lists to checked lists, "
                    "not partner swaps. "
                    "24 fixed notice worlds with 2 native repeats per arm."
                ),
                "metrics": summarize(notices),
            },
        },
    }
