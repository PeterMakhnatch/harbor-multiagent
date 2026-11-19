"""Test hybrid8 registration, mode binding, and aggregation without Docker."""

import importlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_study import _result as _base_result

ADAPTER = Path(__file__).resolve().parents[1]


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    return importlib.import_module("interactive")


@pytest.fixture
def reviewed(driver):
    return {name: (ADAPTER / name).read_bytes() for name in driver.REVIEWED_FILES}


def _suffix_case(driver, case_id, *, null=False):
    """Build a case requiring 8 nonterminal scalar rounds before suffix round 9.

    With horizon=24 and duration=1:
    - m1 busy on [0, 2, 4, 6, 8] (free at 1, 3, 5, 7, 9..23).
    - m2..m4 busy on [1, 3, 5, 7, 9] (free at 0, 2, 4, 6, 8, 10..23).
    - For null=False, m5 matches m2..m4 (earliest common start is 10).
    - For null=True, m5 is busy on [1, 3, 5, 7, 9..23] (free at 0, 2, 4, 6, 8),
      so m5 stays non-null through round 8 and has no common start at >= 8.

    Scalar rounds progress one tick per round:
      r1 b=0 -> (1, 0, 0, 0, 0) max=1
      r2 b=1 -> (1, 2, 2, 2, 2) max=2
      r3 b=2 -> (3, 2, 2, 2, 2) max=3
      r4 b=3 -> (3, 4, 4, 4, 4) max=4
      r5 b=4 -> (5, 4, 4, 4, 4) max=5
      r6 b=5 -> (5, 6, 6, 6, 6) max=6
      r7 b=6 -> (7, 6, 6, 6, 6) max=7
      r8 b=7 -> (7, 8, 8, 8, 8) max=8
    Round 9 is suffix-hex at bound=8 with 24 - 8 = 16 bit positions per seat
    (width = ceil(16 / 4) = 4 hex digits; 5 * 16 = 80 suffix_bit_positions).
    """
    horizon = 24
    busy_even = [0, 2, 4, 6, 8]
    busy_odd = [1, 3, 5, 7, 9]
    if null:
        members = {
            "m1": busy_even,
            "m2": busy_odd,
            "m3": busy_odd,
            "m4": busy_odd,
            "m5": [1, 3, 5, 7, *range(9, horizon)],
        }
        gold = []
    else:
        members = {
            "m1": busy_even,
            "m2": busy_odd,
            "m3": busy_odd,
            "m4": busy_odd,
            "m5": busy_odd,
        }
        gold = [10]
    return {
        "schema": "seatlab.calendar-case/v1",
        "dataset_commit": driver.DATASET_COMMIT,
        "case_id": case_id,
        "horizon": horizon,
        "slots_per_day": horizon,
        "duration": 1,
        "gold": gold,
        "members": members,
        "source_sha256": {"task_plans": "a" * 64},
    }


def _short_case(driver, case_id):
    """Build a case that terminates in 2 scalar rounds (no suffix round)."""
    return {
        "schema": "seatlab.calendar-case/v1",
        "dataset_commit": driver.DATASET_COMMIT,
        "case_id": case_id,
        "horizon": 24,
        "slots_per_day": 24,
        "duration": 1,
        "gold": [2],
        "members": {
            "m1": [0, 1],
            "m2": [0],
            "m3": [],
            "m4": [],
            "m5": [],
        },
        "source_sha256": {"task_plans": "a" * 64},
    }


def _register(driver, reviewed, out, cases, *, mode="hybrid8"):
    out.mkdir(mode=0o700)
    (out / "cells").mkdir(mode=0o700)
    source = {
        "out_path": str(out),
        "dataset_path": "/not/needed/to/collect",
        "dataset_commit": driver.DATASET_COMMIT,
        "harbor_executable_path": "/not/needed/to/collect/harbor",
        "harbor_executable_sha256": "b" * 64,
        "reviewed_sha256": driver._digests(reviewed),
        "cases": cases,
    }
    hashes = {
        case_id: driver._digests(driver._task_payloads(case, reviewed, mode=mode))
        for case_id, case in cases.items()
    }
    plan = driver._plan(uuid4().hex, source, cases, hashes, mode=mode)
    driver.study._write_once(out / "source.json", source)
    driver.study._write_once(out / "plan.json", plan)
    return plan


