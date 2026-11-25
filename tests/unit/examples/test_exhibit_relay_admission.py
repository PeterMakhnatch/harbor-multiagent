import json
import stat
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from examples.native_study import record_bytes
from examples.seatlab.exhibit_bind import bind
from examples.seatlab.exhibit_enroll import digest, enroll
from examples.seatlab.exhibit_plan import load_plan
from harbor.models.job.lock import TrialLock
from harbor.models.trial.config import TrialConfig


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    path = tmp_path / "plan"
    enroll(path, pairs=8, development_seed=143)
    return path


def _assert_safe_receipt(receipt: dict[str, Any], socket: Path) -> None:
    forbidden = {
        "seed",
        "world_seed",
        "development_seed",
        "cases",
        "assignments",
        "schedule",
        "owner_sockets",
        "owner_socket",
        "api_key",
        "token",
    }

    def check(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for member in value.values():
                check(member)
        elif isinstance(value, list):
            for member in value:
                check(member)
        elif isinstance(value, str):
            assert str(socket) not in value

    check(receipt)


def test_balanced_reproducible_private_enrollment(plan: Path, tmp_path: Path) -> None:
    manifest, schedule, receipt = load_plan(plan)
    assert manifest["arms"] == schedule["arms"] == ["masked", "bundled"]
    assert schedule["profile"] == "coupling"
    assert receipt["enrollable"] is False
    assert receipt["executions"] == 0
    assert receipt["funds_spent_usd"] == 0
    assert len(schedule["cases"]) == len(manifest["pairs"]) == 8
    assert len({case["seed"] for case in schedule["cases"]}) == 8
    assert len({pair["case_commitment"] for pair in manifest["pairs"]}) == 8
    assert Counter(
        (case["layout"], case["order"][0]) for case in schedule["cases"]
    ) == {(layout, arm): 2 for layout in ("a", "b") for arm in ("masked", "bundled")}
    rows = schedule["schedule"]
    assert [row["position"] for row in rows] == list(range(1, 17))
    for case in schedule["cases"]:
        assert [row["arm"] for row in rows if row["id"] == case["id"]] == case["order"]
    assert stat.S_IMODE(plan.stat().st_mode) == 0o700
    for name in ("manifest.json", "schedule.json", "receipt.json"):
        assert stat.S_IMODE((plan / name).stat().st_mode) == 0o600
    _assert_safe_receipt(receipt, tmp_path / "private.sock")
    other = tmp_path / "repeat"
    enroll(other, pairs=8, development_seed=143)
    repeat_manifest, repeat_schedule, _ = load_plan(other)
    assert repeat_manifest["pairs"] == manifest["pairs"]
    assert repeat_schedule["cases"] == schedule["cases"]
    assert repeat_schedule["schedule"] == rows
    with pytest.raises((ValueError, OSError)):
        enroll(plan, pairs=8, development_seed=144)
    assert load_plan(plan) == (manifest, schedule, receipt)


def test_schedule_digest_and_semantic_validation(plan: Path) -> None:
    _, schedule, receipt = load_plan(plan)
    rows = schedule["schedule"]
    assert rows[0]["id"] == rows[1]["id"]
    rows[0]["arm"], rows[1]["arm"] = rows[1]["arm"], rows[0]["arm"]
    payload = record_bytes(schedule)
    (plan / "schedule.json").write_bytes(payload)
    with pytest.raises(ValueError):
        load_plan(plan)
    receipt["schedule_digest"] = digest(payload)
    (plan / "receipt.json").write_bytes(record_bytes(receipt))
    with pytest.raises(ValueError):
        load_plan(plan)


def test_case_commitment_must_match_generated_case(plan: Path) -> None:
    manifest, schedule, receipt = load_plan(plan)
    old = manifest["pairs"][0]["case_commitment"]
    replacement = old[:-1] + ("1" if old[-1] == "0" else "0")
    manifest["pairs"][0]["case_commitment"] = replacement
    manifest_bytes = record_bytes(manifest)
    schedule["manifest_digest"] = receipt["manifest_digest"] = digest(manifest_bytes)
    schedule_bytes = record_bytes(schedule)
    receipt["schedule_digest"] = digest(schedule_bytes)
    (plan / "manifest.json").write_bytes(manifest_bytes)
    (plan / "schedule.json").write_bytes(schedule_bytes)
    (plan / "receipt.json").write_bytes(record_bytes(receipt))
    with pytest.raises(ValueError):
        load_plan(plan)


def _check_roster(native: TrialConfig | TrialLock, socket: Path) -> None:
    members = {member.seat: member for member in native.participants}
    assert len(native.participants) == 3
    assert set(members) == {"loader", "registrar", "depot"}
    for role in ("loader", "registrar"):
        assert members[role].name == "oracle"
        assert members[role].import_path is None
        assert "owner_sockets" not in members[role].kwargs
    depot = members["depot"]
    assert depot.import_path == "examples.seatlab.exhibit_owner:RelayOwner"
    assert depot.kwargs == {"role": "depot", "owner_sockets": [str(socket)]}
    for member in members.values():
        assert member.model_name is None
        assert member.base_url is None
        assert not member.env


@pytest.fixture
def endpoint() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="xr-", dir="/tmp") as directory:
        yield Path(directory) / "owner.sock"


def test_native_bind_isolates_only_world_mode(
    plan: Path, tmp_path: Path, endpoint: Path
) -> None:
    manifest, schedule, enrollment_receipt = load_plan(plan)
    case = schedule["cases"][0]
    ticket = case["id"]
    socket = endpoint
    assert socket.is_absolute() and not socket.exists()
    configs = []
    locks = []
    for arm in ("masked", "bundled"):
        out = tmp_path / arm
        bind(plan, ticket, arm, socket, out)
        config = TrialConfig.model_validate_json((out / "config.json").read_bytes())
        lock = TrialLock.model_validate_json((out / "lock.json").read_bytes())
        expected = {
            "EXHIBIT_SEED": str(case["seed"]),
            "EXHIBIT_PROFILE": "coupling",
            "EXHIBIT_LAYOUT": case["layout"],
            "EXHIBIT_MODE": arm,
        }
        for native in (config, lock):
            assert native.environment.env == expected
            _check_roster(native, socket)
        _assert_safe_receipt(json.loads((out / "receipt.json").read_bytes()), socket)
        configs.append(config)
        locks.append(lock)
    for left, right in ((configs[0], configs[1]), (locks[0], locks[1])):
        left.environment.env.pop("EXHIBIT_MODE")
        right.environment.env.pop("EXHIBIT_MODE")
        assert left.model_dump(
            exclude={"trial_name", "trials_dir", "job_id"}
        ) == right.model_dump(exclude={"trial_name", "trials_dir", "job_id"})
    with pytest.raises((ValueError, OSError)):
        bind(plan, ticket, "masked", socket, tmp_path / "masked")
    with pytest.raises((ValueError, OSError)):
        bind(plan, ticket, "masked", socket, plan)
    assert load_plan(plan) == (manifest, schedule, enrollment_receipt)
    assert not socket.exists()
