from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import resume_months as rm

SCHEMA = "seatlab.deploy-queue-cell/v1"
PROG = "python -B -m examples.seatlab.continuation.deploy_queue_cell"
PAIRS = tuple(range(8))
SIDES = ("X", "Y")
LANDINGS = (13, 17)
CUTOFF = rm.SWITCH_AFTER
SEED_LABEL = "seatlab/deploy-queue/v1/pair/{pair}/{side}/{purpose}"
BOOK_KEYS = ("month", "revenue", "cash_at_end")
LIMITATIONS = (
    *rm.LIMITATIONS,
    "public_books are month, revenue and cash_at_end from stock live.books after exactly 13 months",
    "a frozen month, unknown program or missing stock outcome leaves the cell invalid with no score",
)


def _accept(
    argv: list[str],
) -> tuple[tuple[int, str, int], dict[str, tuple[str, bytes]]]:
    parser = argparse.ArgumentParser(prog=PROG, allow_abbrev=False)
    parser.add_argument("bespoke_root", metavar="BESPOKE_ROOT")
    parser.add_argument("--pair", choices=[str(p) for p in PAIRS], required=True)
    parser.add_argument("--side", choices=SIDES, required=True)
    parser.add_argument("--landing", choices=[str(n) for n in LANDINGS], required=True)
    args = parser.parse_args(argv)
    rm._accept_host()
    root = os.path.realpath(args.bespoke_root)
    if not os.path.isdir(root):
        raise rm.Rejected("BESPOKE_ROOT is not a directory")
    sources = {rel: rm._read_pinned(root, rel) for rel in rm.PINNED}
    return (int(args.pair), args.side, int(args.landing)), sources


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _canonical(data: Any) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _public_books(view: Any) -> list[dict[str, Any]] | None:
    months = (
        view.get("months")
        if type(view) is dict and view.get("started") is True
        else None
    )
    if type(months) is not list or len(months) != CUTOFF:
        return None
    if not all(type(m) is dict and all(k in m for k in BOOK_KEYS) for m in months):
        return None
    picked = [{k: m[k] for k in BOOK_KEYS} for m in months]
    ok = all(
        type(r["month"]) is int
        and r["month"] == k
        and _finite(r["revenue"])
        and _finite(r["cash_at_end"])
        for k, r in enumerate(picked)
    )
    return picked if ok else None


def _score(live: Any, scoring: Any, state: Path) -> dict[str, Any] | None:
    result = live.result(str(state))
    if result is None:
        return None
    return {
        "value": float(result["value"]),
        "live_score": float(scoring.world_score(result, rm.RESEARCH_SPENT)),
        "bankrupt": bool(result["bankrupt"]),
        "seed_money": float(scoring.RULES["seed_money"]),
        "top_multiple": float(scoring.TOP),
    }


def _execute(
    base: Path,
    sources: dict[str, tuple[str, bytes]],
    live_json: dict[str, Any],
    payload: dict[str, str],
    planned: list[str],
) -> dict[str, Any]:
    staged = rm._stage_package(base, sources)
    repo = rm._stage_repo(base, sources)
    state = base / "state"
    (state / "live").mkdir(parents=True, mode=0o700)
    rm._write_new(
        state / "live" / "live.json", json.dumps(live_json, sort_keys=True).encode()
    )
    work = base / "tmp"
    work.mkdir(mode=0o700)
    live, scoring = rm._import_stock(staged, sources)
    payload = dict(payload, state=str(state), repo=str(repo))
    done: dict[str, Any] = {
        "rows": [],
        "view": None,
        "status": {},
        "score": None,
        "error": None,
    }
    previous = tempfile.tempdir
    tempfile.tempdir = str(work)
    try:
        rm._require(
            live.status(str(state))["started"] is False,
            "stock live company already started",
        )
        for k in range(rm.MONTHS):
            if k == CUTOFF:
                done["view"] = live.books({"state": str(state)})
            rm._place_program(repo, planned[k], sources)
            rm._require(os.geteuid() != 0, "effective uid became 0")
            out = live.month(dict(payload))
            rm._require(
                out["months"] == k + 1, "stock live_month did not log exactly one month"
            )
            done["rows"].append(rm._executed(state, k, planned[k], out["log"][k]))
            if out["ended"]:
                break
        done["status"] = live.status(str(state))
        done["score"] = _score(live, scoring, state)
    except Exception as e:  # noqa: BLE001
        message = str(e) if isinstance(e, AssertionError) else None
        done["error"] = {"type": type(e).__name__, "message": message}
    finally:
        tempfile.tempdir = previous
    return dict(done, intact=rm._intact(staged))


