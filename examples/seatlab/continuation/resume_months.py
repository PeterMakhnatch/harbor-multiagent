from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
import stat
import sys
import sysconfig
import tarfile
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from . import resume_clock

SCHEMA = "seatlab.continuation-resume-months/v1"
PROG = "python -B -m examples.seatlab.continuation.resume_months"
SOURCE_COMMIT = resume_clock.SOURCE_COMMIT
TASK = "tasks/seed-to-profit/task"
PACKAGE = "marketsim"
PACKAGE_DIR = f"{TASK}/tests/hidden/marketsim/marketsim"
HUB = f"{TASK}/environment/hub/make_hub_plugin.py"
STARTER_OPS = f"{TASK}/environment/app/ops/autopilot.py"
REFERENCE_OPS = f"{TASK}/solution/reference/ops/autopilot.py"
REFERENCE_PLAN = f"{TASK}/solution/reference/company/plan.yaml"
MODULES = {
    "__init__": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "catalog": "42ca148286261e04f1ce3e72fc9dee3628917ca4ecd9c1e6a2d01c8cc363dcea",
    "countries": "92585e8a4f7b9249a75346fc359a42b60508740eb3f0acce34f23cd8969228f6",
    "live": "4253328d5e0a384a692b3110ed2b56aeedfa0259579352ce412a2dc94def29cd",
    "office": "f12c866eb3214e3941e87c6cc7791d58feebb819a245beae463a177e469acc88",
    "ops_runner": "91cbae9f4b7fb796455d463e6919a27f868b95ded1b5a481359d5d67b71bffe0",
    "plan": "29ad41502f3c77b5cca419bb8d8223c5406a2bcb4fa0b2adecf4a4f18b4019dc",
    "policies": "060ae4ad21e62dcea021cd5a7150c45a98c7460a79d1089b3d6257f3a183abf9",
    "rivals": "32415d7c7d65275051deaf9074fcf65dcd999d80a709e82b6f76c99a674dee51",
    "runner": "031d659748998897b69c3a24113ee3e51dbe675795198fe1751ee825815c445d",
    "scoring": "d1222e397054459a7d7bd1da53305c857262cdf172e4a820e45155143a52a696",
    "sim": "df371acafc32d0c309f5925e6eb88bc7441690feb6f41b121dd897b26460f4c2",
    "tricks": "888eb4e78995e959364d2cd7853ce7cee7225b6aebea3ca119ec7ab0272fab64",
    "truth": "dde46d41ff1034b878f7384b1415dd514f0bb5e198349accd82bfd93512547f1",
    "worlds": "b6e4a0377052ab177c85222af392ca0022e13ec8321230fc805d21cee34d3881",
}
PINNED = {f"{PACKAGE_DIR}/{name}.py": digest for name, digest in MODULES.items()} | {
    HUB: "90965ec216383b56645c898f3fc42de0f562f04ee52872f27de4f922430035b5",
    STARTER_OPS: "3b781d2a8e69b3dcaffc9897903e34a5692af952f8a50d0fc91b37256bd04dde",
    REFERENCE_OPS: "9346bdb8ab26712af9669a336e58b6939327460cfac49d9b675e78a5efb525b3",
    REFERENCE_PLAN: "6cfacdd500434d7f66cdd4795ff90eeab5eda59b3d6a660dc38fedaebc0179e4",
}
PROGRAMS = {"starter": STARTER_OPS, "reference": REFERENCE_OPS}
DELAYS = (0, 1, 2, 4, 8, 16, 24)
CASES = tuple(range(8))
MONTHS = 37
SWITCH_AFTER = 13
SEED_LABEL = "seatlab/resume-landing/v1/{index}/{purpose}"
HUB_LIVE = {
    "launch_sec": 3600.0,
    "month_sec": 283.78,
    "months": MONTHS,
    "change_day": 540,
    "shape": "mnl",
}
RUN_SEC = 14400.0
RESEARCH_SPENT = 0.0
REFERENCE_STATE = "reference-history.json"
SNAPSHOT_MEMBERS = ["company", "company/plan.yaml", "ops", "ops/autopilot.py"]
PYTHON = (3, 12)
DEPENDENCIES = {"numpy": "2.1.3", "yaml": "6.0.2"}
REJECTED_ENV = ("PYTHONPATH", "PYTHONHOME")
ENV_PREFIX = "SEEDPROFIT_"
SOURCE_LIMIT = 1 << 20
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
CREATE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
)
LIMITATIONS = (
    "months advance by calling stock live.month in sequence; the market office wall clock and hub are not run",
    "landing delay is counted in live months; no outage, checkpoint, or restart is simulated",
    "the program folder is a plain folder (the stock local-check path), so stock log commits are null; "
    "the executed program is read from the stock per-month snapshot tar",
    "live_score is stock scoring.world_score on stock live.result with research spend 0; the grader's "
    "replay minimum, playbook half, office research ledger, and forecast are not run",
    "not root: stock runner makes no separate user, and its seccomp filter is Linux only, so the two official "
    "programs run unsandboxed on macOS",
    "numpy and pyyaml are pinned by version, not by file bytes",
    "the stock software limits are wall clock (60 s per month, 20 s per call); host load can freeze a month, "
    "and software_frozen is reported per month",
)


