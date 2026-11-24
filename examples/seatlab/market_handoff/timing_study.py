from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import simulator, timing_world

ROOT = Path(__file__).resolve().parents[3]
ROLES = ("finance_a", "finance_b")
STUDY = "seatlab.price-timing/v1"


def assignment(world: int, repeat: int) -> str:
    if (
        type(world) is not int
        or not 0 <= world <= 8
        or type(repeat) is not int
        or repeat not in (0, 1)
    ):
        raise ValueError("invalid registered cell")
    key = f"seatlab/price-timing/v1:{world}:assignment".encode("ascii")
    return ROLES[(hashlib.sha256(key).digest()[0] & 1) ^ repeat]


def cell(root: Path, world: int, repeat: int) -> Path:
    root = root.resolve(strict=True)
    if (
        not root.is_dir()
        or root.stat().st_mode & 0o077
        or root.stat().st_uid != os.getuid()
    ):
        raise ValueError("use an owner-only directory outside the repository")
    if root == ROOT or ROOT in root.parents:
        raise ValueError(
            "store native inputs and subscription receipts outside the repository"
        )
    return root / f"w{world:02d}-r{repeat}"


def store(path: Path, record: dict[str, Any]) -> None:
    with path.open("xb") as handle:
        handle.write(simulator.encode(record))
        handle.flush()
        os.fsync(handle.fileno())


def load(path: Path) -> dict[str, Any]:
    value = json.loads(simulator.read_own(path))
    if not isinstance(value, dict):
        raise TypeError(f"expected an object: {path.name}")
    return value


def base(world: int, repeat: int, early: str) -> dict[str, Any]:
    return {
        "schema": STUDY,
        "world": world,
        "repeat": repeat,
        "early": early,
        "control": world == timing_world.CONTROL_WORLD,
        "status": "incomplete",
        "picks": {},
        "turns": [],
        "native_id": None,
        "protocol_reward": None,
    }


OWNER_PURPOSES = {
    "sales": ("sales",),
    "finance_a": ("finance-price", "finance-receipt"),
    "finance_b": ("finance-price", "finance-receipt"),
}
OWNER_FACTS = (
    "session_sha256",
    "client_tokens",
    "prompt_sha256",
    "answer_sha256",
    "requested_model",
    "served_model",
    "tool_steps",
    "credit_fallback",
)


def owner_blank(seat: str, purpose: str, diagnostic: str | None) -> dict[str, Any]:
    return {
        "seat": seat,
        "purpose": purpose,
        "admission": None,
        "admitted": False,
        "request_recorded": False,
        "reply_recorded": False,
        "verified": False,
        **dict.fromkeys(OWNER_FACTS),
        "diagnostic": diagnostic,
    }


def owner_entry(turn: Path, seat: str, purpose: str) -> dict[str, Any]:
    from ..visibility import calendar_packet as packet
    from ..wait_peer import receipt as owner_receipt
    from . import timing_seat

    note = timing_seat.admission(turn)
    record = turn / purpose
    entry = owner_blank(seat, purpose, None)
    entry["admission"] = (
        None
        if note is None
        else {key: note[key] for key in ("outcome", "stage", "cause")}
    )
    entry["admitted"] = note is not None and (
        note["outcome"],
        note["stage"],
        note["cause"],
    ) == ("passed", "complete", "none")
    entry["request_recorded"] = os.path.lexists(record / "request.json")
    entry["reply_recorded"] = os.path.lexists(record / "reply.json")
    if not entry["request_recorded"]:
        entry["diagnostic"] = (
            "admission_missing"
            if note is None
            else "no_request"
            if entry["admitted"]
            else "not_admitted"
        )
        return entry
    if not entry["admitted"]:
        entry["diagnostic"] = "request_without_admission"
        return entry
    try:
        request = packet._json(owner_receipt.read(record / "request.json"))
        if type(request) is not dict or request.keys() != {"schema", "prompt"}:
            raise ValueError("owner_request_shape")
        if not isinstance(request["prompt"], str):
            raise TypeError("owner_request_prompt")
        owned = owner_receipt.owner_turn(turn, purpose, request["prompt"], None)
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        entry["diagnostic"] = "reply_unverified"
        return entry
    if owned["status"] != "answered_unused":
        entry["diagnostic"] = "unanswered"
        return entry
    receipt = owned["receipt"]
    entry.update(
        verified=True,
        session_sha256=receipt["session_sha256"],
        client_tokens=receipt["usage"]["total_tokens"],
        prompt_sha256=receipt["prompt_sha256"],
        answer_sha256=receipt["answer_sha256"],
        requested_model=receipt["requested_model"],
        served_model=receipt["server_model"],
        tool_steps=receipt["observed_tool_steps"],
        credit_fallback=receipt["ai_credit_fallback"],
    )
    return entry


