import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pytest

from examples.seatlab.visibility import (
    calendar_client,
    calendar_protocol,
    calendar_replica,
    spend,
)
from examples.seatlab.visibility import calendar_tools_evidence as evidence
from examples.seatlab.visibility import calendar_tools_protocol as tools
from examples.seatlab.visibility import calendar_tools_report as report

_BLOCK = calendar_protocol.Block(1, 0, 0)


def _proof(policy: str, trial: str, source: str) -> evidence._Policy:
    run = tools.native_id(_BLOCK)
    actions = {}
    for identifier in calendar_client._request_plan(_BLOCK):
        uid = tools.action_id(_BLOCK, policy, identifier)
        action = evidence._action(uid, identifier, _BLOCK)
        action.update(status="bound", tool_status="not_requested")
        actions[identifier] = action
    return {
        "protocol_status": "complete",
        "actions": actions,
        "native": {
            "native_sha256": tools.digest(source),
            "semantics_sha256": tools.digest("same rules"),
            "arms": {
                "peer": {
                    "trial_id_sha256": tools.digest(trial),
                    "status": "complete",
                    "native_timeline_verified": True,
                    "reward": 1,
                    "slot": 14,
                }
            },
            "turns": {f"{run}-peer:cal_decide": {}},
        },
    }


@pytest.mark.parametrize("changed", ["source", "trial"])
def test_copy_or_rekey_cannot_form_a_policy_contrast(changed: str) -> None:
    off = _proof("off", "A", "same credentials")
    on = _proof(
        "on",
        "A" if changed == "trial" else "B",
        "same credentials" if changed == "trial" else "different credentials",
    )
    conflicts = report._identity([(_BLOCK, "off", off), (_BLOCK, "on", on)])
    assert conflicts == (
        ({tools.digest("A")}, set()) if changed == "trial" else (set(), {1})
    )
    for policy, proof in (("on", on), ("off", off)):
        row = report._row(_BLOCK, policy, "peer", proof, *conflicts)
        assert row["native_status"] == "invalid_evidence"
        assert (row["protocol_bound"], row["reward"], row["slot"]) == (
            False,
            None,
            None,
        )


def test_a_native_score_survives_missing_tool_evidence_but_cannot_score_the_policy() -> (
    None
):
    proof = _proof("off", "A", "source")
    good = report._row(_BLOCK, "off", "peer", proof, set(), set())
    assert (good["protocol_bound"], good["reward"]) == (True, 1)
    del proof["actions"][tools.native_id(_BLOCK) + ":cal_initial_p2"]
    proof["protocol_status"] = "missing"
    native_only = report._row(_BLOCK, "off", "peer", proof, set(), set())
    assert (
        native_only["native_status"],
        native_only["protocol_bound"],
        native_only["reward"],
    ) == ("complete", False, 1)


@pytest.mark.parametrize("reason", ["missing", "invalid"])
def test_unreadable_policy_preserves_registered_unknown(
    reason: Literal["missing", "invalid"],
) -> None:
    proof = report._blocked(reason)
    assert report._identity([(_BLOCK, "on", proof)]) == (set(), set())
    row = report._row(_BLOCK, "on", "peer", proof, set(), set())
    assert (row["native_status"], row["protocol_bound"], row["reward"]) == (
        reason + "_evidence",
        False,
        None,
    )


def test_meter_keeps_the_two_stages_and_the_original_charges_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path.resolve()
    ledger_dir = private / "ledger"
    ledger_dir.mkdir(mode=0o700)
    spend.initialize(ledger_dir, Decimal(12), Decimal(1))
    monkeypatch.setattr(spend, "_HOST_LEDGER", ledger_dir)
    proof = _proof("off", "A", "source")
    identifier = tools.native_id(_BLOCK) + ":cal_initial_p1"
    action = proof["actions"][identifier]
    proof["actions"] = {identifier: action}
    directory = private / "policy"
    directory.mkdir(mode=0o700)
    meter = directory / "meter"
    meter.mkdir(mode=0o700)
    uid = action["run"]
    instant = datetime(2026, 11, 20, 16, tzinfo=UTC)
    # Both calls really settle. Substituting the planning response for the
    # acting response must break only the second host-to-native link.
    action.update(
        select_prompt_sha256=tools.digest("selection"),
        select_response_sha256=tools.digest("none"),
        final_prompt_sha256=tools.digest("base and no tool result"),
        final_response_sha256=tools.digest("opening list"),
    )
    for role, p, r in (
        ("cal_tool_pick", "selection", "none"),
        ("cal_tool_reply", "base and no tool result", "none"),
    ):
        spend.reserve(uid, role, balance_usd=Decimal(12))
        spend.settle(
            uid,
            role,
            cache_hit=8,
            cache_miss=22,
            output=13,
            served_model="deepseek-flash",
            started_at=instant,
            finished_at=instant,
        )
        receipt: dict[str, Any] = {
            "schema": calendar_client._METER_SCHEMA,
            "attempt": 1,
            "status": "success",
            "run": uid,
            "role": role,
            "prompt_sha256": tools.digest(p),
            "response_sha256": tools.digest(r),
            "served_model": "deepseek-flash",
            "input_tokens": 30,
            "cache_tokens": 8,
            "output_tokens": 13,
        }
        path = meter / f"{uid}_{role}.json"
        path.write_text(json.dumps(receipt))
        path.chmod(0o600)
    ledger, index = report._billing(ledger_dir)
    assert ledger is not None and len(index) == 2
    matched, issues = report._meters(directory, _BLOCK, "off", proof, index)
    assert matched == {(uid, "cal_tool_pick")} and issues == {"response_mismatch": 1}
    assert (uid, "cal_tool_reply") in index and ledger["unsettled_calls"] == 0


def test_separate_processes_cannot_share_the_same_compose_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = tmp_path.resolve() / "locks"
    monkeypatch.setattr(calendar_replica, "_LOCKS", locks)
    child = """from pathlib import Path
import sys
from examples.seatlab.visibility import calendar_replica as r, calendar_protocol as p
r._LOCKS = Path(sys.argv[1]); block = p.Block(int(sys.argv[2]), 0, 0)
try:
    with r._coordinate_lease(block): print('admitted')
except r._Failed as exc: print(str(exc))
"""

    def try_world(world: int) -> str:
        return subprocess.check_output(
            [sys.executable, "-B", "-c", child, str(locks), str(world)],
            text=True,
            timeout=20,
        ).strip()

    with (
        pytest.raises(RuntimeError, match="abort"),
        calendar_replica._coordinate_lease(_BLOCK),
    ):
        assert try_world(1) == "coordinate_busy"
        assert try_world(2) == "admitted"
        raise RuntimeError("abort")
    assert try_world(1) == "admitted"