def _feasible_starts(busy, horizon, slots_per_day, duration):
    occupied = set(busy)
    return [
        start
        for day in range(0, horizon, slots_per_day)
        for start in range(day, day + slots_per_day - duration + 1)
        if occupied.isdisjoint(range(start, start + duration))
    ]


def _encode_suffix(starts, bound, horizon):
    width = math.ceil((horizon - bound) / 4)
    bits = 0
    for tick in starts:
        if tick >= bound:
            bits |= 1 << (tick - bound)
    return f"{bits:0{width}x}"


def _world(driver, case, *, mode="hybrid8"):
    """Build a synthetic world transcript for cursor or hybrid8."""
    canonical = driver.study._canonical

    def digest(value):
        return driver.study._sha(canonical(value))

    horizon = case["horizon"]
    slots_per_day = case["slots_per_day"]
    duration = case["duration"]
    views = {
        role: digest(
            {
                "role": role,
                "mode": mode,
                "horizon": horizon,
                "slots_per_day": slots_per_day,
                "duration": duration,
                "busy": busy,
            }
        )
        for role, busy in case["members"].items()
    }
    starts = {
        role: _feasible_starts(busy, horizon, slots_per_day, duration)
        for role, busy in case["members"].items()
    }
    rounds = []
    bound = 0
    number = 1
    choice = None
    while True:
        if mode == "hybrid8" and number == 9:
            query = {"round": 9, "bound": bound, "encoding": "suffix-hex"}
            replies = {
                role: _encode_suffix(starts[role], bound, horizon)
                for role in driver.study.SEATS
            }
            bundle = {**query, "replies": replies}
            rounds.append(
                {
                    **bundle,
                    "query_sha256": digest(query),
                    "bundle_sha256": digest(bundle),
                }
            )
            common = (1 << (horizon - bound)) - 1
            for mask in replies.values():
                common &= int(mask, 16)
            if common:
                choice = bound + (common & -common).bit_length() - 1
            else:
                choice = None
            break

        query = {"round": number, "bound": bound}
        replies = {}
        for role in driver.study.SEATS:
            nxt = next((tick for tick in starts[role] if tick >= bound), None)
            replies[role] = nxt
        bundle = {**query, "replies": replies}
        rounds.append(
            {
                **bundle,
                "query_sha256": digest(query),
                "bundle_sha256": digest(bundle),
            }
        )
        values = list(replies.values())
        if any(value is None for value in values):
            choice = None
            break
        highest = max(values)
        if all(value == highest for value in values):
            choice = highest
            break
        bound = highest
        number += 1

    return {
        "schema": "seatlab.calendar-interactive-world/v1",
        "case_id": case["case_id"],
        "mode": mode,
        "initial_view_sha256": views,
        "rounds": rounds,
        "choice": choice,
        "choice_view_sha256": rounds[-1]["bundle_sha256"],
        "acknowledged": True,
    }


