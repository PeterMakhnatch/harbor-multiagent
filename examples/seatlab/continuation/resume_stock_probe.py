from __future__ import annotations

import builtins
import hashlib
import json
import os
import stat
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from . import resume_clock

SCHEMA = "seatlab.continuation-resume-stock-probe/v1"
USAGE = "usage: python3.12 -BS -m examples.seatlab.continuation.resume_stock_probe STOCK_CHECKOUT"
PINNED_COMMIT = resume_clock.SOURCE_COMMIT
CHECKPOINT = "harness/src/multi_agent_harness/harbor/checkpoint.py"
MARKET = "tasks/seed-to-profit/task/environment/hub/market_plugin.py"
HUB = "tasks/seed-to-profit/task/environment/hub/make_hub_plugin.py"
PINNED = {
    CHECKPOINT: "6b24e93a3f2d5d33dbf8f716c1b56fc507501ad180f1754d6da574160055771b",
    MARKET: "43e7b637779d464e329b3aea1fa9fc97a5761bb14909bda17d9b17eaf16208a2",
    HUB: "90965ec216383b56645c898f3fc42de0f562f04ee52872f27de4f922430035b5",
}
SOURCE_LIMIT = 1 << 20
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
ENV_PREFIX = "SEEDPROFIT_"
EPOCH = 1763942400.0
TASK = "seed-to-profit"
STATE_ARC = resume_clock.STATE_DIR.lstrip("/")
WORLD_SEED_FIXTURE = "0" * 32
CREATED = "%Y-%m-%dT%H:%M:%SZ"
RESTARTS = (("immediate", 0.0), ("after_2h", 7200.0))
LIMITATIONS = (
    "completed months at the snapshot are a fixture set to the stock live_clock due count; no company month ran",
    "no marketsim subprocess, market trade, test, board, keepalive, sandbox, agent, or harness restore ran",
    "No full checkpoint load: the tested resume gate uses stock manifest, time_left and MIN_LEFT_SEC.",
    "the hub-state pack mirrors environment._pull_hub_state layout with tarfile, not GNU tar on a hub",
    "months due are counted by the restored office clock; months actually run after restart are not measured",
)


class Rejected(Exception):
    pass


class FixedClock:
    def __init__(self, at: float) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read_pinned(root: str, rel: str) -> tuple[str, bytes]:
    path = os.path.join(root, rel)
    if os.path.realpath(path) != path:
        raise Rejected
    try:
        fd = os.open(path, OPEN_FLAGS)
    except OSError:
        raise Rejected from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > SOURCE_LIMIT:
            raise Rejected
        data = os.read(fd, SOURCE_LIMIT + 1)
    except OSError:
        raise Rejected from None
    finally:
        os.close(fd)
    if len(data) > SOURCE_LIMIT or hashlib.sha256(data).hexdigest() != PINNED[rel]:
        raise Rejected
    return path, data


def _verified_sources(argv: list[str]) -> dict[str, tuple[str, bytes]]:
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        raise Rejected
    if any(key.startswith(ENV_PREFIX) for key in os.environ):
        raise Rejected
    root = os.path.realpath(argv[0])
    if not os.path.isdir(root):
        raise Rejected
    return {rel: _read_pinned(root, rel) for rel in PINNED}


def _exec_stock(rel: str, path: str, data: bytes) -> dict[str, Any]:
    _require(
        hashlib.sha256(data).hexdigest() == PINNED[rel],
        "bytes changed after verification",
    )
    namespace: dict[str, Any] = {
        "__name__": "stock_" + Path(rel).stem,
        "__file__": path,
        "__builtins__": builtins,
    }
    exec(compile(data, path, "exec", dont_inherit=True), namespace)  # noqa: S102
    return namespace