def _reason(done: dict[str, Any], books: list[dict[str, Any]] | None) -> str | None:
    error = None if done["error"] is None else done["error"]["type"]
    seen = rm._observed(done["rows"], done["status"], error)
    score = done["score"]
    checks = (
        (error is None, "stock_error"),
        (done["intact"], "sources_changed"),
        (seen["unknown_program_months"] == 0, "unknown_program"),
        (not seen["software_frozen_months"], "software_frozen"),
        (seen["programs_match_schedule"], "schedule_mismatch"),
        (seen["plan_is_reference_every_month"], "plan_mismatch"),
        (seen["reference_state_consistent"], "reference_state_inconsistent"),
        (seen["completed"], "incomplete"),
        (done["view"] is not None, "books_missing"),
        (books is not None, "books_invalid"),
        (score is not None, "score_missing"),
        (
            score is not None
            and score["bankrupt"] == seen["bankrupt"]
            and _finite(score["value"])
            and _finite(score["live_score"]),
            "score_invalid",
        ),
    )
    return next((code for ok, code in checks if not ok), None)


def _month(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "month": row["month"],
        "program": row["executed"],
        "plan_sha256": row["plan_sha256"],
        "ops_sha256": row["ops_sha256"],
        "frozen": bool(row["software_frozen"]),
        "bankrupt": bool(row["bankrupt"]),
    }


def run(
    cell: tuple[int, str, int],
    sources: dict[str, tuple[str, bytes]],
    deps: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    pair, side, landing = cell
    hub_live = rm._exec_hub(sources)
    os.environ[rm.ENV_PREFIX + "TEAM_PYTHON"] = sys.executable
    seeds = {
        p: rm._sha(SEED_LABEL.format(pair=pair, side=side, purpose=p).encode())
        for p in ("world", "truth")
    }
    planned = rm._planned(False, landing - rm.SWITCH_AFTER)
    live_json = dict(hub_live, world_seed=seeds["world"])
    payload = {"secret": seeds["truth"], "ref": "main"}
    with tempfile.TemporaryDirectory(prefix="deploy-queue-cell-") as raw:
        base = Path(raw)
        private = base.is_absolute() and stat.S_IMODE(base.stat().st_mode) == 0o700
        rm._require(private, "temp root not private")
        done = _execute(base, sources, live_json, payload, planned)
    books = _public_books(done["view"])
    reason = _reason(done, books)
    source = {
        "commit": rm.SOURCE_COMMIT,
        "manifest_sha256": rm._sha(_canonical(rm.PINNED)),
        "runner_sha256": rm._sha(Path(__file__).read_bytes()),
        "helper_sha256": rm._sha(Path(rm.__file__).read_bytes()),
        "interpreter": sys.version.split()[0],
        "dependencies": deps,
        "loading": "verified_bytes_staged_in_temp_compile_exec_no_pyc",
    }
    return {
        "schema": SCHEMA,
        "pair": pair,
        "side": side,
        "landing": landing,
        "stock_commit": rm.SOURCE_COMMIT,
        "valid": reason is None,
        "public_books": books or [],
        "public_books_sha256": rm._sha(_canonical(books or [])),
        "score": done["score"] if reason is None else None,
        "months": [_month(r) for r in done["rows"]],
        "reason": reason,
        "world_commitments": {p: rm._sha(s.encode()) for p, s in seeds.items()},
        "source": source,
        "limitations": list(LIMITATIONS),
    }, done["error"]


def main(argv: list[str] | None = None) -> int:
    try:
        cell, sources = _accept(sys.argv[1:] if argv is None else argv)
        deps = rm._dependencies()
    except rm.Rejected as e:
        sys.stderr.write(
            _canonical({"error": str(e), "schema": SCHEMA}).decode() + "\n"
        )
        return 2
    result, error = run(cell, sources, deps)
    if error is not None:
        sys.stderr.write(_canonical({"stock_error": error}).decode() + "\n")
    sys.stdout.buffer.write(_canonical(result) + b"\n")
    sys.stdout.buffer.flush()
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