def _record_attempt(
    driver,
    reviewed,
    out,
    plan,
    case,
    *,
    mode=None,
    world_mode=None,
    identifier=None,
    returncode=0,
    modify=None,
):
    effective_mode = mode or plan["mode"]
    case_id = case["case_id"]
    cell = next(row for row in plan["cells"] if row["case_id"] == case_id)
    root, native = driver.study._paths(out, cell)
    root.mkdir()
    task = root / "task"
    for name, contents in driver._task_payloads(
        case, reviewed, mode=effective_mode
    ).items():
        path = task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o755 if name.endswith(".sh") else 0o644)
    driver.study._write_once(
        root / "trial-config.json", driver.study._native_config(task, native, cell)
    )
    files = {}
    if returncode == 0:
        world = _world(driver, case, mode=world_mode or effective_mode)
        reward = int(world["choice"] in case["gold"])
        result = _base_result(
            driver.study, root, native, cell, identifier or str(uuid4()), reward
        )
        verify_start = result["verifier"]["finished_at"]
        result["verifier"] = {"started_at": verify_start, "finished_at": verify_start}
        if modify:
            modify(world, result)
        files = {
            "result": driver.study._canonical(result),
            "world": driver.study._canonical(world),
            "reward": f"{reward}\n".encode("ascii"),
        }
        for key, contents in files.items():
            path = native / driver.NATIVE_FILES[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
    driver.study._write_once(
        root / "attempt.json",
        {
            "schema": driver.ATTEMPT_SCHEMA,
            "trial_name": cell["trial_name"],
            "returncode": returncode,
            "task_sha256": driver.study._sha(
                driver.study._canonical(plan["task_sha256"][case_id])
            ),
            "native_sha256": driver._digests(files),
        },
    )
    return root, native


def _never_launch(*args, **kwargs):
    pytest.fail("collector tried to start an external process")


def test_run_cli_accepts_hybrid8_and_defaults_to_cursor(
    driver, tmp_path, monkeypatch, capsys
):
    harbor = tmp_path / "harbor"
    harbor.write_text("not executed")
    harbor.chmod(0o700)
    calls = []

    def fake_run(dataset, out, harbor_path, case_spec, mode=driver.MODE):
        calls.append((dataset, out, harbor_path, case_spec, mode))
        return {"summary": {"verified": 1, "planned": 1}, "mode": mode}

    monkeypatch.setattr(driver, "run", fake_run)
    for extra, expected_mode in (([], "cursor"), (["--mode", "hybrid8"], "hybrid8")):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "interactive.py",
                "run",
                "--dataset",
                str(tmp_path),
                "--out",
                str(tmp_path / f"out-{expected_mode}"),
                "--harbor",
                str(harbor),
                "--cases",
                "2/1",
                *extra,
            ],
        )
        assert driver.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == expected_mode
    assert [call[4] for call in calls] == ["cursor", "hybrid8"]


def test_run_hybrid8_binds_mode_task_bytes_and_instructions(
    driver, tmp_path, monkeypatch
):
    harbor = tmp_path / "harbor"
    harbor.write_text("not executed")
    harbor.chmod(0o700)
    out = tmp_path / "private-hybrid8"
    materialize = importlib.import_module("materialize")
    case = _suffix_case(driver, "2/7")
    seen = []

    def load(_dataset, case_id):
        assert case_id == "2/7"
        return case

    def start(args, *, cwd, env, **_kwargs):
        seen.append(cwd.name)
        assert cwd.name == "2-7-hybrid8"
        task = cwd / "task"
        wrapper = json.loads((task / "environment/case.json").read_bytes())
        assert wrapper == {"mode": "hybrid8", "case": case}
        for seat in driver.study.SEATS:
            text = (task / f"instructions/{seat}.md").read_text("utf-8")
            assert f"You are `{seat}`" in text
            assert "`mode` is `hybrid8`" in text
            assert "suffix-hex" in text
            assert "`mask` instead of `next`" in text
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(driver.study, "_source_case_ids", lambda _dataset: ["2/7"])
    monkeypatch.setattr(driver, "load_case", load)
    monkeypatch.setattr(materialize, "load_case", load)
    monkeypatch.setattr(driver, "_ready", lambda _env: None)
    monkeypatch.setattr(driver.subprocess, "run", start)
    report = driver.run(tmp_path, out, harbor, "2/7", mode="hybrid8")
    assert seen == ["2-7-hybrid8"]
    assert report["mode"] == "hybrid8"
    assert report["cells"] == [
        {
            "case_id": "2/7",
            "case_sha256": driver.study._sha(driver.study._canonical(case)),
            "status": "failed",
            "reason": "native trial returned an error",
        }
    ]
    plan = json.loads((out / "plan.json").read_bytes())
    assert plan["mode"] == "hybrid8"
    assert plan["cells"][0]["mode"] == "hybrid8"