class Rejected(Exception):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _module_name(name: str) -> str:
    return PACKAGE if name == "__init__" else f"{PACKAGE}.{name}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, allow_abbrev=False)
    parser.add_argument("bespoke_root", metavar="BESPOKE_ROOT")
    parser.add_argument("--delay", type=int, choices=DELAYS, required=True)
    parser.add_argument("--fixed", action="store_true")
    parser.add_argument("--case", type=int, choices=CASES, default=0)
    return parser


def _read_pinned(root: str, rel: str) -> tuple[str, bytes]:
    path = os.path.join(root, rel)
    if os.path.realpath(path) != path:
        raise Rejected(f"{rel} is not a plain path under BESPOKE_ROOT")
    try:
        fd = os.open(path, OPEN_FLAGS)
    except OSError:
        raise Rejected(f"{rel} cannot be opened") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > SOURCE_LIMIT:
            raise Rejected(f"{rel} is not a regular file within the size limit")
        data = os.read(fd, SOURCE_LIMIT + 1)
    except OSError:
        raise Rejected(f"{rel} cannot be read") from None
    finally:
        os.close(fd)
    if len(data) > SOURCE_LIMIT or _sha(data) != PINNED[rel]:
        raise Rejected(f"{rel} does not match the pinned official bytes")
    return path, data


def _accept_host() -> None:
    if os.geteuid() == 0:
        raise Rejected("refusing to run with effective uid 0")
    if not sys.flags.dont_write_bytecode:
        raise Rejected("run with python -B")
    if sys.version_info[:2] != PYTHON:
        raise Rejected("python 3.12 is required")
    if not sys.executable or not os.path.isfile(sys.executable):
        raise Rejected("sys.executable is not a file")
    if any(key.startswith(ENV_PREFIX) for key in os.environ):
        raise Rejected(f"{ENV_PREFIX}* environment variables are not accepted")
    if any(key in os.environ for key in REJECTED_ENV):
        raise Rejected("PYTHONPATH and PYTHONHOME are not accepted")
    if PACKAGE in sys.modules:
        raise Rejected(f"{PACKAGE} is already imported")


def _dependencies() -> dict[str, str]:
    paths = sysconfig.get_paths()
    roots = {Path(paths[key]).resolve() for key in ("purelib", "platlib")}
    found: dict[str, str] = {}
    for name, version in DEPENDENCIES.items():
        try:
            module = importlib.import_module(name)
        except ImportError:
            raise Rejected(f"{name}=={version} is not importable") from None
        where = Path(module.__file__ or "").resolve()
        installed = any(where.is_relative_to(root) for root in roots)
        if getattr(module, "__version__", None) != version or not installed:
            raise Rejected(
                f"{name}=={version} from this interpreter's site-packages is required"
            )
        found[name] = version
    return found