def _released_live(hub: dict[str, Any]) -> tuple[dict[str, Any], float]:
    live, run_sec = hub["LIVE"], hub["RUN_SEC"]
    _require(
        type(live) is dict and type(run_sec) is float,
        "stock LIVE or RUN_SEC has an unexpected type",
    )
    released = (live["launch_sec"], live["month_sec"], live["months"], run_sec)
    _require(
        released == resume_clock.PUBLISHED,
        "stock LIVE is not the released 4-hour clock",
    )
    return dict(live), run_sec


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _save_office(
    market: dict[str, Any], state: Path, live: dict[str, Any], snapshot: float
) -> dict[str, Any]:
    (state / "live").mkdir(parents=True, mode=0o700)
    with open(state / "live" / "live.json", "w") as f:
        json.dump(dict(live, world_seed=WORLD_SEED_FIXTURE), f)
    clock = FixedClock(EPOCH)
    office = market["MarketOffice"](
        None, dict(market["DEFAULTS"], state=str(state)), now=clock
    )
    _require(office.live is not None, "stock office did not load live.json")
    started = office.run_started()["run_started_at"]
    clock.at = snapshot
    due = office.live_clock(clock())
    with office.lock:
        office.s["live_months"] = due
        office.save()
    _require(not office.running and not office.live_busy, "stock office started work")
    return {
        "created_at": office.s["created_at"],
        "run_started_at": started,
        "due_at_snapshot": due,
        "completed_fixture": office.s["live_months"],
        "office_json_sha256": _digest(state / "office.json"),
        "live_json_sha256": _digest(state / "live" / "live.json"),
    }


def _commit_checkpoint(
    ck: dict[str, Any], base: Path, state_parent: Path, run_sec: float, snapshot: float
) -> Path:
    root = base / "job.checkpoints" / "trial"
    n = ck["next_n"](root)
    tmp = ck["begin"](root, n)
    with tarfile.open(tmp / ck["HUB_STATE"], "w:gz") as tf:
        tf.add(state_parent / STATE_ARC, arcname=STATE_ARC)
    manifest = {
        "format": ck["FORMAT"],
        "n": n,
        "reason": "probe",
        "final": False,
        "created": time.strftime(CREATED, time.gmtime(snapshot)),
        "task": TASK,
        "agent_timeout_sec": run_sec,
        "elapsed_agent_sec": round(snapshot - EPOCH, 1),
        "hub_state": [resume_clock.STATE_DIR],
    }
    final = ck["commit"](tmp, root, n, manifest)
    _require(
        ck["listing"](root) == [(n, final)],
        "stock listing does not show exactly the committed checkpoint",
    )
    _require(
        ck["_DIR"].match(final.name) is not None,
        "committed name does not match the stock pattern",
    )
    return final


def _restore(ck: dict[str, Any], final: Path, dest: Path) -> Path:
    dest.mkdir(mode=0o700)
    with tarfile.open(final / ck["HUB_STATE"], "r:gz") as tf:
        tf.extractall(dest, filter="data")
    return dest / STATE_ARC


def _open_restored(
    market: dict[str, Any], ck: dict[str, Any], state: Path, final: Path, at: float
) -> dict[str, Any]:
    before = _tree(state)
    office = market["MarketOffice"](
        None, dict(market["DEFAULTS"], state=str(state)), now=FixedClock(at)
    )
    anchor = office.run_started()["run_started_at"]
    due = office.live_clock(office.now())
    done = office.s.get("live_months", 0)
    manifest = ck["_manifest"](final)
    left = ck["time_left"](manifest)
    _require(_tree(state) == before, "opening the restored office changed its state")
    _require(not office.running and not office.live_busy, "stock office started work")
    return {
        "restart_at": at,
        "run_started_at_after_stock_call": anchor,
        "months_due": due,
        "months_completed": done,
        "backlog": due - done,
        "stock_live_tick_would_start_month": done < due,
        "remaining_agent_s": left,
        "harness_would_resume": left is None or left >= ck["MIN_LEFT_SEC"],
        "state": before,
    }


def _diagnose(
    state: Path,
    final: Path,
    snapshot: float,
    at: float,
    stock: dict[str, Any],
    saved: dict[str, Any],
) -> dict[str, Any]:
    paths = {
        "office.json": str(state / "office.json"),
        "live.json": str(state / "live" / "live.json"),
        "manifest.json": str(final / "manifest.json"),
    }
    result = resume_clock.evaluate(TASK, paths, at, snapshot)
    market = result["market"] or {}
    extra = stock["months_due"] - saved["due_at_snapshot"]
    compared = {
        "due_at_snapshot": market.get("due_at_snapshot") == saved["due_at_snapshot"],
        "due_at_restart": market.get("due_at_restart") == stock["months_due"],
        "months_run_at_snapshot": market.get("months_run_at_snapshot")
        == stock["months_completed"],
        "overdue_at_restart": market.get("overdue_at_restart") == stock["backlog"],
        "extra_due_across_interval": market.get("extra_due_across_interval") == extra,
        "remaining_agent_s": result["agent"]["remaining_s"]
        == round(stock["remaining_agent_s"], 1),
        "verdict": (result["verdict"] == "mismatch_observed") == (extra > 0),
    }
    return {
        "verdict": result["verdict"],
        "reasons": result["reasons"],
        "clock_shape": result["clock_shape"],
        "backlog_kind": market.get("backlog_kind"),
        "extra_due_across_interval": market.get("extra_due_across_interval"),
        "remaining_agent_s": result["agent"]["remaining_s"],
        "stock_extra_due_across_interval": extra,
        "agrees_with_stock": compared,
    }


