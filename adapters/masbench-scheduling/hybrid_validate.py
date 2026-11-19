#!/usr/bin/env python3
"""Compare offline hybrid_cost.py JSON against native interactive.py collect outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

SCHEMA = "seatlab.calendar-hybrid-validation/v1"
OFFLINE_SCHEMA = "seatlab.calendar-hybrid-cost/v1"
NATIVE_SCHEMA = "seatlab.calendar-interactive-result/v1"
MODE = "hybrid8"
STRATA = ("2", "3", "4", "5")
REGISTERED_CASE_IDS = tuple(f"{s}/{n}" for s in STRATA for n in range(7, 13))
ARMS = ("cursor", "budget8", "hybrid8", "bitset")
STATUSES = ("unattempted", "incomplete", "failed", "invalid", "verified")
RESOURCE_MAP = (
    ("round_trips", "round_trips"), ("seat_exchanges", "seat_exchanges"),
    ("scalar_disclosures", "scalar_disclosures"), ("distinct_disclosed_starts", "distinct_disclosed_starts"),
    ("suffix_exchanges", "suffix_exchanges"), ("suffix_masks", "suffix_masks"),
    ("suffix_bit_positions", "suffix_bit_positions"), ("request_bytes", "request_payload_bytes"),
    ("response_bytes", "response_payload_bytes"), ("payload_bytes", "payload_bytes"),
)
RESOURCE_FIELDS = tuple(off for off, _ in RESOURCE_MAP)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _req(cond: object, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def _sha256(value: object) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    _req(not path.is_symlink() and path.is_file(), f"{path.name}: expected regular JSON file")
    def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            _req(k not in out, f"duplicate JSON key: {k}")
            out[k] = v
        return out
    def _nonfinite(tok: str) -> None:
        raise ValueError(f"non-finite JSON constant: {tok}")
    data = json.loads(path.read_bytes(), object_pairs_hook=_unique, parse_constant=_nonfinite)
    _req(isinstance(data, dict), f"{path.name}: expected JSON object")
    return data


def _uuid4(value: object, label: str) -> str:
    _req(isinstance(value, str), f"invalid {label}")
    parsed = UUID(value)
    _req(parsed.version == 4 and value in (str(parsed), parsed.hex), f"invalid {label}")
    return str(parsed)


def _sha_hex(value: object, label: str) -> str:
    _req(isinstance(value, str) and _SHA_RE.fullmatch(value), f"invalid {label}")
    return value


def _check_metrics(doc: Mapping[str, Any], key_map: Sequence[tuple[str, str]], label: str) -> dict[str, Any]:
    choice, feasible, exact = doc.get("choice"), doc.get("physically_feasible"), doc.get("official_exact_match")
    _req(choice is None or (type(choice) is int and choice >= 0), f"{label}: invalid choice")
    _req(feasible is None if choice is None else type(feasible) is bool, f"{label}: invalid physically_feasible")
    _req(type(exact) is bool, f"{label}: invalid official_exact_match")
    res = {}
    for canonical, src_key in key_map:
        val = doc.get(src_key)
        _req(type(val) is int and val >= 0, f"{label}.{src_key} must be non-negative int")
        res[canonical] = val
    _req(res["payload_bytes"] == res["request_bytes"] + res["response_bytes"], f"{label}: payload_bytes mismatch")
    return {"choice": choice, "physically_feasible": feasible, "official_exact_match": exact, **res}


def _load_offline(doc: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
    _req(doc.get("schema") == OFFLINE_SCHEMA, f"offline schema must be {OFFLINE_SCHEMA}")
    raw_cases = doc.get("cases")
    _req(isinstance(raw_cases, list), "offline cases must be a list")
    off_map = tuple((f, f) for f in RESOURCE_FIELDS)
    by_id: dict[str, dict[str, Any]] = {}
    for row in raw_cases:
        _req(isinstance(row, dict) and isinstance(row.get("case_id"), str), "invalid offline case row")
        cid = row["case_id"]
        _req(cid not in by_id, f"duplicate offline case_id: {cid}")
        arms = row.get("arms")
        _req(isinstance(arms, dict) and set(ARMS) <= set(arms), f"{cid}: missing offline arms")
        by_id[cid] = {
            "stratum": cid.split("/", 1)[0],
            "case_sha256": _sha_hex(row.get("case_sha256"), f"{cid} offline case_sha256"),
            "arms": {a: _check_metrics(arms[a], off_map, f"{cid} {a}") for a in ARMS},
        }
    missing = [cid for cid in REGISTERED_CASE_IDS if cid not in by_id]
    _req(not missing, f"offline report missing registered cases: {', '.join(missing)}")
    commit = doc.get("dataset_commit")
    _req(commit is None or isinstance(commit, str), "invalid dataset_commit")
    return {cid: by_id[cid] for cid in REGISTERED_CASE_IDS}, commit


def _load_native_source(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Load an interactive.py collect JSON file or revalidate a private registry directory."""
    _req(not path.is_symlink(), f"{path.name}: native input cannot be a symlink")
    if path.is_dir():
        adapter_dir = str(Path(__file__).resolve().parent)
        if adapter_dir not in sys.path:
            sys.path.insert(0, adapter_dir)
        import interactive  # sibling stdlib module; collect() never launches processes

        summary = interactive.collect(path)
        cases = _load_json(path / "source.json").get("cases")
        _req(isinstance(cases, dict), "invalid registry source.json cases")
        return summary, {cid: _sha256(case) for cid, case in cases.items()}
    return _load_json(path), {}