def _accept(argv: list[str]) -> tuple[argparse.Namespace, dict[str, tuple[str, bytes]]]:
    args = _parser().parse_args(argv)
    _accept_host()
    root = os.path.realpath(args.bespoke_root)
    if not os.path.isdir(root):
        raise Rejected("BESPOKE_ROOT is not a directory")
    return args, {rel: _read_pinned(root, rel) for rel in PINNED}


class _PinnedPackage(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, staged: Path, sources: dict[str, bytes]) -> None:
        self.staged = staged
        self.sources = sources

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != PACKAGE and not fullname.startswith(PACKAGE + "."):
            return None
        if fullname not in self.sources:
            raise ImportError(f"{fullname} is not a pinned stock module")
        package = fullname == PACKAGE
        leaf = "__init__" if package else fullname.rpartition(".")[2]
        return importlib.util.spec_from_file_location(
            fullname,
            str(self.staged / f"{leaf}.py"),
            loader=self,
            submodule_search_locations=[str(self.staged)] if package else None,
        )

    def create_module(self, spec: Any) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        spec = module.__spec__
        _require(
            spec is not None and spec.origin is not None, "stock module has no origin"
        )
        data = self.sources[spec.name]
        leaf = Path(spec.origin).stem
        _require(_sha(data) == MODULES[leaf], "stock bytes changed after verification")
        exec(compile(data, spec.origin, "exec", dont_inherit=True), module.__dict__)  # noqa: S102


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, CREATE_FLAGS, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def _stage_package(base: Path, sources: dict[str, tuple[str, bytes]]) -> Path:
    staged = base / "stock" / PACKAGE
    staged.mkdir(parents=True, mode=0o700)
    for name in MODULES:
        _write_new(staged / f"{name}.py", sources[f"{PACKAGE_DIR}/{name}.py"][1])
    return staged


def _stage_repo(base: Path, sources: dict[str, tuple[str, bytes]]) -> Path:
    repo = base / "program"
    (repo / "company").mkdir(parents=True, mode=0o700)
    (repo / "ops").mkdir(mode=0o700)
    _write_new(repo / "company" / "plan.yaml", sources[REFERENCE_PLAN][1])
    return repo


def _place_program(
    repo: Path, label: str, sources: dict[str, tuple[str, bytes]]
) -> None:
    pending = repo / ".autopilot.next"
    _write_new(pending, sources[PROGRAMS[label]][1])
    os.replace(pending, repo / "ops" / "autopilot.py")
    _require(
        sorted(os.listdir(repo)) == ["company", "ops"],
        "program folder has extra entries",
    )
    _require(
        sorted(os.listdir(repo / "company")) == ["plan.yaml"],
        "company/ has extra entries",
    )
    _require(
        sorted(os.listdir(repo / "ops")) == ["autopilot.py"], "ops/ has extra entries"
    )
    placed = (repo / "ops" / "autopilot.py").read_bytes()
    _require(
        _sha(placed) == PINNED[PROGRAMS[label]],
        "placed program is not the official bytes",
    )
    plan = (repo / "company" / "plan.yaml").read_bytes()
    _require(
        _sha(plan) == PINNED[REFERENCE_PLAN], "placed plan is not the official bytes"
    )


def _exec_hub(sources: dict[str, tuple[str, bytes]]) -> dict[str, Any]:
    path, data = sources[HUB]
    _require(_sha(data) == PINNED[HUB], "hub bytes changed after verification")
    namespace: dict[str, Any] = {
        "__name__": "stock_make_hub_plugin",
        "__file__": path,
        "__builtins__": builtins,
    }
    exec(compile(data, path, "exec", dont_inherit=True), namespace)  # noqa: S102
    live, run_sec = namespace["LIVE"], namespace["RUN_SEC"]
    _require(
        type(live) is dict and dict(live) == HUB_LIVE,
        "stock LIVE is not the released clock",
    )
    _require(
        run_sec == RUN_SEC and namespace["MONTHS"] == MONTHS,
        "stock run is not 4 h of 37 months",
    )
    return dict(live)


