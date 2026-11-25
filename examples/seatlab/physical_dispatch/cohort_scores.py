import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...native_study import require
from ..exhibit_pairs import DIR_FLAGS
from .cohort import _private_folder, check_binding, load_design, roster_slot
from .cohort_draw import CELLS
from .contrast import STATUSES, summarize
from .order_evidence import reconcile

ERRORS = (
    OSError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    RecursionError,
    RuntimeError,
)


def unknown(exc: Exception) -> dict[str, Any]:
    missing = isinstance(exc, FileNotFoundError)
    return {
        "evidence": "insufficient" if missing else "unverified",
        "status": "missing_artifact" if missing else "inconsistent_or_unreadable",
        "native_reward": None,
    }


@contextmanager
def _names(path: Path, *, private: bool):
    fd = _private_folder(path) if private else os.open(path, DIR_FLAGS)
    try:
        with os.scandir(fd) as it:
            yield (entry.name for entry in it)
    finally:
        os.close(fd)


def checked_trial(
    binding: dict[str, Any], plan: Path, cell: str, trial: Path
) -> dict[str, Any]:
    from .physical_evidence import inspect_trial

    result = inspect_trial(plan, cell, trial)
    require(
        result["artifact_digests"]["plan"] == binding["input_receipt_digest"]
        and result["case_commitment"]
        == binding["case_commitments"][cell.split("-", 1)[0]],
        "archive differs from occupied slot",
    )
    return {
        **result,
        "cohort_digest": binding["cohort_digest"],
        "case_id": binding["case_id"],
    }


def _case(
    study: dict[str, Any], receipt: dict[str, Any], row: dict[str, Any], plan: Path
) -> dict[str, dict[str, Any]]:
    names = {f"exhibit-physical-receiver-{row['seed']}-{cell}": cell for cell in CELLS}
    try:
        with _names(plan / "trials", private=False) as entries:
            present = set()
            for name in entries:
                require(name in names, "unexpected trial in enrolled case")
                present.add(name)
        _, slot = roster_slot(study, receipt, row["id"])
        binding = check_binding(row, slot, plan)
    except ERRORS as exc:
        return {cell: unknown(exc) for cell in CELLS}
    cells = {}
    for name, cell in names.items():
        if name not in present:
            cells[cell] = unknown(FileNotFoundError())
            continue
        try:
            cells[cell] = checked_trial(binding, plan, cell, plan / "trials" / name)
        except ERRORS as exc:
            cells[cell] = unknown(exc)
    return cells


def score_cohort(study: Path, root: Path, expect: str) -> dict[str, Any]:
    design, receipt = load_design(study, expect)
    tickets = [row["id"] for row in design["cases"]]
    with _names(root, private=True) as entries:
        require(
            all(name in tickets for name in entries),
            "unexpected case in study input root",
        )
    trials = {
        row["id"]: _case(design, receipt, row, root / row["id"])
        for row in design["cases"]
    }
    orders = {row["id"]: row["order"] for row in design["cases"]}
    audited, order_audit = reconcile(orders, trials)
    cells = {}
    by_condition = {cell: dict.fromkeys(STATUSES, 0) for cell in CELLS}
    for ticket, reports in audited.items():
        cells[ticket] = {
            cell: {"status": report["status"], "native_reward": report["native_reward"]}
            for cell, report in reports.items()
        }
        for cell, report in reports.items():
            by_condition[cell][report["status"]] += 1
    return {
        "schema": "seatlab.physical-dispatch.cohort-summary/v1",
        "cohort_digest": expect,
        "sampling": receipt["sampling"],
        "status_by_condition": by_condition,
        **order_audit,
        "source": "supplied local canonical slots; no provider, actual order or attempt authentication",
        **summarize(tickets, cells),
    }
