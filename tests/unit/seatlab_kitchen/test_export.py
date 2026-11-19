import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

EXPORTER = (
    Path(__file__).resolve().parents[3]
    / "examples/tasks/cramped-room-parity/seatlab_export.py"
)
export_trial = runpy.run_path(str(EXPORTER))["export_trial"]

TRIAL_ID = "48f37ac3-0477-44ab-b195-0416bfad9dff"
PARTNER_TRIAL_ID = "68f37ac3-0477-44ab-b195-0416bfad9dff"
WORLD = Path("artifacts/logs/world/kitchen.jsonl")
ACKS = WORLD.with_name("seatlab-acks.jsonl")
REWARD = Path("verifier/reward.json")
RESULT = Path("result.json")
SEATS = ("chef0", "chef1")
FINISHED = "2026-10-10T10:00:00Z"
PRIVATE_VALUE = "NEVER_PUBLISH_FIXTURE_METADATA"
TAPE = ([0, -1], [0, 1], [1, 0], [-1, 0], [0, 0], "interact")
ACTION_WORDS = ("north", "south", "east", "west", "stay", "interact")


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write_json(path, value):
    path.write_bytes(_canonical(value) + b"\n")


def _write_rows(path, rows):
    path.write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))


def _read_json(path):
    return json.loads(path.read_bytes())


def _read_rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _state(tick, lead_waits):
    return {
        "timestep": tick,
        "players": [
            {
                "position": [1, 2] if lead_waits or tick == 0 else [1, 1],
                "orientation": [0, -1],
                "held_object": None,
            },
            {"position": [3, 1], "orientation": [-1, 0], "held_object": None},
        ],
        "objects": [],
        "all_orders": [],
        "bonus_orders": [],
    }


def _trial(root, *, native_id=TRIAL_ID, lead_waits=False):
    """Write structural evidence fixtures without simulating the native game."""
    (root / WORLD).parent.mkdir(parents=True)
    (root / REWARD).parent.mkdir()
    grade = float(not lead_waits)
    _write_json(
        root / RESULT,
        {
            "id": native_id,
            "finished_at": FINISHED,
            "reward_valid": True,
            "exception_info": None,
            "verifier_result": {"rewards": {"reward": grade}},
            "config": {"private": PRIVATE_VALUE},
            "seat_results": [
                {
                    "seat": seat,
                    "under_test": True,
                    "agent_info": {"name": "oracle", "version": "1.0.0"},
                    "agent_result": {"metadata": PRIVATE_VALUE},
                    "agent_execution": {"finished_at": FINISHED},
                    "exception_info": None,
                }
                for seat in SEATS
            ],
        },
    )
    _write_json(root / REWARD, {"reward": grade})
    transitions, receipts = [], []
    for tick in range(38):
        view = {"tick": tick, "state": _state(tick, lead_waits)}
        receipts.append(
            {
                "tick": tick,
                "view": view,
                "ack_sha256": dict.fromkeys(SEATS, _digest(view)),
            }
        )
        delivered = tick == 37 and not lead_waits
        sparse = [0, 20 if delivered else 0]
        transitions.append(
            {
                "tick": tick + 1,
                "joint_action": [
                    [0, 0] if lead_waits else TAPE[tick % 6],
                    TAPE[(tick + 2) % 6],
                ],
                "state": _state(tick + 1, lead_waits),
                "reward": sum(sparse),
                "sparse_reward_by_agent": sparse,
                "shaped_reward_by_agent": [0, 0],
                "soup_delivery_by_agent": [False, delivered],
            }
        )
    _write_rows(root / WORLD, transitions)
    _write_rows(root / ACKS, receipts)
    return root


@pytest.fixture
def trial(tmp_path):
    return _trial(tmp_path / "trial")


def _assert_rejected(trial, out, match):
    with pytest.raises(ValueError, match=match):
        export_trial(trial, out)
    assert not out.exists()