def _import_stock(
    staged: Path, sources: dict[str, tuple[str, bytes]]
) -> tuple[Any, Any]:
    _require(PACKAGE not in sys.modules, "marketsim imported before pinning")
    finder = _PinnedPackage(
        staged,
        {
            _module_name(name): sources[f"{PACKAGE_DIR}/{name}.py"][1]
            for name in MODULES
        },
    )
    sys.meta_path.insert(0, finder)
    live = importlib.import_module(f"{PACKAGE}.live")
    scoring = importlib.import_module(f"{PACKAGE}.scoring")
    runner = sys.modules[f"{PACKAGE}.runner"]
    loaded = sorted(
        name
        for name in sys.modules
        if name == PACKAGE or name.startswith(PACKAGE + ".")
    )
    _require(
        all(sys.modules[name].__loader__ is finder for name in loaded),
        "unpinned marketsim module",
    )
    _require(
        runner.HERE == str(staged), "stock runner does not copy the staged ops_runner"
    )
    _require(
        runner.TEAM_PYTHON == sys.executable,
        "stock runner interpreter is not sys.executable",
    )
    _require(
        live.STEP_DAYS == 30 and live.CHANGE_DAY == HUB_LIVE["change_day"],
        "stock live changed",
    )
    return live, scoring


def _world(case: int) -> dict[str, Any]:
    labels = {p: SEED_LABEL.format(index=case, purpose=p) for p in ("world", "truth")}
    return {
        "case": case,
        "shape": HUB_LIVE["shape"],
        "change_day": HUB_LIVE["change_day"],
        "world_label": labels["world"],
        "truth_label": labels["truth"],
        "world_seed": _sha(labels["world"].encode()),
        "truth_secret": _sha(labels["truth"].encode()),
    }


def _planned(fixed: bool, delay: int) -> list[str]:
    landing = 0 if fixed else SWITCH_AFTER + delay
    return ["reference" if month >= landing else "starter" for month in range(MONTHS)]


def _schedule(fixed: bool, delay: int, planned: list[str]) -> dict[str, Any]:
    first = planned.index("reference") if "reference" in planned else None
    return {
        "mode": "fixed" if fixed else "switch",
        "delay_argument": delay,
        "delay_applied": not fixed,
        "months_planned": MONTHS,
        "switch_scheduled_after_months": None if fixed else SWITCH_AFTER,
        "first_reference_month_planned": first,
        "landing_within_run": first is not None,
        "planned_months": {label: planned.count(label) for label in PROGRAMS},
        "planned_programs": planned,
    }


def _executed(
    state: Path, k: int, planned: str, entry: dict[str, Any]
) -> dict[str, Any]:
    _require(entry["month"] == k, "stock log month does not match the loop")
    with tarfile.open(state / "live" / "snapshots" / f"m{k:02d}.tar", "r:") as tf:
        members = {m.name: m for m in tf.getmembers()}
        _require(
            sorted(members) == SNAPSHOT_MEMBERS,
            "stock snapshot holds unexpected members",
        )
        files = [n for n in SNAPSHOT_MEMBERS if "/" in n]
        _require(
            all(members[n].isdir() for n in SNAPSHOT_MEMBERS if n not in files),
            "snapshot dir type",
        )
        _require(all(members[n].isfile() for n in files), "snapshot file type")
        blobs = {n: tf.extractfile(members[n]).read(SOURCE_LIMIT + 1) for n in files}
    ops_sha = _sha(blobs["ops/autopilot.py"])
    executed = next(
        (label for label, rel in PROGRAMS.items() if PINNED[rel] == ops_sha), "unknown"
    )
    kept = state / "live" / "software-state" / REFERENCE_STATE
    return {
        "month": k,
        "first_day": entry["first_day"],
        "last_day": entry["last_day"],
        "planned": planned,
        "executed": executed,
        "ops_sha256": ops_sha,
        "plan_sha256": _sha(blobs["company/plan.yaml"]),
        "commit": entry["commit"],
        "software_frozen": entry["software_frozen"],
        "notes": entry["notes"],
        "value": entry["value"],
        "bankrupt": entry["bankrupt"],
        "reference_state_kept": kept.is_file(),
    }