def _verdict(restarts: dict[str, dict[str, Any]]) -> str:
    first, second = (restarts[label] for label, _ in RESTARTS)
    same_time = first["remaining_agent_s"] == second["remaining_agent_s"]
    same_due = first["months_due"] == second["months_due"]
    if same_time and same_due:
        return "control_passes"
    if same_time:
        return "control_fails_market_months_due_advance_while_agent_time_is_frozen"
    return "control_fails_remaining_agent_time_differs"


def probe(sources: dict[str, tuple[str, bytes]]) -> dict[str, Any]:
    ck = _exec_stock(CHECKPOINT, *sources[CHECKPOINT])
    market = _exec_stock(MARKET, *sources[MARKET])
    hub = _exec_stock(HUB, *sources[HUB])
    live, run_sec = _released_live(hub)
    snapshot = EPOCH + run_sec / 2
    with tempfile.TemporaryDirectory(prefix="resume-stock-probe-") as raw:
        base = Path(raw)
        saved_parent = base / "saved-hub"
        saved = _save_office(market, saved_parent / STATE_ARC, live, snapshot)
        final = _commit_checkpoint(ck, base, saved_parent, run_sec, snapshot)
        restarts: dict[str, dict[str, Any]] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        for label, delay in RESTARTS:
            state = _restore(ck, final, base / f"restore-{label}")
            stock = _open_restored(market, ck, state, final, snapshot + delay)
            restarts[label] = stock
            diagnostics[label] = _diagnose(
                state, final, snapshot, snapshot + delay, stock, saved
            )
        manifest = ck["_manifest"](final)
        saved_tree = _tree(saved_parent / STATE_ARC)
    trees = [r.pop("state") for r in restarts.values()]
    identical = all(tree == saved_tree for tree in trees)
    agree = all(all(d["agrees_with_stock"].values()) for d in diagnostics.values())
    return {
        "schema": SCHEMA,
        "verdict": _verdict(restarts),
        "diagnostic_agrees_with_stock": agree,
        "workload": {
            "type": "stock_code_fixture_clock_probe",
            "company_months_run": 0,
            "market_trades_run": 0,
            "marketsim_subprocesses_run": 0,
            "harness_or_sandbox_restore_run": False,
            "completed_months_source": "fixture_equal_to_stock_live_clock_at_snapshot",
        },
        "stock": {
            "pinned_commit": PINNED_COMMIT,
            "sha256": dict(PINNED),
            "loading": "verified_bytes_compile_exec_no_import_no_pyc",
            "live": live,
            "run_sec": run_sec,
            "seedprofit_env_overrides": 0,
        },
        "clock": {"epoch": EPOCH, "snapshot_at": snapshot, **saved},
        "checkpoint": {
            "format": manifest["format"],
            "created": manifest["created"],
            "agent_timeout_sec": manifest["agent_timeout_sec"],
            "elapsed_agent_sec": manifest["elapsed_agent_sec"],
            "hub_state": manifest["hub_state"],
            "min_left_sec": ck["MIN_LEFT_SEC"],
        },
        "restored_state_identical_to_saved": identical,
        "restarts": restarts,
        "resume_clock": {
            "schema": resume_clock.SCHEMA,
            "snapshot": "caller_supplied",
            "by_restart": diagnostics,
        },
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        sources = _verified_sources(sys.argv[1:] if argv is None else argv)
    except Rejected:
        sys.stderr.write(
            json.dumps(
                {"error": "stock checkout not accepted", "schema": SCHEMA},
                sort_keys=True,
            )
        )
        sys.stderr.write("\n" + USAGE + "\n")
        return 2
    result = probe(sources)
    text = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    sys.stdout.buffer.write((text + "\n").encode())
    sys.stdout.buffer.flush()
    return (
        0
        if result["diagnostic_agrees_with_stock"]
        and result["restored_state_identical_to_saved"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