def test_collect_hybrid8_aggregates_suffix_and_byte_counters(
    driver, reviewed, tmp_path, monkeypatch
):
    out = tmp_path / "private-hybrid8"
    cases = {
        "2/7": _suffix_case(driver, "2/7", null=False),
        "2/8": _suffix_case(driver, "2/8", null=True),
        "3/7": _short_case(driver, "3/7"),
        "4/7": _short_case(driver, "4/7"),
        "5/7": _short_case(driver, "5/7"),
    }
    plan = _register(driver, reviewed, out, cases, mode="hybrid8")
    _record_attempt(driver, reviewed, out, plan, cases["2/7"])
    _record_attempt(driver, reviewed, out, plan, cases["2/8"])
    _record_attempt(driver, reviewed, out, plan, cases["3/7"])
    (out / "cells" / "4-7-hybrid8").mkdir()
    # 5/7 stays unattempted.

    monkeypatch.setattr(driver.subprocess, "run", _never_launch)
    report = driver.collect(out)
    assert report["schema"] == "seatlab.calendar-interactive-result/v1"
    assert report["mode"] == "hybrid8"
    assert list(report["by_stratum"]) == ["2", "3", "4", "5"]

    verified_cells = {
        row["case_id"]: row["observed"]
        for row in report["cells"]
        if row["status"] == "verified"
    }
    assert set(verified_cells) == {"2/7", "2/8", "3/7"}
    assert verified_cells["2/7"]["choice"] == 10
    assert verified_cells["2/7"]["resources"]["suffix_exchanges"] == 1
    assert verified_cells["2/7"]["resources"]["suffix_masks"] == 5
    assert verified_cells["2/7"]["resources"]["suffix_bit_positions"] == 5 * (24 - 8)
    assert verified_cells["2/8"]["choice"] is None
    assert verified_cells["2/8"]["resources"]["suffix_exchanges"] == 1
    assert verified_cells["2/8"]["resources"]["suffix_masks"] == 5
    assert verified_cells["2/8"]["resources"]["suffix_bit_positions"] == 5 * (24 - 8)
    assert verified_cells["3/7"]["choice"] == 2
    assert verified_cells["3/7"]["resources"]["suffix_exchanges"] == 0
    assert verified_cells["3/7"]["resources"]["suffix_masks"] == 0
    assert verified_cells["3/7"]["resources"]["suffix_bit_positions"] == 0

    # Manual exact byte and distinct-disclosure derivation:
    # 2/7 & 2/8 (identical scalar trace across rounds 1..8, suffix round 9 at bound 8):
    #   Rounds 1..8 bounds are 0..7 (each 1 ASCII byte * 5 seats = 5 req bytes/round -> 40 req bytes).
    #   Rounds 1..8 replies are all single-digit integers 0..8 (5 * 1 = 5 resp bytes/round -> 40 resp bytes).
    #   Distinct (seat, tick) scalar disclosures: m1 reveals {1,3,5,7} (4), each of m2..m5 reveals
    #   {0,2,4,6,8} (5 each -> 20), total 24 distinct scalar disclosures.
    #   Round 9 suffix at bound 8: request ["suffix-hex",8] is 16 ASCII bytes * 5 = 80 req bytes;
    #   suffix width = ceil((24 - 8) / 4) = 4 hex digits, so each JSON string mask is 6 bytes * 5 = 30 resp bytes.
    #   Totals per suffix cell: request=120, response=70, payload=190.
    # 3/7 (2 scalar rounds, no suffix round):
    #   r1 b=0 -> (2,1,0,0,0), r2 b=2 -> (2,2,2,2,2).
    #   request=10, response=10, payload=20, distinct_disclosed_starts=9.
    assert verified_cells["2/7"]["resources"]["distinct_disclosed_starts"] == 24
    assert verified_cells["2/7"]["resources"]["request_payload_bytes"] == 120
    assert verified_cells["2/7"]["resources"]["response_payload_bytes"] == 70
    assert verified_cells["2/7"]["resources"]["payload_bytes"] == 190
    assert verified_cells["2/8"]["resources"]["distinct_disclosed_starts"] == 24
    assert verified_cells["2/8"]["resources"]["request_payload_bytes"] == 120
    assert verified_cells["2/8"]["resources"]["response_payload_bytes"] == 70
    assert verified_cells["2/8"]["resources"]["payload_bytes"] == 190
    assert verified_cells["3/7"]["resources"]["distinct_disclosed_starts"] == 9
    assert verified_cells["3/7"]["resources"]["request_payload_bytes"] == 10
    assert verified_cells["3/7"]["resources"]["response_payload_bytes"] == 10
    assert verified_cells["3/7"]["resources"]["payload_bytes"] == 20
    expected_req = 120 + 120 + 10
    expected_resp = 70 + 70 + 10
    expected_payload = expected_req + expected_resp
    assert report["summary"] == {
        "planned": 5,
        "unattempted": 1,
        "incomplete": 1,
        "failed": 0,
        "invalid": 0,
        "verified": 3,
        "measured_nulls": 1,
        "official_exact_matches": 2,
        "physically_feasible": 2,
        "rounds": 9 + 9 + 2,
        "scalar_disclosures": 40 + 40 + 10,
        "suffix_exchanges": 2,
        "suffix_masks": 10,
        "suffix_bit_positions": 160,
        "request_bytes": expected_req,
        "response_bytes": expected_resp,
        "payload_bytes": expected_payload,
    }
    assert report["by_stratum"]["3"]["suffix_exchanges"] == 0
    assert report["by_stratum"]["4"] == {
        "planned": 1,
        "unattempted": 0,
        "incomplete": 1,
        "failed": 0,
        "invalid": 0,
        "verified": 0,
        "measured_nulls": 0,
        "official_exact_matches": 0,
        "physically_feasible": 0,
        "rounds": 0,
        "scalar_disclosures": 0,
        "suffix_exchanges": 0,
        "suffix_masks": 0,
        "suffix_bit_positions": 0,
        "request_bytes": 0,
        "response_bytes": 0,
        "payload_bytes": 0,
    }
    statuses = {row["case_id"]: row["status"] for row in report["cells"]}
    assert statuses == {
        "2/7": "verified",
        "2/8": "verified",
        "3/7": "verified",
        "4/7": "incomplete",
        "5/7": "unattempted",
    }


