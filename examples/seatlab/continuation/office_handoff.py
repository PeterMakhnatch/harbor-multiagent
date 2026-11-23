from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from . import resume_clock, resume_months, resume_stock_probe

SCHEMA = "seatlab.continuation-office-handoff/v1"
USAGE = "usage: python -B -m examples.seatlab.continuation.office_handoff BESPOKE_ROOT"
BOARD_DIR = "harness/src/multi_agent_harness/board"
BOARD = {
    "ratelimit": "0461d67d87403bf117b68e6a73c9b3444a6939b3c755d6e3f1e1fdeea966112e",
    "core": "65e44723876b3b6b07f0936db921c5954c1e65e26f1f4f65f279574a6e596421",
}
BOARD_PACKAGE = "stock_board"
KIND, TARGET, PARAMS = "supplier_audit", "h5", {"supplier": "A"}
ROLES = {"orderer": "p1", "planner": "p2"}
SECRET = resume_months._sha(b"seatlab/private-office/v1/handoff/truth")
EPOCH = resume_stock_probe.EPOCH
OFFICE_COMMANDS = ("catalog", "quote", "run")
REFUSAL = "only the agent who ordered this test can read its result"
WORKER_JOIN_S = 600.0
REPORT = {
    "product": str,
    "supplier": str,
    "sample": int,
    "defective": int,
    "lead_times_days": list,
    "fill_rates": list,
    "quoted_lead_days": (int, float),
}
EVENT_FIELDS = (
    "op",
    "seq",
    "seqs",
    "returned",
    "test",
    "kind",
    "target",
    "cost_usd",
    "facility",
)
TRANSPORT = {
    "office_cli": "substituted: stock MarketOffice.cli keeps its argv, env and payload; plugin-namespace "
    "subprocess.run calls pinned marketsim.office.main in process (resume_months finder); catalog, quote, run only; "
    "cli timeout not enforced",
    "office_clock": "substituted: stock MarketOffice now= is a fixed simulated clock; register() clock thread not run",
    "test_worker": "stock MarketOffice.tick starts its own test thread; this runner joins it",
    "board": "stock Board in sqlite :memory:, no log file; package __init__ and server not loaded",
    "tool_identity": "board.authenticate(bearer token) then the MarketOffice method, as register().run does",
}
UNOBSERVABLE = {
    "mcp_test_result_wrapper": "unobservable: register() needs fastapi and mcp and starts a 2 s wall-clock thread; "
    "the wrapped MarketOffice.result was called instead",
    "host_elapsed_time": "unobservable: not measured; all offsets are simulated clock seconds",
}
LIMITATIONS = (
    "one fixed benign stock supplier_audit in a fresh private office; no model, team repo or live company",
    "the report itself is not published; only canonical SHA-256 commitments are",
    "the planner never calls a tool on the received message; receipt is the stock inbox return only",
)
Rejected = resume_months.Rejected
_require = resume_months._require