def partial_usage(native: Path) -> list[dict[str, Any]]:
    try:
        participants = load(native / "config.json").get("participants")
        base_dir = native.resolve(strict=True)
    except (OSError, ValueError, TypeError, simulator.SimulatorError):
        return []
    if type(participants) is not list:
        return []
    usage: list[dict[str, Any]] = []
    seen: set[str] = set()
    for participant in participants[: len(OWNER_PURPOSES)]:
        kwargs = participant.get("kwargs") if isinstance(participant, dict) else None
        seat = participant.get("seat") if isinstance(participant, dict) else None
        owner = kwargs.get("owner") if isinstance(kwargs, dict) else None
        if seat not in OWNER_PURPOSES or seat in seen or type(owner) is not str:
            continue
        seen.add(seat)
        root = Path(owner)
        if (
            not root.is_absolute()
            or ".." in root.parts
            or base_dir not in root.parents
            or root.is_symlink()
            or not root.is_dir()
        ):
            continue
        for purpose in OWNER_PURPOSES[seat]:
            turn = root / purpose
            if not os.path.lexists(turn):
                continue
            if turn.is_symlink() or not turn.is_dir():
                usage.append(owner_blank(seat, purpose, "unsafe_owner_dir"))
                continue
            usage.append(owner_entry(turn, seat, purpose))
    return usage


def observed(
    root: Path, binding: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any]:
    native = root / "native"
    name = f"price-w{row['world']:02d}-r{row['repeat']}"
    trial = native / "trials" / name
    row["checkpoint_sha256"] = binding["checkpoint_sha256"]
    row["shape"] = binding["shape"]
    result = trial / "result.json"
    if result.is_file():
        rec = load(result)
        if rec.get("trial_name") != name:
            raise ValueError("native trial name mismatch")
        row["native_id"] = rec.get("id")
        verify = rec.get("verifier_result") or {}
        if rec.get("reward_valid") is True:
            row["protocol_reward"] = (verify.get("rewards") or {}).get("reward")
        row["actor_phases"] = {
            seat["seat"]: (
                ((seat.get("agent_result") or {}).get("metadata") or {}).get("phase")
            )
            for seat in rec.get("seat_results") or []
        }
    journal_path = trial / "artifacts/logs/world/timing-journal.json"
    if journal_path.is_file():
        journal = load(journal_path)
        if (
            journal.get("checkpoint_sha256") != binding["checkpoint_sha256"]
            or journal.get("early") != row["early"]
        ):
            raise ValueError("native journal is for a different branch assignment")
        picks = journal.get("picks", {})
        if (
            not isinstance(picks, dict)
            or set(picks) - set(ROLES)
            or any(type(p) is not int or p not in (1, 2, 3) for p in picks.values())
        ):
            raise ValueError("invalid partial price commitment")
        if journal.get("report") is not None:
            from . import timing_messages

            row["report"] = timing_messages.read_sales(json.dumps(journal["report"]))
        row["picks"] = picks
        row["acknowledged"] = journal.get("acknowledged", [])
        row["journal_sha256"] = hashlib.sha256(journal_path.read_bytes()).hexdigest()
    return row