def test_exports_exact_bound_role_episodes(trial, tmp_path):
    out = tmp_path / "episodes.jsonl"
    summary = export_trial(trial, out)
    receipts = _read_rows(trial / ACKS)
    expected_case = _digest(
        {
            "engine": "HumanCompatibleAI/overcooked_ai@739950a079cdaed5a44fcc662efc40244c205d06",
            "peer_task": "fa357189438d0d8c7d2ff4f97b00afb83d75dfa5",
            "layout": "cramped_room",
            "horizon": 38,
            "initial_view_sha256": _digest(receipts[0]["view"]),
        }
    )
    episodes = _read_rows(out)
    payload = out.read_bytes()
    assert payload == b"".join(_canonical(episode) + b"\n" for episode in episodes)
    assert PRIVATE_VALUE.encode() not in payload
    assert [episode["role"] for episode in episodes] == list(SEATS)
    hashes = {}
    for seat, episode in zip(SEATS, episodes, strict=True):
        offset = 0 if seat == "chef0" else 2
        envelope = {
            "schema": "seatlab.episode/v1",
            "id": f"kitchen:{TRIAL_ID}:{seat}",
            "case_sha256": expected_case,
            "role": seat,
            "native_trial_id": TRIAL_ID,
            "closed": True,
            "frames": [
                {
                    "turn": tick + 1,
                    "basis": "bound",
                    "view": receipt["view"],
                    "view_sha256": receipt["ack_sha256"][seat],
                    "observed": {"action": ACTION_WORDS[(tick + offset) % 6]},
                }
                for tick, receipt in enumerate(receipts)
            ],
        }
        hashes[seat] = _digest(envelope)
        assert episode == {**envelope, "episode_sha256": hashes[seat]}
        assert episode["frames"][-1]["view"]["state"]["timestep"] == 37
    assert summary == {
        "native_trial_id": TRIAL_ID,
        "case_sha256": expected_case,
        "reward": 1,
        "deliveries_by_seat": {"chef0": 0, "chef1": 1},
        "episodes": 2,
        "turns_per_role": 38,
        "episode_sha256": hashes,
    }


def test_changed_partner_changes_view_not_unchanged_policy(trial, tmp_path):
    changed = _trial(tmp_path / "changed", native_id=PARTNER_TRIAL_ID, lead_waits=True)
    baseline_out, changed_out = tmp_path / "baseline.jsonl", tmp_path / "changed.jsonl"
    baseline_summary = export_trial(trial, baseline_out)
    changed_summary = export_trial(changed, changed_out)
    assert changed_summary["native_trial_id"] != baseline_summary["native_trial_id"]
    assert changed_summary["case_sha256"] == baseline_summary["case_sha256"]
    assert changed_summary["reward"] == 0
    assert changed_summary["deliveries_by_seat"] == {"chef0": 0, "chef1": 0}
    lead, partner = [episode["frames"] for episode in _read_rows(baseline_out)]
    changed_lead, changed_partner = [
        episode["frames"] for episode in _read_rows(changed_out)
    ]
    assert changed_lead[0]["view_sha256"] == lead[0]["view_sha256"]
    assert changed_lead[0]["observed"] == {"action": "stay"}
    assert lead[0]["observed"] == {"action": "north"}
    assert [frame["observed"] for frame in changed_partner] == [
        frame["observed"] for frame in partner
    ]
    assert changed_partner[0]["view_sha256"] == partner[0]["view_sha256"]
    assert changed_partner[1]["view_sha256"] != partner[1]["view_sha256"]


@pytest.mark.parametrize("seat,kind", [("chef0", "stale"), ("chef1", "foreign")])
def test_rejects_stale_and_foreign_client_hashes(trial, tmp_path, seat, kind):
    receipts = _read_rows(trial / ACKS)
    receipts[1]["ack_sha256"][seat] = (
        receipts[0]["ack_sha256"][seat]
        if kind == "stale"
        else _digest({"other_game": 1})
    )
    _write_rows(trial / ACKS, receipts)
    _assert_rejected(
        trial, tmp_path / "episodes.jsonl", f"{seat} digest.*full pre-action view"
    )