def _canon(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _digest(value: Any) -> str | None:
    return None if value is None else resume_months._sha(_canon(value))


def _read_board(root: str) -> dict[str, tuple[str, bytes]]:
    out: dict[str, tuple[str, bytes]] = {}
    for leaf, digest in BOARD.items():
        rel = f"{BOARD_DIR}/{leaf}.py"
        path = os.path.join(root, rel)
        try:
            _require(os.path.realpath(path) == path, "not a plain path")
            with open(
                path, "rb", opener=lambda p, _: os.open(p, resume_months.OPEN_FLAGS)
            ) as handle:
                regular = stat.S_ISREG(os.fstat(handle.fileno()).st_mode)
                data = handle.read(resume_months.SOURCE_LIMIT + 1) if regular else b""
        except (OSError, AssertionError):
            raise Rejected(
                f"{rel} is not a readable plain file under BESPOKE_ROOT"
            ) from None
        if resume_months._sha(data) != digest:
            raise Rejected(f"{rel} does not match the pinned official bytes")
        out[leaf] = (path, data)
    return out


def _accept(
    argv: list[str],
) -> tuple[
    dict[str, tuple[str, bytes]], tuple[str, bytes], dict[str, tuple[str, bytes]]
]:
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        raise Rejected(USAGE)
    resume_months._accept_host()
    root = os.path.realpath(argv[0])
    if not os.path.isdir(root):
        raise Rejected("BESPOKE_ROOT is not a directory")
    prefix = resume_months.PACKAGE_DIR + "/"
    package = {
        rel: resume_months._read_pinned(root, rel)
        for rel in resume_months.PINNED
        if rel.startswith(prefix)
    }
    try:
        market = resume_stock_probe._read_pinned(root, resume_stock_probe.MARKET)
    except resume_stock_probe.Rejected:
        raise Rejected(
            f"{resume_stock_probe.MARKET} does not match the pinned official bytes"
        ) from None
    return package, market, _read_board(root)


def _load_board(sources: dict[str, tuple[str, bytes]]) -> ModuleType:
    _require(BOARD_PACKAGE not in sys.modules, "stock board loaded twice")
    package = ModuleType(BOARD_PACKAGE)
    package.__path__, package.__package__ = [], BOARD_PACKAGE
    sys.modules[BOARD_PACKAGE] = package
    for leaf, digest in BOARD.items():
        path, data = sources[leaf]
        _require(
            resume_months._sha(data) == digest, "board bytes changed after verification"
        )
        module = ModuleType(f"{BOARD_PACKAGE}.{leaf}")
        module.__file__, module.__package__ = path, BOARD_PACKAGE
        sys.modules[module.__name__] = module
        exec(compile(data, path, "exec", dont_inherit=True), module.__dict__)  # noqa: S102
    return sys.modules[f"{BOARD_PACKAGE}.core"]


class _OfficeTransport:
    def __init__(self, office: ModuleType, cfg: dict[str, Any]) -> None:
        self.office, self.cfg, self.calls = office, cfg, []

    def run(
        self,
        argv: list[str],
        *,
        input: str,
        capture_output: bool,
        text: bool,
        timeout: float,
        env: dict[str, str],
        cwd: str,
    ) -> subprocess.CompletedProcess[str]:
        cmd = argv[-1]
        launch = [self.cfg["python"], "-P", "-m", "marketsim.office", cmd]
        _require(argv == launch and cmd in OFFICE_COMMANDS, "unexpected office launch")
        _require(
            capture_output and text and cwd == self.cfg["state"],
            "unexpected office launch mode",
        )
        _require(
            env.get("PYTHONPATH") == self.cfg["pythonpath"],
            "unexpected office import path",
        )
        _require(
            json.loads(input).get("state") == self.cfg["state"],
            "payload is not for the private office",
        )
        out, err, saved = io.StringIO(), io.StringIO(), (sys.argv, sys.stdin)
        sys.argv, sys.stdin = ["marketsim.office", cmd], io.StringIO(input)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.office.main()
        finally:
            sys.argv, sys.stdin = saved
        self.calls.append(cmd)
        return subprocess.CompletedProcess(argv, 0, out.getvalue(), err.getvalue())


def _view(out: dict[str, Any]) -> dict[str, Any]:
    result = out.get("result")
    field = (
        "absent"
        if "result" not in out
        else "stock_refusal"
        if result == REFUSAL
        else ("report" if isinstance(result, dict) else "other")
    )
    return {
        "status": out.get("status"),
        "seconds_left": out.get("seconds_left"),
        "result_field": field,
    }


def _typed(report: Any) -> bool:
    if type(report) is not dict or set(report) != set(REPORT):
        return False
    if not all(
        isinstance(report[k], t) and type(report[k]) is not bool
        for k, t in REPORT.items()
    ):
        return False
    leads, fills = report["lead_times_days"], report["fill_rates"]
    return (
        report["product"] == TARGET
        and report["supplier"] == PARAMS["supplier"]
        and report["sample"] == 200
        and 0 <= report["defective"] <= 200
        and len(leads) == 8
        and len(fills) == 8
        and all(type(x) is int for x in leads)
        and all(type(x) is float and 0 <= x <= 1 for x in fills)
    )


def _event(record: dict[str, Any], roles: dict[str, str]) -> dict[str, Any]:
    out = {"event": record.get("event"), "role": roles.get(record.get("agent"))}
    out.update({k: record[k] for k in EVENT_FIELDS if k in record})
    return out


def _office_steps(
    board: Any, office: Any, tokens: dict[str, str], clock: Any
) -> dict[str, Any]:
    def call(role: str, fn: Any, *args: Any) -> Any:
        return fn(board.authenticate(tokens[role]), *args)

    quote = call("orderer", office.quote, KIND, TARGET, PARAMS)
    order = call("orderer", office.order, KIND, TARGET, PARAMS, "", "main")
    tid = order["test"]
    seen = {
        "planner_queued": _view(call("planner", office.result, tid)),
        "orderer_queued": _view(call("orderer", office.result, tid)),
    }
    office.tick()
    worker = next((t for t in threading.enumerate() if t.name == f"test-{tid}"), None)
    if worker is not None:
        worker.join(WORKER_JOIN_S)
    test = office.get(tid)
    _require(
        test["status"] == "running" and test["ends_at"] is not None,
        "stock office did not start the test",
    )
    computed = _digest(test.get("result"))
    clock.at = test["ends_at"] - 1.0
    office.tick()
    seen["orderer_running"] = _view(call("orderer", office.result, tid))
    seen["planner_running"] = _view(call("planner", office.result, tid))
    clock.at = test["ends_at"]
    office.tick()
    seen["planner_done"] = _view(call("planner", office.result, tid))
    released = call("orderer", office.result, tid)
    seen["orderer_done"] = _view(released)
    report = (
        released.get("result") if isinstance(released.get("result"), dict) else None
    )
    status = {role: call(role, office.status) for role in ROLES}
    return {
        "tid": tid,
        "quote": quote,
        "order": order,
        "seen": seen,
        "computed": computed,
        "report": report,
        "status": status,
        "budget": office.budget(),
        "call": call,
        "test": dict(test),
    }


def _handoff(
    board: Any, office: Any, state: Path, steps: dict[str, Any]
) -> dict[str, Any]:
    call, tid, report = steps["call"], steps["tid"], steps["report"]
    before = asyncio.run(call("planner", board.inbox, 0))
    text = _canon({"test": tid, "result": report}).decode()
    sent = call("orderer", board.send, text, ROLES["planner"])
    got = asyncio.run(call("planner", board.inbox, 0))
    echo = asyncio.run(call("orderer", board.inbox, 0))
    after = _view(call("planner", office.result, tid))
    message = got[0] if len(got) == 1 else {}
    try:
        envelope = json.loads(message.get("text") or "null")
    except ValueError:
        envelope = None
    envelope = envelope if isinstance(envelope, dict) else {}
    disk, _ = resume_clock._read("office.json", str(state / "office.json"))
    stored = next((t for t in disk.get("tests", []) if t.get("id") == tid), {})
    return {
        "before": before,
        "text": text,
        "sent": sent,
        "got": got,
        "message": message,
        "echo": echo,
        "after": after,
        "envelope": envelope,
        "disk": disk,
        "stored": stored,
    }


def _checks(
    steps: dict[str, Any],
    hand: dict[str, Any],
    hashes: dict[str, Any],
    calls: list[str],
) -> dict[str, bool]:
    seen, tid, status, stored, message = (
        steps["seen"],
        steps["tid"],
        steps["status"],
        hand["stored"],
        hand["message"],
    )
    entry = next((e for e in status["planner"]["ledger"] if e["id"] == tid), {})
    cost = steps["quote"]["cost_usd"]
    planner = ("planner_queued", "planner_running", "planner_done")
    return {
        "report_typed": _typed(steps["report"]),
        "same_report_at_every_boundary": None not in hashes.values()
        and len(set(hashes.values())) == 1,
        "owner_pending_while_queued": seen["orderer_queued"]
        == {"status": "queued", "seconds_left": None, "result_field": "absent"},
        "owner_pending_while_computed": steps["computed"] is not None
        and seen["orderer_running"]["status"] == "running"
        and seen["orderer_running"]["result_field"] == "absent",
        "owner_report_after_ready": seen["orderer_done"]["status"] == "done"
        and seen["orderer_done"]["result_field"] == "report",
        "peer_refused_before_and_after_ready": all(
            seen[k]["result_field"] == "stock_refusal" for k in planner
        ),
        "peer_refused_after_board_receipt": hand["after"]["result_field"]
        == "stock_refusal",
        "ledger_owner_is_orderer": entry.get("agent") == ROLES["orderer"]
        and stored.get("agent") == ROLES["orderer"],
        "peer_owns_no_test": status["planner"]["mine"] == []
        and [m["id"] for m in status["orderer"]["mine"]] == [tid],
        "task_matches_request": (
            stored.get("kind"),
            stored.get("target"),
            stored.get("params"),
        )
        == (KIND, TARGET, PARAMS)
        and stored.get("status") == "done",
        "fee_matches_stock_quote": cost
        == steps["order"]["cost_usd"]
        == entry.get("cost_usd")
        == stored.get("cost_usd")
        == status["orderer"]["committed_usd"]
        and status["orderer"]["left_usd"] == round(steps["budget"] - cost, 2),
        "inbox_empty_before_send": hand["before"] == [],
        "inbox_returned_sent_record": len(hand["got"]) == 1
        and message.get("seq") == hand["sent"]["seq"]
        and message.get("from") == ROLES["orderer"]
        and message.get("to") == ROLES["planner"]
        and message.get("text") == hand["text"],
        "envelope_names_office_test": hand["envelope"].get("test") == tid,
        "sender_inbox_excludes_own_send": hand["echo"] == [],
        "only_pinned_office_commands": set(calls) <= set(OFFICE_COMMANDS)
        and "run" in calls,
    }


def _session(
    core: ModuleType,
    market: dict[str, Any],
    cfg: dict[str, Any],
    transport: _OfficeTransport,
    state: Path,
) -> dict[str, Any]:
    board = core.Board(":memory:")
    events: list[dict[str, Any]] = []
    roles = {name: role for role, name in ROLES.items()}
    board.subscribe(lambda record: events.append(_event(record, roles)))
    tokens = {
        role: board.register_agent(name, role=role) for role, name in ROLES.items()
    }
    clock = resume_stock_probe.FixedClock(EPOCH)
    office = market["MarketOffice"](board, cfg, now=clock)
    _require(
        office.live is None and not office.midrun_at,
        "private office must have no live company or shock",
    )
    steps = _office_steps(board, office, tokens, clock)
    hand = _handoff(board, office, state, steps)
    hashes = {
        "office_memory_when_computed": steps["computed"],
        "office_json_snapshot": _digest(hand["stored"].get("result")),
        "orderer_test_result_release": _digest(steps["report"]),
        "board_send_text": _digest(json.loads(hand["text"])["result"]),
        "planner_inbox_return": _digest(hand["envelope"].get("result")),
    }
    checks = _checks(steps, hand, hashes, transport.calls)
    test = steps["test"]
    return {
        "verdict": "handoff_observed"
        if all(checks.values())
        else "rejected_inconsistent_provenance",
        "checks": checks,
        "report_sha256": hashes,
        "observations": dict(steps["seen"], planner_after_board_receipt=hand["after"]),
        "refusal_text": REFUSAL,
        "task": {
            "kind": KIND,
            "target": TARGET,
            "params": PARAMS,
            "facility": steps["quote"]["facility"],
            "minutes": steps["quote"]["minutes"],
            "cost_usd": steps["quote"]["cost_usd"],
            "research_budget_usd": steps["budget"],
            "committed_usd": steps["status"]["orderer"]["committed_usd"],
            "left_usd": steps["status"]["orderer"]["left_usd"],
        },
        "clock": {
            "epoch": EPOCH,
            "time_scale": cfg["time_scale"],
            "ordered_offset_s": test["ordered_at"] - EPOCH,
            "started_offset_s": test["started_at"] - EPOCH,
            "ready_offset_s": test["ends_at"] - EPOCH,
        },
        "snapshot": {
            "office_test_record": steps["tid"],
            "office_json_tests": len(hand["disk"].get("tests", [])),
            "office_json_canonical_sha256": _digest(hand["disk"]),
            "board_send_seq": hand["sent"]["seq"],
            "planner_inbox_seqs": [m["seq"] for m in hand["got"]],
        },
        "office_commands": list(transport.calls),
        "events": events,
        "secret_commitment": resume_months._sha(SECRET.encode()),
    }


def _observe(
    package: dict[str, tuple[str, bytes]],
    market_src: tuple[str, bytes],
    board_src: dict[str, tuple[str, bytes]],
) -> dict[str, Any]:
    core = _load_board(board_src)
    market = resume_stock_probe._exec_stock(resume_stock_probe.MARKET, *market_src)
    os.environ[resume_months.ENV_PREFIX + "TEAM_PYTHON"] = sys.executable
    with tempfile.TemporaryDirectory(prefix="office-handoff-") as raw:
        base = Path(raw)
        _require(stat.S_IMODE(base.stat().st_mode) == 0o700, "temp root not private")
        staged = resume_months._stage_package(base, package)
        resume_months._import_stock(staged, package)
        office_module = importlib.import_module(f"{resume_months.PACKAGE}.office")
        _require(
            isinstance(office_module.__loader__, resume_months._PinnedPackage),
            "office is not pinned",
        )
        state = base / "state"
        state.mkdir(mode=0o700)
        resume_months._write_new(
            state / "secret.json", json.dumps({"secret": SECRET}).encode()
        )
        cfg = dict(
            market["DEFAULTS"],
            python=sys.executable,
            pythonpath=str(staged.parent),
            state=str(state),
            repo=str(base / "absent-repo"),
        )
        transport = _OfficeTransport(office_module, cfg)
        market["subprocess"] = transport
        observed = _session(core, market, cfg, transport, state)
        observed["staged_sources_intact"] = resume_months._intact(staged)
    if not observed["staged_sources_intact"]:
        observed["verdict"] = "rejected_inconsistent_provenance"
    return observed


def main(argv: list[str] | None = None) -> int:
    try:
        package, market, board = _accept(sys.argv[1:] if argv is None else argv)
        deps = resume_months._dependencies()
    except Rejected as e:
        sys.stderr.write(
            json.dumps({"error": str(e), "schema": SCHEMA}, sort_keys=True) + "\n"
        )
        return 2
    result = _observe(package, market, board)
    result.update(
        schema=SCHEMA,
        roles=sorted(ROLES),
        source={
            "commit": resume_clock.SOURCE_COMMIT,
            "marketsim": dict(resume_months.MODULES),
            "market_plugin": resume_stock_probe.PINNED[resume_stock_probe.MARKET],
            "board": dict(BOARD),
            "loading": "verified_bytes_compile_exec_no_pyc",
        },
        interpreter={"version": sys.version.split()[0], **deps},
        transport=TRANSPORT,
        unobservable=UNOBSERVABLE,
        limitations=list(LIMITATIONS),
    )
    sys.stdout.write(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    return 0 if result["verdict"] == "handoff_observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