def _arm_totals(items: Sequence[Mapping[str, Any]], denom: int) -> dict[str, Any]:
    non_null = sum(item["choice"] is not None for item in items)
    return {
        "measured_non_null": non_null, "measured_null": len(items) - non_null,
        "official_exact_match": {"passes": sum(item["official_exact_match"] is True for item in items), "denominator": denom, "verified_denominator": len(items)},
        "physically_feasible": {"passes": sum(item["physically_feasible"] is True for item in items), "infeasible": sum(item["physically_feasible"] is False for item in items), "denominator": non_null},
        "sums": {f: sum(item[f] for item in items) for f in RESOURCE_FIELDS},
        "max": {f: max((item[f] for item in items), default=0) for f in RESOURCE_FIELDS},
    }


def _native_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys(STATUSES, 0)
    verified, matched, mismatched, sha_checked, sha_matched = [], 0, 0, 0, 0
    for r in rows:
        counts[r["status"]] += 1
        if r["case_sha256_match"] is not None:
            sha_checked += 1
            sha_matched += r["case_sha256_match"] is True
        if r["status"] == "verified":
            verified.append(r["native"])
            matched += r["matches_offline_hybrid8"] is True
            mismatched += r["matches_offline_hybrid8"] is False
    return {
        "registered": len(rows), **counts, "unknown_outcome": len(rows) - counts["verified"],
        "verified_matched_offline": matched, "verified_mismatched_offline": mismatched,
        "case_sha256_checked": sha_checked, "case_sha256_matched": sha_matched,
        "case_sha256_unchecked": len(rows) - sha_checked, **_arm_totals(verified, len(rows)),
    }


