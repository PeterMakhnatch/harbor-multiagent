from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from . import resume_months as rm

SCHEMA = "seatlab.continuation-supplier-screen/v1"
PROG = "python -B -m examples.seatlab.continuation.supplier_screen"
CASES = tuple(range(6))
SUPPLIERS = ("A", "B")
SEED_LABEL = "seatlab/private-supplier/v1/screen/{index}/{purpose}"
USED = {rel: digest for rel, digest in rm.PINNED.items() if rel != rm.STARTER_OPS}
PRODUCT_COUNT = 10
BASE_FIELD = b", supplier: A}\n"
ARM_FIELD = {s: f", supplier: {s}}}\n".encode() for s in SUPPLIERS}
PLAN_SHA = {
    "A": rm.PINNED[rm.REFERENCE_PLAN],
    "B": "f9305097353b2b1e8f6843077ce795c81937975b25938d18027a2a33277ed663",
}
AUDIT_KIND = "supplier_audit"
REQUIREMENTS = {
    "python": "3.12",
    "flags": ["-B"],
    "dependencies": {
        "numpy": rm.DEPENDENCIES["numpy"],
        "pyyaml": rm.DEPENDENCIES["yaml"],
    },
    "effective_uid": "non-zero",
    "rejected_env": [*rm.REJECTED_ENV, rm.ENV_PREFIX + "*"],
    "arguments": ["BESPOKE_ROOT", "--case 0..5", "--supplier A|B"],
    "network": "not used",
    "git": "not used; the program folder is a plain folder",
}
LIMITATIONS = (
    "months advance by calling stock live.month in sequence; the market office wall clock and hub are not run",
    "both arms run the fixed official reference autopilot; only the 10 plan supplier fields differ",
    (
        "audit fees are 20 stock office.quote prices (one A and one B audit per reference product), "
        "subtracted as research spend in stock scoring.world_score; no audit is ordered, run or charged "
        "inside the company"
    ),
    (
        "live_score_after_fees is stock scoring.world_score on stock live.result; the grader's replay minimum, "
        "playbook half, office research ledger and forecast are not run"
    ),
    (
        "not root: stock runner makes no separate user, and its seccomp filter is Linux only, so the official "
        "reference program runs unsandboxed on macOS"
    ),
    "numpy and pyyaml are pinned by version, not by file bytes",
    (
        "stock software has a wall-clock limit (60 s per month, 20 s per call); a frozen month "
        "leaves this cell invalid, not a zero-score company"
    ),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, allow_abbrev=False)
    parser.add_argument("bespoke_root", metavar="BESPOKE_ROOT")
    parser.add_argument("--case", type=int, choices=CASES, required=True)
    parser.add_argument("--supplier", choices=SUPPLIERS, required=True)
    return parser


def _accept(argv: list[str]) -> tuple[argparse.Namespace, dict[str, tuple[str, bytes]]]:
    args = _parser().parse_args(argv)
    rm._accept_host()
    root = os.path.realpath(args.bespoke_root)
    if not os.path.isdir(root):
        raise rm.Rejected("BESPOKE_ROOT is not a directory")
    sources = {rel: rm._read_pinned(root, rel) for rel in USED}
    try:
        _arm_plan(sources[rm.REFERENCE_PLAN][1], args.supplier)
    except AssertionError as e:
        raise rm.Rejected(str(e)) from None
    return args, sources


def _arm_plan(base: bytes, supplier: str) -> bytes:
    rm._require(rm._sha(base) == PLAN_SHA["A"], "reference plan bytes changed")
    rm._require(
        base.count(b"supplier:") == PRODUCT_COUNT
        and base.count(BASE_FIELD) == PRODUCT_COUNT,
        "reference plan does not hold exactly 10 supplier A fields",
    )
    arm = base.replace(BASE_FIELD, ARM_FIELD[supplier])
    changed = sum(1 for a, b in zip(base, arm, strict=True) if a != b)
    rm._require(
        changed == (0 if supplier == "A" else PRODUCT_COUNT),
        "arm plan is not a field-only supplier change",
    )
    rm._require(
        rm._sha(arm) == PLAN_SHA[supplier], "arm plan is not the pinned derivation"
    )
    return arm


def _products(planmod: Any, base: bytes, arm: bytes, supplier: str) -> list[str]:
    reference = planmod.parse(base.decode())
    applied = planmod.parse(arm.decode())
    ids = [p["id"] for p in reference["products"]]
    rm._require(
        len(ids) == PRODUCT_COUNT and len(set(ids)) == PRODUCT_COUNT,
        "stock plan does not list 10 products",
    )
    rm._require(
        all(p["supplier"] == "A" for p in reference["products"]),
        "reference plan is not all supplier A",
    )
    expected = dict(
        reference,
        products=[dict(p, supplier=supplier) for p in reference["products"]],
    )
    rm._require(applied == expected, "arm plan differs beyond the 10 supplier fields")
    return ids