def collect(
    stock: Any, target: Path, world: int, repeat: int, early: str
) -> dict[str, Any]:
    from .timing_evidence import evidence

    row = base(world, repeat, early)
    try:
        checkpoint, binding = timing_world.make_company(stock, world)
    except timing_world.CompanyEndedBeforeDecision as exc:
        return {**row, **exc.record, "reason": "company_ended_before_decision"}
    if load(target / "company.json") != binding:
        raise ValueError(
            "the issued checkpoint does not match the pinned stock reconstruction"
        )
    observed(target, binding, row)
    try:
        proof = evidence(target / "native", binding, early, control=row["control"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        category = getattr(exc, "category", None)
        row["reason"] = "no_complete_verified_pair"
        row["refusal"] = category if type(category) is str else type(exc).__name__
        if not row["control"]:
            row["owner_turns_observed"] = partial_usage(target / "native")
        picks = row["picks"]
        row["settlements"] = {
            seat: timing_world.settle(stock, checkpoint, picks[seat])
            for seat in ROLES
            if seat in picks
        }
        print(f"audit {target.name}: {row['refusal']}", file=sys.stderr)
        return row
    if any(
        k in proof and proof[k] != v
        for k, v in {
            "world": world,
            "repeat": repeat,
            "early": early,
            "checkpoint_sha256": binding["checkpoint_sha256"],
        }.items()
    ):
        raise ValueError("complete native evidence names another cell")
    row.update(proof)
    picks = row["picks"]
    if set(picks) != set(ROLES):
        raise ValueError("expected 2 committed prices")
    row["settlements"] = {
        role: timing_world.settle(stock, checkpoint, picks[role]) for role in ROLES
    }
    row["schema"] = STUDY
    row["status"] = "complete"
    return row


def native_env() -> dict[str, str]:
    env = {
        k: os.environ[k]
        for k in ("HOME", "PATH", "LANG", "TMPDIR", "DOCKER_HOST")
        if os.environ.get(k)
    }
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHON_DOTENV_DISABLED="1",
        HARBOR_TELEMETRY="0",
    )
    return env


def issue(stock: Any, target: Path, args: argparse.Namespace, early: str) -> None:
    from .timing_stage import stage

    native_python, client, slot = (
        Path(x).expanduser().absolute()
        for x in (args.native_python, args.client, args.slot)
    )
    if any(
        not p.is_file() or not os.access(p, os.X_OK)
        for p in (native_python, client, slot)
    ):
        raise ValueError(
            "native Python, trusted agy and the Docker slot must be executable files"
        )
    target.mkdir(mode=0o700)
    store(
        target / "issued.json",
        {
            "schema": STUDY,
            "world": args.world,
            "repeat": args.repeat,
            "early": early,
            "control": args.control,
        },
    )
    try:
        _, binding = timing_world.make_company(stock, args.world)
    except timing_world.CompanyEndedBeforeDecision as exc:
        store(target / "predecision.json", exc.record)
        return
    store(target / "company.json", binding)
    config = stage(
        target / "native",
        args.world,
        args.repeat,
        binding,
        early,
        client,
        control=args.control,
    )
    command = [
        str(slot),
        str(native_python),
        "-B",
        "-m",
        "examples.seatlab.market_handoff.timing_native",
        str(config),
    ]
    with (target / "native.log").open("xb") as log:
        p = subprocess.run(
            command,
            cwd=ROOT,
            env=native_env(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    store(target / "native-exit.json", {"returncode": p.returncode})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run once or inspect one native sales-report timing comparison."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "inspect"):
        sub = commands.add_parser(name)
        sub.add_argument("--stock", type=Path, required=True)
        sub.add_argument("--root", type=Path, required=True)
        sub.add_argument("--world", type=int, choices=range(9), required=True)
        sub.add_argument("--repeat", type=int, choices=(0, 1), required=True)
        sub.add_argument("--control", action="store_true")
        if name == "run":
            for param in ("native-python", "client", "slot"):
                sub.add_argument("--" + param, type=Path, required=True)
    args = parser.parse_args()
    if args.control != (args.world == timing_world.CONTROL_WORLD) or (
        args.control and args.repeat != 0
    ):
        parser.error("--control is mandatory only for company 8, repetition 0")
    os.umask(0o077)
    early = assignment(args.world, args.repeat)
    target = cell(args.root, args.world, args.repeat)
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    if stock.numpy_version != "2.1.3":
        raise ValueError("the registered experiment requires NumPy 2.1.3")
    if args.command == "run":
        issue(stock, target, args, early)
    issued = load(target / "issued.json")
    if issued != {
        "schema": STUDY,
        "world": args.world,
        "repeat": args.repeat,
        "early": early,
        "control": args.control,
    }:
        raise ValueError("original attempt does not match the requested cell")
    row = collect(stock, target, args.world, args.repeat, early)
    print(json.dumps(row, sort_keys=True, allow_nan=False))
    return 0 if row["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
