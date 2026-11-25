from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, cast

from ...native_study import record_bytes, require, write_exclusive
from ...seat_checkpoints.materialize import sync_directory
from ...ship_inflight.materialize import publish
from ..exhibit_enroll import digest, target
from ..exhibit_pairs import DIR_FLAGS, read_json
from ..exhibit_prepare import CHECKOUT
from .certificate import certify
from .cohort_draw import make_design, validate_design
from .preflight import host_fingerprint

COHORT = "seatlab.physical-dispatch.cohort/v1"
SLOT = "seatlab.physical-dispatch.cohort-slot/v1"


def _receipt(design: dict[str, Any]) -> dict[str, Any]:
    n = len(design["cases"])
    fields = {
        "schema": COHORT,
        "sampling": "development_only"
        if design["development"]
        else "private_random_schedule",
        "cases": n,
        "planned_decisions": 4 * n,
        "candidates_checked": design["candidates_checked"],
        "eligible_population": "naturally_realized_first_public_brief",
        "independent_unit": "warehouse_case_not_cell_or_item",
        "design_digest": digest(record_bytes(design)),
        "host_source_digest": host_fingerprint(CHECKOUT),
        "executions": 0,
    }
    return {**fields, "cohort_digest": digest(record_bytes(fields))}


def enroll(out: Path, n: int, development_seed: int | None = None) -> dict[str, Any]:
    require(
        type(n) is int and 4 <= n <= 64 and n % 4 == 0,
        "cases must be a multiple of 4 in 4..64",
    )
    require(
        development_seed is None
        or (type(development_seed) is int and 0 <= development_seed < 2**63),
        "invalid development seed",
    )
    out = target(out)
    secret = (
        secrets.token_bytes(32)
        if development_seed is None
        else hashlib.sha256(
            b"seatlab.physical-development/v1\0" + development_seed.to_bytes(8, "big")
        ).digest()
    )
    design = make_design(n, secret, development=development_seed is not None)
    receipt = _receipt(design)
    stage = Path(tempfile.mkdtemp(prefix=".physical-cohort-", dir=out.parent))
    try:
        for name, record in (("design.json", design), ("receipt.json", receipt)):
            write_exclusive(stage / name, record, 0o600)
        sync_directory(stage)
        publish(stage, out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    sync_directory(out.parent)
    return receipt


def _private_folder(path: Path) -> int:
    fd = os.open(path, DIR_FLAGS)
    try:
        st = os.fstat(fd)
        require(
            st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700,
            "directory must be private and owned by the operator",
        )
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_record(fd: int, name: str) -> dict[str, Any]:
    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    require(
        stat.S_ISREG(st.st_mode)
        and st.st_uid == os.geteuid()
        and stat.S_IMODE(st.st_mode) == 0o600,
        "record must be a private operator-owned regular file",
    )
    raw = read_json(fd, name)
    require(type(raw) is dict, "expected an object")
    return cast(dict[str, Any], raw)


def load_design(
    path: Path, expect: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    fd = _private_folder(path)
    try:
        receipt = _private_record(fd, "receipt.json")
        if expect is not None:
            require(
                type(expect) is str
                and re.fullmatch(r"sha256:[0-9a-f]{64}", expect) is not None
                and receipt.get("cohort_digest") == expect,
                "cohort differs from external commitment",
            )
        design = validate_design(_private_record(fd, "design.json"))
    finally:
        os.close(fd)
    require(
        record_bytes(_receipt(design)) == record_bytes(receipt),
        "cohort receipt differs from its design or host code",
    )
    return design, receipt


def roster_slot(
    design: dict[str, Any], receipt: dict[str, Any], ticket: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    expect = receipt["cohort_digest"]
    require(
        type(expect) is str and len(expect) == 71,
        "supply the original external cohort commitment",
    )
    require(
        type(ticket) is str and re.fullmatch(r"case-[0-9]{4}", ticket) is not None,
        "invalid case ID",
    )
    index = int(ticket[5:]) - 1
    require(0 <= index < len(design["cases"]), "case is not in roster")
    row = design["cases"][index]
    require(row["id"] == ticket, "case is not in this position")
    slot = {
        "schema": SLOT,
        "cohort_digest": expect,
        "case_id": ticket,
        "sampling": receipt["sampling"],
        "case_commitments": row["case_commitments"],
        "condition_order": row["order"],
    }
    proof = certify(row["seed"])
    require(
        proof["development_seed"] == row["seed"]
        and proof["candidates_checked"] == 1
        and proof["case_commitments"] == row["case_commitments"],
        "certificate does not describe enrolled case",
    )
    slot["qualification"] = {
        "certificate_digest": digest(record_bytes(proof)),
        "native_dispatches_checked": proof["native_commits"],
        "request_bytes": proof["final_owner_request_bytes"],
        "attainable_other": proof["comparison"]["before_observed"],
        "attainable_relevant": proof["comparison"]["after_observed"],
        "measured_model_outcomes": 0,
    }
    return row, slot


def qualify_case(study: Path, ticket: str, expect: str) -> dict[str, Any]:
    _, slot = roster_slot(*load_design(study, expect), ticket)
    return {
        "cohort_digest": expect,
        "case_id": ticket,
        "sampling": slot["sampling"],
        **slot["qualification"],
    }


def bind_case(
    study: Path,
    ticket: str,
    expect: str,
    task: Path,
    out: Path,
    endpoints: dict[str, str],
) -> dict[str, Any]:
    from .receiver_plan import prepare_receivers

    row, slot = roster_slot(*load_design(study, expect), ticket)
    prep = prepare_receivers(task, out, row["seed"], endpoints, study_slot=slot)
    return {
        "schema": SLOT,
        "case_id": ticket,
        "cohort_digest": expect,
        "sampling": slot["sampling"],
        "input_receipt_digest": digest(record_bytes(prep)),
        "condition_order": row["order"],
        "executions": 0,
    }


def check_slot(study: Path, ticket: str, expect: str, plan: Path) -> dict[str, Any]:
    row, slot = roster_slot(*load_design(study, expect), ticket)
    return check_binding(row, slot, plan)


def check_binding(
    row: dict[str, Any], slot: dict[str, Any], plan: Path
) -> dict[str, Any]:
    fd = _private_folder(plan)
    try:
        inputs = _private_record(fd, "receipt.json")
        binding = _private_record(fd, "cohort-binding.json")
    finally:
        os.close(fd)
    require(
        type(inputs.get("seed")) is int and inputs["seed"] == row["seed"],
        "native input seed differs from enrollment",
    )
    require(
        record_bytes(inputs["case_commitments"])
        == record_bytes(row["case_commitments"]),
        "native cases differ from enrollment",
    )
    expected = {**slot, "input_receipt_digest": digest(record_bytes(inputs))}
    require(
        record_bytes(binding) == record_bytes(expected),
        "native input binding differs from enrollment",
    )
    return binding