@pytest.mark.parametrize("tamper", ["post-state", "both-client-hashes"])
def test_rejects_complete_pre_post_state_mismatch(trial, tmp_path, tamper):
    if tamper == "post-state":
        rows = _read_rows(trial / WORLD)
        rows[0]["state"]["players"][1]["orientation"] = [0, 1]
        _write_rows(trial / WORLD, rows)
    else:
        rows = _read_rows(trial / ACKS)
        rows[1]["view"]["state"]["players"][1]["orientation"] = [0, 1]
        rows[1]["ack_sha256"] = dict.fromkeys(SEATS, _digest(rows[1]["view"]))
        _write_rows(trial / ACKS, rows)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "previous native post-state")


def test_rejects_a_post_action_view_at_the_same_turn(trial, tmp_path):
    rows = _read_rows(trial / ACKS)
    rows[0]["view"]["state"] = _read_rows(trial / WORLD)[0]["state"]
    rows[0]["ack_sha256"] = dict.fromkeys(SEATS, _digest(rows[0]["view"]))
    _write_rows(trial / ACKS, rows)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "state.timestep must be 0")


@pytest.mark.parametrize("mismatch", ["separate reward", "delivery endpoint"])
def test_rejects_native_reward_mismatches(trial, tmp_path, mismatch):
    _write_json(trial / REWARD, {"reward": 0})
    if mismatch == "delivery endpoint":
        result = _read_json(trial / RESULT)
        result["verifier_result"]["rewards"]["reward"] = 0
        _write_json(trial / RESULT, result)
    expected = (
        "delivery endpoint"
        if mismatch == "delivery endpoint"
        else "between result.json"
    )
    _assert_rejected(
        trial, tmp_path / "episodes.jsonl", f"native reward mismatch.*{expected}"
    )


@pytest.mark.parametrize("where", [REWARD, RESULT])
def test_missing_gradation_is_not_zero(tmp_path, where):
    trial = _trial(tmp_path / "zero", lead_waits=True)
    if where == REWARD:
        _write_json(trial / REWARD, {})
    else:
        result = _read_json(trial / RESULT)
        result["verifier_result"]["rewards"] = {}
        _write_json(trial / RESULT, result)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "reward")


@pytest.mark.parametrize("grade", [b"true", b"1.00000000000000001", b"1e-999"])
def test_rejects_non_binary_numeric_grade(trial, tmp_path, grade):
    (trial / REWARD).write_bytes(b'{"reward":' + grade + b"}\n")
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "numeric reward of 0 or 1")


def test_rejects_legacy_world_only_trial(trial, tmp_path):
    for path in (ACKS, RESULT, REWARD):
        (trial / path).unlink()
    _assert_rejected(
        trial,
        tmp_path / "episodes.jsonl",
        "missing client ack.*not enough to reconstruct",
    )


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("id", TRIAL_ID.upper(), "canonical native UUID"),
        ("finished_at", "", "finished trial"),
        ("reward_valid", 1, "valid reward"),
        ("exception_info", {}, "no exception"),
        ("seat_results", {}, "exactly 2 seat_results"),
    ],
)
def test_rejects_invalid_trial_result(trial, tmp_path, field, value, match):
    result = _read_json(trial / RESULT)
    result[field] = value
    _write_json(trial / RESULT, result)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", match)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("seat", "chef0", "distinct chef0 and chef1"),
        ("under_test", 1, "under-test oracle"),
        ("exception_info", {}, "under-test oracle"),
        ("agent_info", {"name": "nop"}, "under-test oracle"),
        ("agent_execution", {"finished_at": None}, "under-test oracle"),
    ],
)
def test_rejects_invalid_seat_result(trial, tmp_path, field, value, match):
    result = _read_json(trial / RESULT)
    result["seat_results"][1][field] = value
    _write_json(trial / RESULT, result)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", match)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("tick", 0, "one native transition"),
        ("joint_action", [[True, 0], "interact"], "invalid normalized action"),
        ("sparse_reward_by_agent", [False, 0], "sparse reward or delivery"),
        ("soup_delivery_by_agent", [True, False], "sparse reward or delivery"),
        ("reward", 20, "sparse reward or delivery"),
    ],
)
def test_rejects_invalid_native_transition(trial, tmp_path, field, value, match):
    rows = _read_rows(trial / WORLD)
    rows[0][field] = value
    _write_rows(trial / WORLD, rows)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", match)