def _fees(office: Any, ids: list[str]) -> dict[str, Any]:
    quotes: list[dict[str, Any]] = []
    for pid in ids:
        for supplier in SUPPLIERS:
            quote = office.quote(AUDIT_KIND, pid, {"supplier": supplier})
            cost = float(quote["cost_usd"])
            rm._require(
                quote["kind"] == AUDIT_KIND and quote["target"] == pid,
                "stock quote names another test",
            )
            rm._require(
                math.isfinite(cost) and cost > 0, "stock quote is not a positive price"
            )
            quotes.append(
                {
                    "product": pid,
                    "supplier": supplier,
                    "cost_usd": cost,
                    "facility": quote["facility"],
                    "minutes": quote["minutes"],
                }
            )
    rm._require(len(quotes) == 2 * PRODUCT_COUNT, "expected 20 audit quotes")
    return {
        "kind": AUDIT_KIND,
        "source": "stock office.quote; simulated business spending, not an order receipt or a charge",
        "count": len(quotes),
        "quotes": quotes,
        "total_usd": round(math.fsum(q["cost_usd"] for q in quotes), 2),
    }


def _stage_repo(base: Path, sources: dict[str, tuple[str, bytes]], plan: bytes) -> Path:
    repo = base / "program"
    (repo / "company").mkdir(parents=True, mode=0o700)
    (repo / "ops").mkdir(mode=0o700)
    rm._write_new(repo / "company" / "plan.yaml", plan)
    rm._write_new(repo / "ops" / "autopilot.py", sources[rm.REFERENCE_OPS][1])
    return repo


def _check_repo(repo: Path, plan_sha: str) -> None:
    rm._require(
        sorted(os.listdir(repo)) == ["company", "ops"],
        "program folder has extra entries",
    )
    rm._require(
        sorted(os.listdir(repo / "company")) == ["plan.yaml"],
        "company/ has extra entries",
    )
    rm._require(
        sorted(os.listdir(repo / "ops")) == ["autopilot.py"], "ops/ has extra entries"
    )
    rm._require(
        rm._sha((repo / "ops" / "autopilot.py").read_bytes()) == USED[rm.REFERENCE_OPS],
        "placed program is not the official reference",
    )
    rm._require(
        rm._sha((repo / "company" / "plan.yaml").read_bytes()) == plan_sha,
        "placed plan is not the arm plan",
    )


def _lines(company: Any) -> dict[str, Any] | None:
    if company is None:
        return None
    return {
        "plan_accepted": getattr(company, "plan_note", None) is None,
        "products": sorted(company.lines),
        "suppliers": dict(
            sorted(Counter(ln.supplier for ln in company.lines.values()).items())
        ),
    }


def _row(state: Path, k: int, entry: dict[str, Any]) -> dict[str, Any]:
    seen = rm._executed(state, k, "reference", entry)
    return {
        "month": k,
        "first_day": seen["first_day"],
        "last_day": seen["last_day"],
        "executed": seen["executed"],
        "ops_sha256": seen["ops_sha256"],
        "plan_sha256": seen["plan_sha256"],
        "software_frozen": bool(seen["software_frozen"]),
        "reference_state_kept": seen["reference_state_kept"],
        "notes_count": len(entry["notes"]),
        "value": seen["value"],
        "bankrupt": seen["bankrupt"],
    }


def _error(e: Exception) -> dict[str, Any]:
    return {
        "type": type(e).__name__,
        "message": str(e) if isinstance(e, AssertionError) else None,
    }