def validate(
    offline_doc: Mapping[str, Any],
    native_inputs: Sequence[tuple[Mapping[str, Any], Mapping[str, str]]],
) -> dict[str, Any]:
    """Cross-check registered hybrid8 native collect summaries against offline hybrid_cost.py."""
    _req(len(native_inputs) >= 1, "at least one native collect input is required")
    offline_by_id, dataset_commit = _load_offline(offline_doc)
    registered_set, expected_res_keys = set(REGISTERED_CASE_IDS), {nat for _, nat in RESOURCE_MAP}
    study_ids, seen_studies, seen_native_ids = [], set(), set()
    native_rows: dict[str, tuple[str, Mapping[str, Any], str | None]] = {}

    for summary, case_hashes in native_inputs:
        _req(summary.get("schema") == NATIVE_SCHEMA, f"native schema must be {NATIVE_SCHEMA}")
        _req(summary.get("mode") == MODE, f"native mode must be {MODE!r}, got {summary.get('mode')!r}")
        study_id = _uuid4(summary.get("study_id"), "study_id")
        _req(study_id not in seen_studies, f"duplicate study_id: {study_id}")
        seen_studies.add(study_id)
        study_ids.append(study_id)
        cells = summary.get("cells")
        _req(isinstance(cells, list) and len(cells) > 0, f"study {study_id}: cells must be non-empty list")
        for cell in cells:
            _req(isinstance(cell, dict) and isinstance(cell.get("case_id"), str), f"study {study_id}: invalid cell")
            cid = cell["case_id"]
            _req(cid in registered_set, f"foreign case_id outside registered 7..12 cohort: {cid}")
            _req(cid not in native_rows, f"duplicate case_id across native inputs: {cid}")
            _req(cell.get("status") in STATUSES, f"{cid}: invalid status {cell.get('status')!r}")
            cell_sha = _sha_hex(case_hashes[cid], f"{cid} registry case_sha256") if cid in case_hashes else None
            if "case_sha256" in cell:
                declared = _sha_hex(cell["case_sha256"], f"{cid} case_sha256")
                _req(cell_sha is None or cell_sha == declared, f"{cid}: conflicting case_sha256")
                cell_sha = declared
            native_rows[cid] = (study_id, cell, cell_sha)

    missing = [cid for cid in REGISTERED_CASE_IDS if cid not in native_rows]
    _req(not missing, f"missing registered case_ids in native inputs: {', '.join(missing)}")

    records: list[dict[str, Any]] = []
    for cid in REGISTERED_CASE_IDS:
        off, (study_id, cell, native_sha) = offline_by_id[cid], native_rows[cid]
        status = cell["status"]
        native_id = _uuid4(cell["native_id"], f"{cid} native_id") if "native_id" in cell else None
        if native_id is not None:
            _req(native_id not in seen_native_ids, f"duplicate native_id across studies: {native_id}")
            seen_native_ids.add(native_id)
        if status == "verified":
            _req(native_id is not None, f"{cid}: verified cell missing native_id")
            obs = cell.get("observed")
            _req(isinstance(obs, dict) and isinstance(obs.get("resources"), dict), f"{cid}: invalid observed")
            _req(set(obs["resources"]) == expected_res_keys, f"{cid}: invalid native resource keys")
            native_obs = _check_metrics({**obs, **obs["resources"]}, RESOURCE_MAP, f"{cid} native")
            reward = obs.get("reward")
            _req(type(reward) is int and reward == int(native_obs["official_exact_match"]), f"{cid}: reward mismatch")
            mismatches = [
                f for f in ("choice", "physically_feasible", "official_exact_match", *RESOURCE_FIELDS)
                if native_obs[f] != off["arms"][MODE][f]
            ]
        else:
            _req("observed" not in cell, f"{cid}: non-verified cell must not carry observed outcome")
            native_obs, mismatches = None, []
        sha_match = None if native_sha is None else (native_sha == off["case_sha256"])
        if sha_match is False:
            mismatches.append("case_sha256")
        record: dict[str, Any] = {
            "case_id": cid, "stratum": off["stratum"], "study_id": study_id, "native_id": native_id,
            "status": status, "case_sha256": off["case_sha256"], "native_case_sha256": native_sha,
            "case_sha256_match": sha_match, "native": native_obs, "offline": off["arms"],
            "matches_offline_hybrid8": None if status != "verified" else (len(mismatches) == 0),
            "mismatches": mismatches,
        }
        if "reason" in cell:
            _req(isinstance(cell["reason"], str), f"{cid}: invalid reason")
            record["reason"] = cell["reason"]
        records.append(record)

    native_tot = _native_totals(records)
    return {
        "schema": SCHEMA, "mode": MODE, "dataset_commit": dataset_commit,
        "study_ids": study_ids, "registered_case_ids": list(REGISTERED_CASE_IDS),
        "all_verified_match_offline": (
            native_tot["verified"] == len(records)
            and native_tot["verified_mismatched_offline"] == 0
            and native_tot["case_sha256_matched"] == len(records)
        ),
        "all_case_sha256_checked": native_tot["case_sha256_checked"] == len(records),
        "summary": {
            "native_hybrid8": native_tot,
            "offline_arms": {a: _arm_totals([r["offline"][a] for r in records], len(records)) for a in ARMS},
            "by_stratum": {
                s: {
                    "native_hybrid8": _native_totals([r for r in records if r["stratum"] == s]),
                    "offline_arms": {a: _arm_totals([r["offline"][a] for r in records if r["stratum"] == s], 6) for a in ARMS},
                }
                for s in STRATA
            },
        },
        "cases": records,
    }


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as stream:
        stream.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
    try:
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", type=Path, required=True, help="Offline hybrid_cost.py JSON report.")
    parser.add_argument(
        "--native", type=Path, nargs="+", required=True,
        help="One or more interactive.py collect JSON files or private registry directories.",
    )
    parser.add_argument("--out", type=Path, default=None, help="New JSON output file (refuses overwrite).")
    args = parser.parse_args(argv)
    try:
        report = validate(_load_json(args.offline), [_load_native_source(p) for p in args.native])
        if args.out is not None:
            _write_new_json(args.out, report)
        else:
            print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0 if report["all_verified_match_offline"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