def test_collect_preserves_cursor_summary_schema_without_hybrid_fields(
    driver, reviewed, tmp_path, monkeypatch
):
    out = tmp_path / "private-cursor"
    case = _short_case(driver, "2/1")
    plan = _register(driver, reviewed, out, {"2/1": case}, mode="cursor")
    _record_attempt(driver, reviewed, out, plan, case, mode="cursor")
    monkeypatch.setattr(driver.subprocess, "run", _never_launch)
    report = driver.collect(out)
    assert report["mode"] == "cursor"
    assert set(report["summary"]) == {
        "planned",
        "unattempted",
        "incomplete",
        "failed",
        "invalid",
        "verified",
        "measured_nulls",
        "official_exact_matches",
        "physically_feasible",
        "rounds",
        "scalar_disclosures",
    }
    assert set(report["cells"][0]["observed"]["resources"]) == set(
        driver.CURSOR_RESOURCE_KEYS
    )


def test_collect_rejects_conflicting_world_mode(driver, reviewed, tmp_path):
    out = tmp_path / "private-hybrid8"
    case = _short_case(driver, "2/7")
    plan = _register(driver, reviewed, out, {"2/7": case}, mode="hybrid8")
    _record_attempt(
        driver, reviewed, out, plan, case, mode="hybrid8", world_mode="cursor"
    )
    row = driver.collect(out)["cells"][0]
    assert row["status"] == "invalid"
    assert row["reason"] == "invalid interactive world evidence"
    assert "observed" not in row