def _run_months(
    live: Any,
    state: Path,
    repo: Path,
    payload: dict[str, str],
    sources: dict[str, tuple[str, bytes]],
    planned: list[str],
) -> tuple[list[dict[str, Any]], str | None]:
    rows: list[dict[str, Any]] = []
    try:
        _require(
            live.status(str(state))["started"] is False,
            "stock live company already started",
        )
        for k in range(MONTHS):
            _place_program(repo, planned[k], sources)
            _require(os.geteuid() != 0, "effective uid became 0")
            out = live.month(dict(payload))
            _require(
                out["months"] == k + 1, "stock live_month did not log exactly one month"
            )
            rows.append(_executed(state, k, planned[k], out["log"][k]))
            if out["ended"]:
                break
    except Exception as e:  # noqa: BLE001
        return rows, f"{type(e).__name__}: {e}"
    return rows, None


def _observed(
    rows: list[dict[str, Any]], status: dict[str, Any], error: str | None
) -> dict[str, Any]:
    executed = [r["executed"] for r in rows]
    first = executed.index("reference") if "reference" in executed else None
    switches = sum(1 for a, b in zip(executed, executed[1:]) if a != b)
    state_ok = all(
        (r["executed"] != "starter" or not r["reference_state_kept"])
        and (
            r["executed"] != "reference"
            or r["software_frozen"]
            or r["reference_state_kept"]
        )
        for r in rows
    )
    bankrupt = bool(status.get("bankrupt"))
    ended = bool(status.get("ended"))
    return {
        "months_run": len(rows),
        "ended": ended,
        "bankrupt": bankrupt,
        "completed": error is None and ended and (len(rows) == MONTHS or bankrupt),
        "months": {label: executed.count(label) for label in PROGRAMS},
        "unknown_program_months": executed.count("unknown"),
        "first_reference_month": first,
        "program_changes": switches,
        "switch_observed": first is not None
        and first > 0
        and executed[first - 1] == "starter",
        "programs_match_schedule": all(r["executed"] == r["planned"] for r in rows),
        "plan_is_reference_every_month": all(
            r["plan_sha256"] == PINNED[REFERENCE_PLAN] for r in rows
        ),
        "reference_state_consistent": state_ok,
        "software_frozen_months": [r["month"] for r in rows if r["software_frozen"]],
        "commits_available": any(r["commit"] for r in rows),
    }


def _score(live: Any, scoring: Any, state: Path) -> dict[str, Any] | None:
    result = live.result(str(state))
    company = live.load(str(state))
    if result is None or company is None:
        return None
    bankrupt = bool(result["bankrupt"])
    raw = company.value() if company.bankrupt_day is None else {"value": 0.0}
    return {
        "live_score": float(scoring.world_score(result, RESEARCH_SPENT)),
        "multiple": 0.0
        if bankrupt
        else float(scoring.multiple(result["value"], RESEARCH_SPENT)),
        "value": float(result["value"]),
        "value_unrounded": float(raw["value"]),
        "value_parts_unrounded": {k: float(v) for k, v in raw.items()},
        "bankrupt": bankrupt,
        "bankrupt_day": result["bankrupt_day"],
        "bankrupt_reason": result["bankrupt_reason"],
        "days": int(result["days"]),
        "research_spent": RESEARCH_SPENT,
        "seed_money": float(scoring.RULES["seed_money"]),
        "top_multiple": float(scoring.TOP),
        "years": result["years"],
        "company_months": result["months"],
    }


def _denominator() -> dict[str, Any]:
    cells = [f"case{c}/switch/delay{d}" for c in CASES for d in DELAYS]
    cells += [f"case{c}/fixed" for c in CASES]
    return {"cells": len(cells), "sha256": _sha(("\n".join(cells) + "\n").encode())}