def _run_months(
    live: Any, state: Path, repo: Path, payload: dict[str, str], plan_sha: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    rows: list[dict[str, Any]] = []
    opening: dict[str, Any] | None = None
    try:
        rm._require(
            live.status(str(state))["started"] is False,
            "stock live company already started",
        )
        for k in range(rm.MONTHS):
            _check_repo(repo, plan_sha)
            rm._require(os.geteuid() != 0, "effective uid became 0")
            out = live.month(dict(payload))
            rm._require(
                out["months"] == k + 1, "stock live_month did not log exactly one month"
            )
            rows.append(_row(state, k, out["log"][k]))
            if k == 0:
                opening = _lines(live.load(str(state)))
            if out["ended"]:
                break
        _check_repo(repo, plan_sha)
    except Exception as e:  # noqa: BLE001
        return rows, opening, _error(e)
    return rows, opening, None


def _score(live: Any, scoring: Any, state: Path, fees: float) -> dict[str, Any] | None:
    result = live.result(str(state))
    company = live.load(str(state))
    if result is None or company is None:
        return None
    bankrupt = bool(result["bankrupt"])
    raw = company.value() if company.bankrupt_day is None else {"value": 0.0}
    return {
        "live_score_after_fees": float(scoring.world_score(result, fees)),
        "live_score_no_fees": float(scoring.world_score(result, 0.0)),
        "multiple_after_fees": 0.0
        if bankrupt
        else float(scoring.multiple(result["value"], fees)),
        "multiple_no_fees": 0.0
        if bankrupt
        else float(scoring.multiple(result["value"], 0.0)),
        "value_before_fees": float(result["value"]),
        "value_before_fees_unrounded": float(raw["value"]),
        "value_parts_unrounded": {k: float(v) for k, v in raw.items()},
        "fees_usd": fees,
        "bankrupt": bankrupt,
        "bankrupt_day": result["bankrupt_day"],
        "bankrupt_reason": result["bankrupt_reason"],
        "days": int(result["days"]),
        "seed_money": float(scoring.RULES["seed_money"]),
        "top_multiple": float(scoring.TOP),
        "years": result["years"],
        "company_months": result["months"],
    }


def _execute(
    base: Path,
    sources: dict[str, tuple[str, bytes]],
    live_json: dict[str, Any],
    payload: dict[str, str],
    supplier: str,
) -> dict[str, Any]:
    plan = _arm_plan(sources[rm.REFERENCE_PLAN][1], supplier)
    staged = rm._stage_package(base, sources)
    repo = _stage_repo(base, sources, plan)
    state = base / "state"
    (state / "live").mkdir(parents=True, mode=0o700)
    rm._write_new(
        state / "live" / "live.json", json.dumps(live_json, sort_keys=True).encode()
    )
    work = base / "tmp"
    work.mkdir(mode=0o700)
    live, scoring = rm._import_stock(staged, sources)
    ids = _products(
        sys.modules[f"{rm.PACKAGE}.plan"], sources[rm.REFERENCE_PLAN][1], plan, supplier
    )
    fees = _fees(sys.modules[f"{rm.PACKAGE}.office"], ids)
    previous = tempfile.tempdir
    tempfile.tempdir = str(work)
    try:
        month_payload = dict(payload, state=str(state), repo=str(repo))
        rows, opening, error = _run_months(
            live, state, repo, month_payload, PLAN_SHA[supplier]
        )
        status = live.status(str(state))
        terminal = _lines(live.load(str(state)))
        score = _score(live, scoring, state, fees["total_usd"])
    finally:
        tempfile.tempdir = previous
    return {
        "ids": ids,
        "fees": fees,
        "rows": rows,
        "opening": opening,
        "terminal": terminal,
        "error": error,
        "status": status,
        "score": score,
        "staged_sources_intact": rm._intact(staged),
        "stock_temp_leftovers": len(os.listdir(work)),
    }


def _world(case: int) -> tuple[dict[str, Any], dict[str, str]]:
    labels = {p: SEED_LABEL.format(index=case, purpose=p) for p in ("world", "truth")}
    seeds = {p: rm._sha(label.encode()) for p, label in labels.items()}
    public = {
        "case": case,
        "shape": rm.HUB_LIVE["shape"],
        "change_day": rm.HUB_LIVE["change_day"],
        "world_label": labels["world"],
        "truth_label": labels["truth"],
        "derivation": "seed = lower-case sha256 hex of the label",
        "world_seed_sha256": rm._sha(seeds["world"].encode()),
        "truth_secret_sha256": rm._sha(seeds["truth"].encode()),
    }
    return public, seeds


def _observed(done: dict[str, Any], supplier: str) -> dict[str, Any]:
    rows, status = done["rows"], done["status"]
    executed = [r["executed"] for r in rows]
    bankrupt = bool(status.get("bankrupt"))
    ended = bool(status.get("ended"))
    opening = done["opening"]
    return {
        "months_run": len(rows),
        "ended": ended,
        "bankrupt": bankrupt,
        "completed": done["error"] is None
        and ended
        and (len(rows) == rm.MONTHS or bankrupt),
        "reference_every_month": bool(rows) and all(x == "reference" for x in executed),
        "unknown_program_months": executed.count("unknown"),
        "plan_matches_arm_every_month": bool(rows)
        and all(r["plan_sha256"] == PLAN_SHA[supplier] for r in rows),
        "software_frozen_months": [r["month"] for r in rows if r["software_frozen"]],
        "reference_state_consistent": all(
            r["software_frozen"] or r["reference_state_kept"] for r in rows
        ),
        "opening_lines": opening,
        "terminal_lines": done["terminal"],
        "arm_applied_at_opening": opening is not None
        and opening["plan_accepted"]
        and opening["products"] == sorted(done["ids"])
        and opening["suppliers"] == {supplier: PRODUCT_COUNT},
    }


def _snapshots(rows: list[dict[str, Any]]) -> dict[str, Any]:
    chain = [
        [
            r["month"],
            r["executed"],
            r["ops_sha256"],
            r["plan_sha256"],
            r["software_frozen"],
        ]
        for r in rows
    ]
    text = json.dumps(chain, separators=(",", ":")).encode()
    return {
        "count": len(rows),
        "members_checked": len(rows) * len(rm.SNAPSHOT_MEMBERS),
        "chain": "json [month, executed, ops_sha256, plan_sha256, software_frozen] per captured month",
        "chain_sha256": rm._sha(text),
    }


def _outcome(observed: dict[str, Any], score: dict[str, Any] | None) -> str:
    if score is None:
        return "missing"
    return "complete" if observed["completed"] else "incomplete"


def _denominator() -> dict[str, Any]:
    cells = [f"case{c}/supplier{s}" for c in CASES for s in SUPPLIERS]
    return {"cells": len(cells), "sha256": rm._sha(("\n".join(cells) + "\n").encode())}


def _source() -> dict[str, Any]:
    manifest = json.dumps(USED, sort_keys=True, separators=(",", ":")).encode()
    return {
        "commit": rm.SOURCE_COMMIT,
        "sha256": dict(USED),
        "manifest_sha256": rm._sha(manifest),
        "arm_plans": {
            s: {
                "from": rm.REFERENCE_PLAN,
                "change": f"each of the 10 product supplier fields A -> {s}",
                "sha256": PLAN_SHA[s],
            }
            for s in SUPPLIERS
        },
        "runner_sha256": rm._sha(Path(__file__).read_bytes()),
        "helper_sha256": rm._sha(Path(rm.__file__).read_bytes()),
        "loading": "verified_bytes_staged_in_temp_compile_exec_no_pyc",
    }


def _valid(result: dict[str, Any]) -> bool:
    observed, score = result["observed"], result["score"]
    return (
        result["outcome"] == "complete"
        and result["error"] is None
        and score is not None
        and score["bankrupt"] == observed["bankrupt"]
        and observed["reference_every_month"]
        and observed["plan_matches_arm_every_month"]
        and observed["unknown_program_months"] == 0
        and not observed["software_frozen_months"]
        and observed["reference_state_consistent"]
        and observed["arm_applied_at_opening"]
        and result["staged_sources_intact"]
    )


def run(
    args: argparse.Namespace,
    sources: dict[str, tuple[str, bytes]],
    deps: dict[str, str],
) -> dict[str, Any]:
    hub_live = rm._exec_hub(sources)
    os.environ[rm.ENV_PREFIX + "TEAM_PYTHON"] = sys.executable
    world, seeds = _world(args.case)
    payload = {"secret": seeds["truth"], "ref": "main"}
    live_json = dict(hub_live, world_seed=seeds["world"])
    with tempfile.TemporaryDirectory(prefix="supplier-screen-") as raw:
        base = Path(raw)
        rm._require(
            base.is_absolute() and stat.S_IMODE(base.stat().st_mode) == 0o700,
            "temp root not private",
        )
        done = _execute(base, sources, live_json, payload, args.supplier)
    observed = _observed(done, args.supplier)
    result = {
        "schema": SCHEMA,
        "cell": f"case{args.case}/supplier{args.supplier}",
        "denominator": _denominator(),
        "source": _source(),
        "requirements": REQUIREMENTS,
        "interpreter": {"version": sys.version.split()[0], **deps},
        "world": world,
        "live_settings": hub_live,
        "arm": {
            "supplier": args.supplier,
            "program": "reference",
            "plan_sha256": PLAN_SHA[args.supplier],
            "products": done["ids"],
        },
        "fees": done["fees"],
        "observed": observed,
        "snapshots": _snapshots(done["rows"]),
        "months": done["rows"],
        "score": done["score"],
        "outcome": _outcome(observed, done["score"]),
        "error": done["error"],
        "staged_sources_intact": done["staged_sources_intact"],
        "stock_temp_leftovers": done["stock_temp_leftovers"],
        "limitations": list(LIMITATIONS),
    }
    result["valid"] = _valid(result)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args, sources = _accept(sys.argv[1:] if argv is None else argv)
        deps = rm._dependencies()
    except rm.Rejected as e:
        sys.stderr.write(
            json.dumps({"error": str(e), "schema": SCHEMA}, sort_keys=True) + "\n"
        )
        return 2
    result = run(args, sources, deps)
    text = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    sys.stdout.buffer.write((text + "\n").encode())
    sys.stdout.buffer.flush()
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
