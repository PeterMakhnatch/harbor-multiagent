"""Tests for adapters/masbench-scheduling/hybrid_validate.py without running Docker or actors."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

import hybrid_cost  # noqa: E402
import hybrid_validate  # noqa: E402
import interactive  # noqa: E402
from cases import DATASET_COMMIT  # noqa: E402

ADAPTER_TESTS = ADAPTER / "tests"
if str(ADAPTER_TESTS) not in sys.path:
    sys.path.insert(0, str(ADAPTER_TESTS))

import test_hybrid_registration as reg  # noqa: E402


def _make_case(case_id: str, index: int) -> dict[str, Any]:
    """Build deterministic valid cases covering scalar, suffix-hit, and suffix-null outcomes."""
    if index % 3 == 0:
        return reg._short_case(interactive, case_id)
    if index % 3 == 1:
        return reg._suffix_case(interactive, case_id, null=False)
    return reg._suffix_case(interactive, case_id, null=True)


def _offline_report(cases_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [hybrid_cost.analyze_case(cases_by_id[cid]) for cid in hybrid_validate.REGISTERED_CASE_IDS]
    return {
        "schema": hybrid_cost.SCHEMA,
        "execution": "offline_deterministic_simulation",
        "dataset_commit": DATASET_COMMIT,
        "case_count": len(rows),
        "cases": rows,
    }


def _native_cell_from_offline(off_row: dict[str, Any], *, native_id: str | None = None) -> dict[str, Any]:
    h8 = off_row["arms"]["hybrid8"]
    return {
        "case_id": off_row["case_id"],
        "native_id": native_id or str(uuid4()),
        "status": "verified",
        "observed": {
            "choice": h8["choice"],
            "official_exact_match": h8["official_exact_match"],
            "physically_feasible": h8["physically_feasible"],
            "reward": int(h8["official_exact_match"]),
            "resources": {
                native_key: h8[off_key]
                for off_key, native_key in hybrid_validate.RESOURCE_MAP
            },
        },
    }


def _native_summary(cells: list[dict[str, Any]], *, mode: str = "hybrid8", study_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": interactive.RESULT_SCHEMA,
        "study_id": study_id or uuid4().hex,
        "mode": mode,
        "cells": cells,
    }


def _build_full_fixture() -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    cases = {cid: _make_case(cid, idx) for idx, cid in enumerate(hybrid_validate.REGISTERED_CASE_IDS)}
    offline = _offline_report(cases)
    first12 = [_native_cell_from_offline(row) for row in offline["cases"][:12]]
    second12 = [_native_cell_from_offline(row) for row in offline["cases"][12:]]
    return cases, offline, _native_summary(first12), _native_summary(second12)


def test_disjoint_two_registrations_match_and_emit_path_free_report(tmp_path: Path) -> None:
    _, offline, part_a, part_b = _build_full_fixture()
    offline_path = tmp_path / "offline.json"
    part_a_path = tmp_path / "collect-a.json"
    part_b_path = tmp_path / "collect-b.json"
    out_path = tmp_path / "validation.json"
    offline_path.write_text(json.dumps(offline), encoding="utf-8")
    part_a_path.write_text(json.dumps(part_a), encoding="utf-8")
    part_b_path.write_text(json.dumps(part_b), encoding="utf-8")

    rc = hybrid_validate.main([
        "--offline", str(offline_path),
        "--native", str(part_b_path), str(part_a_path),
        "--out", str(out_path),
    ])
    assert rc == 1  # Outcomes agree, but the report omits the underlying case commitments.
    raw_out = out_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in raw_out
    assert "collect-a.json" not in raw_out
    assert "offline.json" not in raw_out

    report = json.loads(raw_out)
    assert report["schema"] == hybrid_validate.SCHEMA
    assert report["mode"] == "hybrid8"
    assert report["all_verified_match_offline"] is False
    assert report["all_case_sha256_checked"] is False
    assert [row["case_id"] for row in report["cases"]] == list(hybrid_validate.REGISTERED_CASE_IDS)
    assert report["summary"]["native_hybrid8"]["registered"] == 24
    assert report["summary"]["native_hybrid8"]["verified"] == 24
    assert report["summary"]["native_hybrid8"]["unknown_outcome"] == 0
    assert report["summary"]["native_hybrid8"]["verified_matched_offline"] == 24
    assert report["summary"]["native_hybrid8"]["case_sha256_checked"] == 0
    assert report["summary"]["native_hybrid8"]["case_sha256_unchecked"] == 24
    assert report["summary"]["native_hybrid8"]["measured_null"] == 8
    assert report["summary"]["native_hybrid8"]["measured_non_null"] == 16
    assert report["summary"]["native_hybrid8"]["sums"] == report["summary"]["offline_arms"]["hybrid8"]["sums"]
    assert set(report["summary"]["offline_arms"]) == set(hybrid_validate.ARMS)
    assert list(report["summary"]["by_stratum"]) == ["2", "3", "4", "5"]

    # Refuses to overwrite an existing output file.
    assert hybrid_validate.main([
        "--offline", str(offline_path),
        "--native", str(part_a_path), str(part_b_path),
        "--out", str(out_path),
    ]) == 2


def test_raw_registry_directories_verify_case_sha256_and_never_launch_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(interactive.subprocess, "run", reg._never_launch)
    reviewed = {name: (ADAPTER / name).read_bytes() for name in interactive.REVIEWED_FILES}
    cases = {cid: _make_case(cid, idx) for idx, cid in enumerate(hybrid_validate.REGISTERED_CASE_IDS)}
    offline = _offline_report(cases)

    reg_a = tmp_path / "reg-a"
    reg_b = tmp_path / "reg-b"
    ids_a = list(hybrid_validate.REGISTERED_CASE_IDS[:12])
    ids_b = list(hybrid_validate.REGISTERED_CASE_IDS[12:])
    plan_a = reg._register(interactive, reviewed, reg_a, {cid: cases[cid] for cid in ids_a}, mode="hybrid8")
    plan_b = reg._register(interactive, reviewed, reg_b, {cid: cases[cid] for cid in ids_b}, mode="hybrid8")
    for cid in ids_a:
        reg._record_attempt(interactive, reviewed, reg_a, plan_a, cases[cid])
    for cid in ids_b:
        reg._record_attempt(interactive, reviewed, reg_b, plan_b, cases[cid])

    offline_path = tmp_path / "offline.json"
    out_path = tmp_path / "registry-validation.json"
    offline_path.write_text(json.dumps(offline), encoding="utf-8")

    rc = hybrid_validate.main([
        "--offline", str(offline_path),
        "--native", str(reg_a), str(reg_b),
        "--out", str(out_path),
    ])
    assert rc == 0
    raw = out_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in raw
    report = json.loads(raw)
    assert report["all_verified_match_offline"] is True
    assert report["all_case_sha256_checked"] is True
    assert report["summary"]["native_hybrid8"]["case_sha256_checked"] == 24
    assert report["summary"]["native_hybrid8"]["case_sha256_matched"] == 24
    assert all(row["case_sha256_match"] is True and row["native_id"] is not None for row in report["cases"])


def test_case_sha256_mismatch_fails_match_and_returns_exit_1(tmp_path: Path) -> None:
    _, offline, part_a, part_b = _build_full_fixture()
    part_a["cells"][0]["case_sha256"] = "0" * 64
    report = hybrid_validate.validate(offline, [(part_a, {}), (part_b, {})])
    assert report["all_verified_match_offline"] is False
    assert report["summary"]["native_hybrid8"]["verified_mismatched_offline"] == 1
    assert report["summary"]["native_hybrid8"]["case_sha256_checked"] == 1
    assert report["summary"]["native_hybrid8"]["case_sha256_matched"] == 0
    assert report["cases"][0]["case_sha256_match"] is False
    assert report["cases"][0]["mismatches"] == ["case_sha256"]

    offline_path = tmp_path / "offline.json"
    a_path = tmp_path / "a.json"
    b_path = tmp_path / "b.json"
    offline_path.write_text(json.dumps(offline), encoding="utf-8")
    a_path.write_text(json.dumps(part_a), encoding="utf-8")
    b_path.write_text(json.dumps(part_b), encoding="utf-8")
    assert hybrid_validate.main(["--offline", str(offline_path), "--native", str(a_path), str(b_path)]) == 1


def test_registry_tampered_case_sha256_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(interactive.subprocess, "run", reg._never_launch)
    reviewed = {name: (ADAPTER / name).read_bytes() for name in interactive.REVIEWED_FILES}
    cases = {cid: _make_case(cid, idx) for idx, cid in enumerate(hybrid_validate.REGISTERED_CASE_IDS)}
    offline = _offline_report(cases)
    offline["cases"][0]["case_sha256"] = "f" * 64

    reg_dir = tmp_path / "reg-all"
    plan = reg._register(interactive, reviewed, reg_dir, cases, mode="hybrid8")
    for cid in hybrid_validate.REGISTERED_CASE_IDS:
        reg._record_attempt(interactive, reviewed, reg_dir, plan, cases[cid])

    offline_path = tmp_path / "offline.json"
    out_path = tmp_path / "mismatch.json"
    offline_path.write_text(json.dumps(offline), encoding="utf-8")
    rc = hybrid_validate.main([
        "--offline", str(offline_path),
        "--native", str(reg_dir),
        "--out", str(out_path),
    ])
    assert rc == 1
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["all_verified_match_offline"] is False
    assert report["all_case_sha256_checked"] is True
    assert report["cases"][0]["case_sha256_match"] is False
    assert report["cases"][0]["mismatches"] == ["case_sha256"]


@pytest.mark.parametrize(
    "field,native_key,bad_value",
    [
        ("choice", None, 999),
        ("physically_feasible", None, False),
        ("official_exact_match", None, False),
        ("round_trips", "round_trips", 99),
        ("seat_exchanges", "seat_exchanges", 99),
        ("scalar_disclosures", "scalar_disclosures", 99),
        ("distinct_disclosed_starts", "distinct_disclosed_starts", 99),
        ("suffix_exchanges", "suffix_exchanges", 2),
        ("suffix_masks", "suffix_masks", 10),
        ("suffix_bit_positions", "suffix_bit_positions", 999),
        ("request_bytes", "request_payload_bytes", 999),
        ("response_bytes", "response_payload_bytes", 999),
    ],
)
def test_every_field_and_resource_mismatch_is_detected(
    field: str, native_key: str | None, bad_value: Any
) -> None:
    _, offline, part_a, part_b = _build_full_fixture()
    # Pick cell 0 which is a short_case with non-null choice=2, feasible=True, exact=True.
    cell = part_a["cells"][0]
    if native_key is None:
        cell["observed"][field] = bad_value
        if field == "official_exact_match":
            cell["observed"]["reward"] = int(bad_value)
    else:
        cell["observed"]["resources"][native_key] = bad_value
        if native_key in ("request_payload_bytes", "response_payload_bytes"):
            res = cell["observed"]["resources"]
            res["payload_bytes"] = res["request_payload_bytes"] + res["response_payload_bytes"]

    report = hybrid_validate.validate(offline, [(part_a, {}), (part_b, {})])
    assert report["all_verified_match_offline"] is False
    assert field in report["cases"][0]["mismatches"]
    assert report["cases"][0]["matches_offline_hybrid8"] is False


def test_preserves_unattempted_incomplete_failed_invalid_separately_and_never_as_success() -> None:
    _, offline, part_a, part_b = _build_full_fixture()
    # Replace first 4 cells with each non-verified status; keep remaining 20 verified.
    statuses = [
        ("unattempted", None, None),
        ("incomplete", None, None),
        ("failed", str(uuid4()), "native trial returned an error"),
        ("invalid", str(uuid4()), "invalid interactive world evidence"),
    ]
    for idx, (status, nid, reason) in enumerate(statuses):
        cid = part_a["cells"][idx]["case_id"]
        replacement: dict[str, Any] = {"case_id": cid, "status": status}
        if nid is not None:
            replacement["native_id"] = nid
        if reason is not None:
            replacement["reason"] = reason
        part_a["cells"][idx] = replacement

    report = hybrid_validate.validate(offline, [(part_a, {}), (part_b, {})])
    assert report["all_verified_match_offline"] is False
    tot = report["summary"]["native_hybrid8"]
    assert tot["registered"] == 24
    assert tot["verified"] == 20
    assert tot["unattempted"] == 1
    assert tot["incomplete"] == 1
    assert tot["failed"] == 1
    assert tot["invalid"] == 1
    assert tot["unknown_outcome"] == 4
    assert tot["official_exact_match"]["denominator"] == 24
    assert tot["official_exact_match"]["verified_denominator"] == 20
    # Non-verified cells must remain unknown (None), never measured null or matched.
    for idx in range(4):
        row = report["cases"][idx]
        assert row["native"] is None
        assert row["matches_offline_hybrid8"] is None
    assert tot["measured_null"] + tot["measured_non_null"] == 20


def test_rejects_duplicate_foreign_missing_mode_and_duplicate_uuid_errors() -> None:
    _, offline, part_a, part_b = _build_full_fixture()

    # Wrong mode.
    wrong_mode = copy.deepcopy(part_a)
    wrong_mode["mode"] = "cursor"
    with pytest.raises(ValueError, match="native mode must be 'hybrid8'"):
        hybrid_validate.validate(offline, [(wrong_mode, {}), (part_b, {})])

    # Foreign case_id (2/1 is outside 7..12).
    foreign = copy.deepcopy(part_a)
    foreign["cells"][0]["case_id"] = "2/1"
    with pytest.raises(ValueError, match="foreign case_id"):
        hybrid_validate.validate(offline, [(foreign, {}), (part_b, {})])

    # Duplicate case_id across two disjoint registrations.
    dup_case = copy.deepcopy(part_b)
    dup_case["cells"].append(copy.deepcopy(part_a["cells"][0]))
    with pytest.raises(ValueError, match="duplicate case_id"):
        hybrid_validate.validate(offline, [(part_a, {}), (dup_case, {})])

    # Missing registered case_id.
    with pytest.raises(ValueError, match="missing registered case_ids"):
        hybrid_validate.validate(offline, [(part_a, {})])

    # Duplicate study_id across inputs.
    dup_study = copy.deepcopy(part_b)
    dup_study["study_id"] = part_a["study_id"]
    with pytest.raises(ValueError, match="duplicate study_id"):
        hybrid_validate.validate(offline, [(part_a, {}), (dup_study, {})])

    # Duplicate native_id across two studies.
    dup_uuid = copy.deepcopy(part_b)
    dup_uuid["cells"][0]["native_id"] = part_a["cells"][0]["native_id"]
    with pytest.raises(ValueError, match="duplicate native_id"):
        hybrid_validate.validate(offline, [(part_a, {}), (dup_uuid, {})])

    # Non-verified cell carrying an observed outcome is rejected.
    bad_obs = copy.deepcopy(part_a)
    bad_obs["cells"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="non-verified cell must not carry observed"):
        hybrid_validate.validate(offline, [(bad_obs, {}), (part_b, {})])


def test_module_does_not_import_actor_or_simulator() -> None:
    source = (ADAPTER / "hybrid_validate.py").read_text(encoding="utf-8")
    for forbidden in ("interactive_client", "interactive_world", "hybrid_cost", "cursor", "subprocess"):
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source


def test_rejects_payload_bytes_inconsistency() -> None:
    _, offline, part_a, part_b = _build_full_fixture()
    part_a["cells"][0]["observed"]["resources"]["payload_bytes"] += 1
    with pytest.raises(ValueError, match="payload_bytes mismatch"):
        hybrid_validate.validate(offline, [(part_a, {}), (part_b, {})])