def _source() -> dict[str, Any]:
    manifest = json.dumps(PINNED, sort_keys=True, separators=(",", ":")).encode()
    return {
        "commit": SOURCE_COMMIT,
        "sha256": dict(PINNED),
        "manifest_sha256": _sha(manifest),
        "runner_sha256": _sha(Path(__file__).read_bytes()),
        "loading": "verified_bytes_staged_in_temp_compile_exec_no_pyc",
    }


def _intact(staged: Path) -> bool:
    return all(
        _sha((staged / f"{name}.py").read_bytes()) == digest
        for name, digest in MODULES.items()
    )


def _execute(
    base: Path,
    sources: dict[str, tuple[str, bytes]],
    live_json: dict[str, Any],
    payload: dict[str, str],
    planned: list[str],
) -> dict[str, Any]:
    staged = _stage_package(base, sources)
    repo = _stage_repo(base, sources)
    state = base / "state"
    (state / "live").mkdir(parents=True, mode=0o700)
    _write_new(
        state / "live" / "live.json", json.dumps(live_json, sort_keys=True).encode()
    )
    work = base / "tmp"
    work.mkdir(mode=0o700)
    live, scoring = _import_stock(staged, sources)
    previous = tempfile.tempdir
    tempfile.tempdir = str(work)
    try:
        month_payload = dict(payload, state=str(state), repo=str(repo))
        rows, error = _run_months(live, state, repo, month_payload, sources, planned)
        status = live.status(str(state))
        score = _score(live, scoring, state)
    finally:
        tempfile.tempdir = previous
    return {
        "rows": rows,
        "error": error,
        "status": status,
        "score": score,
        "staged_sources_intact": _intact(staged),
        "stock_temp_leftovers": len(os.listdir(work)),
    }


def run(
    args: argparse.Namespace,
    sources: dict[str, tuple[str, bytes]],
    deps: dict[str, str],
) -> dict[str, Any]:
    hub_live = _exec_hub(sources)
    os.environ[ENV_PREFIX + "TEAM_PYTHON"] = sys.executable
    world = _world(args.case)
    planned = _planned(args.fixed, args.delay)
    payload = {"secret": world["truth_secret"], "ref": "main"}
    live_json = dict(hub_live, world_seed=world["world_seed"])
    with tempfile.TemporaryDirectory(prefix="resume-months-") as raw:
        base = Path(raw)
        _require(
            base.is_absolute() and stat.S_IMODE(base.stat().st_mode) == 0o700,
            "temp root not private",
        )
        done = _execute(base, sources, live_json, payload, planned)
    observed = _observed(done["rows"], done["status"], done["error"])
    cell = (
        f"case{args.case}/fixed"
        if args.fixed
        else f"case{args.case}/switch/delay{args.delay}"
    )
    return {
        "schema": SCHEMA,
        "cell": cell,
        "denominator": _denominator(),
        "source": _source(),
        "interpreter": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            **deps,
        },
        "world": world,
        "live_json": live_json,
        "delay": args.delay,
        "schedule": _schedule(args.fixed, args.delay, planned),
        "observed": observed,
        "months": done["rows"],
        "score": done["score"],
        "error": done["error"],
        "staged_sources_intact": done["staged_sources_intact"],
        "stock_temp_leftovers": done["stock_temp_leftovers"],
        "limitations": list(LIMITATIONS),
    }


def _valid(result: dict[str, Any]) -> bool:
    observed = result["observed"]
    expected = result["schedule"]["first_reference_month_planned"]
    landed = observed["first_reference_month"] == expected or (
        observed["bankrupt"]
        and (expected is None or expected >= observed["months_run"])
    )
    return (
        result["error"] is None
        and observed["completed"]
        and observed["programs_match_schedule"]
        and observed["plan_is_reference_every_month"]
        and observed["reference_state_consistent"]
        and observed["unknown_program_months"] == 0
        and landed
        and result["staged_sources_intact"]
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args, sources = _accept(sys.argv[1:] if argv is None else argv)
        deps = _dependencies()
    except Rejected as e:
        sys.stderr.write(
            json.dumps({"error": str(e), "schema": SCHEMA}, sort_keys=True) + "\n"
        )
        return 2
    result = run(args, sources, deps)
    result["valid"] = _valid(result)
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