def test_rejects_duplicate_observation_tick(trial, tmp_path):
    rows = _read_rows(trial / ACKS)
    rows[1] = rows[0]
    _write_rows(trial / ACKS, rows)
    _assert_rejected(
        trial, tmp_path / "episodes.jsonl", "expected one client ack receipt"
    )


def test_rejects_a_missing_client_ack(trial, tmp_path):
    rows = _read_rows(trial / ACKS)
    del rows[0]["ack_sha256"]["chef1"]
    _write_rows(trial / ACKS, rows)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "missing client ack")


def test_rejects_a_mismatched_view_tick_even_with_both_hashes(trial, tmp_path):
    rows = _read_rows(trial / ACKS)
    rows[1]["view"]["tick"] = 0
    rows[1]["ack_sha256"] = dict.fromkeys(SEATS, _digest(rows[1]["view"]))
    _write_rows(trial / ACKS, rows)
    _assert_rejected(trial, tmp_path / "episodes.jsonl", r"view\.tick must be 1")


@pytest.mark.parametrize("artifact", [WORLD, ACKS])
def test_rejects_trailing_records(trial, tmp_path, artifact):
    with (trial / artifact).open("ab") as file:
        file.write(b"{}\n")
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "expected exactly 38")


@pytest.mark.parametrize(
    "artifact,old,new,match",
    [
        (ACKS, b'"view":', b'"view":{},"view":', "duplicate JSON key"),
        (ACKS, b'"tick":0', b'"tick": 0', "canonical ASCII JSON"),
        (RESULT, b'"id":', b'"id":null,"id":', "duplicate JSON key"),
        (REWARD, b"1.0", b"NaN", "nonfinite JSON number"),
        (REWARD, b"1.0", b"1e999", "nonfinite JSON number"),
        (REWARD, b"}\n", b"}\n{}\n", "Extra data"),
    ],
)
def test_rejects_non_strict_json(trial, tmp_path, artifact, old, new, match):
    file = trial / artifact
    file.write_bytes(file.read_bytes().replace(old, new, 1))
    _assert_rejected(trial, tmp_path / "episodes.jsonl", match)


@pytest.mark.parametrize("artifact", [RESULT, WORLD, ACKS, REWARD, WORLD.parent])
def test_rejects_artifact_symlinks(trial, tmp_path, artifact):
    source = trial / artifact
    outside = tmp_path / "elsewhere"
    source.rename(outside)
    source.symlink_to(outside, target_is_directory=outside.is_dir())
    out = tmp_path / "episodes.jsonl"
    with pytest.raises(OSError):
        export_trial(trial, out)
    assert not out.exists()


def test_rejects_symlinked_output_directory(trial, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        export_trial(trial, alias / "episodes.jsonl")
    assert not (target / "episodes.jsonl").exists()


def test_rejects_hardlinked_artifact(trial, tmp_path):
    os.link(trial / ACKS, tmp_path / "shared")
    _assert_rejected(trial, tmp_path / "episodes.jsonl", "regular.*artifact file")


@pytest.mark.parametrize("existing", ["file", "link", "dangling link"])
def test_never_overwrites_or_follows_existing_output(trial, tmp_path, existing):
    out = tmp_path / "episodes.jsonl"
    target = tmp_path / "target"
    if existing == "file":
        out.write_text("existing")
    else:
        if existing == "link":
            target.write_text("target")
        out.symlink_to(target)
    with pytest.raises(FileExistsError):
        export_trial(trial, out)
    if existing == "file":
        assert out.read_text() == "existing"
    else:
        assert out.is_symlink()
        if existing == "link":
            assert target.read_text() == "target"
        else:
            assert not target.exists()


def test_runs_as_a_standalone_command(trial, tmp_path):
    out = tmp_path / "episodes.jsonl"
    completed = subprocess.run(
        [sys.executable, str(EXPORTER), "--trial", str(trial), "--out", str(out)],
        check=True,
        capture_output=True,
    )
    assert json.loads(completed.stdout)["native_trial_id"] == TRIAL_ID
    assert len(_read_rows(out)) == 2