@pytest.mark.parametrize("tamper", ["unknown-mode", "cell-mode", "case-json-hash"])
def test_collect_rejects_malformed_or_conflicting_plan_mode(
    driver, reviewed, tmp_path, tamper
):
    out = tmp_path / "private-hybrid8"
    case = _short_case(driver, "2/7")
    plan = _register(driver, reviewed, out, {"2/7": case}, mode="hybrid8")
    if tamper == "unknown-mode":
        plan["mode"] = "budget8"
    elif tamper == "cell-mode":
        plan["cells"][0]["mode"] = "cursor"
    else:
        # Replace task_sha256[case_id]["environment/case.json"] with cursor digest
        # while keeping plan["mode"] == "hybrid8".
        plan["task_sha256"]["2/7"]["environment/case.json"] = driver.study._sha(
            driver.study._canonical({"mode": "cursor", "case": case})
        )
    (out / "plan.json").write_bytes(driver.study._canonical(plan))
    with pytest.raises(ValueError, match="mode|source snapshot"):
        driver.collect(out)


def test_collect_invalidates_duplicate_native_ids_in_hybrid8(
    driver, reviewed, tmp_path
):
    out = tmp_path / "private-hybrid8"
    cases = {
        "2/7": _suffix_case(driver, "2/7"),
        "2/8": _short_case(driver, "2/8"),
    }
    plan = _register(driver, reviewed, out, cases, mode="hybrid8")
    identifier = uuid4()
    for case, value in zip(
        cases.values(), (str(identifier), identifier.hex), strict=True
    ):
        _record_attempt(driver, reviewed, out, plan, case, identifier=value)
    report = driver.collect(out)
    assert report["summary"]["invalid"] == 2
    assert report["summary"]["suffix_exchanges"] == 0
    assert report["summary"]["payload_bytes"] == 0
    for row in report["cells"]:
        assert row["status"] == "invalid"
        assert row["reason"] == "duplicate native trial UUID"
        assert "observed" not in row


def test_task_payloads_produce_distinct_cursor_and_hybrid8_instructions(
    driver, reviewed
):
    case = _short_case(driver, "2/7")
    cursor_payloads = driver._task_payloads(case, reviewed, mode="cursor")
    hybrid_payloads = driver._task_payloads(case, reviewed, mode="hybrid8")
    assert (
        cursor_payloads["environment/case.json"]
        != hybrid_payloads["environment/case.json"]
    )
    for seat in driver.study.SEATS:
        cursor_md = cursor_payloads[f"instructions/{seat}.md"].decode("utf-8")
        hybrid_md = hybrid_payloads[f"instructions/{seat}.md"].decode("utf-8")
        assert "`mode` is `cursor`" in cursor_md
        assert "suffix-hex" not in cursor_md
        assert "`mode` is `hybrid8`" in hybrid_md
        assert "suffix-hex" in hybrid_md
        assert "$" not in cursor_md and "$" not in hybrid_md


def test_collect_rejects_malformed_verifier_resource_counters(
    driver, reviewed, tmp_path, monkeypatch
):
    out = tmp_path / "private-hybrid8"
    case = _short_case(driver, "2/7")
    plan = _register(driver, reviewed, out, {"2/7": case}, mode="hybrid8")
    _record_attempt(driver, reviewed, out, plan, case)
    interactive_verify = importlib.import_module("interactive_verify")
    real_verify = interactive_verify.verify

    def bad_verify(case_arg, world_arg, *, expected_mode=None):
        score = real_verify(case_arg, world_arg, expected_mode=expected_mode)
        score["resources"].pop("suffix_bit_positions")
        return score

    monkeypatch.setattr(interactive_verify, "verify", bad_verify)
    row = driver.collect(out)["cells"][0]
    assert row["status"] == "invalid"
    assert row["reason"] == "invalid interactive world evidence"
    assert "observed" not in row
