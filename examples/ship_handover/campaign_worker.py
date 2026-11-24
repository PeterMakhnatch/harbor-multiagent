import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..native_study import native, require
from ..native_tickets import MAX_CONFIG_BYTES, MAX_REGISTRATION_BYTES, Survey
from ..native_worklist import StorageFault, _safe, _survey_all, _take
from ..seat_checkpoints.materialize import target
from ..seat_seals.admission_wire import (
    ALREADY_ISSUED,
    GRANTED,
    SHIPPING_REPLY_SCHEMA,
    SHIPPING_REQUEST_SCHEMA,
    make_reply,
    make_request,
    read_message,
    validate_reply,
    wire_bytes,
)
from ..seat_seals.admitted_run import exchange, trusted_via
from . import campaign_plan
from .study import INFLIGHT, STOCK, TASK_DIR
from .transfer_contract import registration_digest

PROG = "examples.ship_handover.campaign_worker"
WORLDS = (STOCK, INFLIGHT)
INFLIGHT_TASK = "ship-inflight"
ROOT_MODE = 0o700
NONCE_BYTES = 16
EXIT_FRESH = 0
EXIT_FAILURE = 2
EXIT_EXHAUSTED = 3
EXIT_ISSUED = 4
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
Plan = dict[str, Any]
Bound = tuple[Plan, str, list[Path], list[str]]

assert tuple(spec.world for spec in WORLDS) == ("stock", "inflight")


def _file_sha(path: Path, limit: int, label: str) -> str:
    fd = os.open(path, PLAIN_FILE)
    with open(fd, "rb") as handle:
        require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), f"{label} is odd")
        data = handle.read(limit + 1)
    require(0 < len(data) <= limit, f"{label} is empty or exceeds {limit} bytes")
    return hashlib.sha256(data).hexdigest()


def _bound(args: argparse.Namespace) -> Bound:
    worker = args.worker
    require(type(worker) is int and worker >= 0, "--worker must be a natural number")
    plan = campaign_plan.load_plan(Path(args.plan), expected=args.expected_plan)
    digest = registration_digest(plan)
    require(digest == args.expected_plan, "plan digest differs from --expected-plan")
    require(worker < plan["workers"], "--worker is outside the plan")
    regs = [Path(os.path.abspath(args.stock)), Path(os.path.abspath(args.inflight))]
    shas = [
        _file_sha(reg, MAX_REGISTRATION_BYTES, f"{spec.world} registration")
        for reg, spec in zip(regs, WORLDS, strict=True)
    ]
    campaign_plan.bind(plan, regs[0], regs[1])
    return plan, digest, regs, shas


def _shard(plan: Plan, worker: int) -> str:
    assert 0 <= worker < plan["workers"]
    return f"{worker}/{plan['workers']}"


def _studies(root: Path, bound: Bound, worker: int) -> list[tuple[str, str, str]]:
    plan, _, regs, _ = bound
    shard = _shard(plan, worker)
    return [
        (str(reg), str(root / spec.world), shard)
        for reg, spec in zip(regs, WORLDS, strict=True)
    ]


def _owned(plan: Plan, world: str, worker: int) -> list[str]:
    tickets = plan["tickets"][world]
    return [t for i, t in enumerate(tickets) if i % plan["workers"] == worker]


def _check_found(found: list[Survey], root: Path, bound: Bound, worker: int) -> None:
    plan, _, _, shas = bound
    owners = dict(campaign_plan.inventory(plan))
    tasks = (str(TASK_DIR), str(root / INFLIGHT_TASK))
    require(len(found) == len(WORLDS), "worker must survey exactly both worlds")
    for item, spec, sha, task in zip(found, WORLDS, shas, tasks, strict=True):
        name = spec.world
        require(item.world.schema == spec.schema, f"{name} workspace is another world")
        require(item.workspace == root / name, f"{name} workspace left ROOT")
        require(
            item.registration_sha256 == sha,
            f"{name} registration changed while it was bound",
        )
        owned = _owned(plan, name, worker)
        require(item.tickets == owned, f"{name} tickets differ from the plan shard")
        require(
            all(owners.get(ticket) == worker for ticket in owned),
            f"{name} tickets are not owned by this worker in the plan",
        )
        require(item.task_path == task, f"{name} configs name another task package")


def _granted_ticket(config: str, found: list[Survey], root: Path) -> str:
    path = Path(config)
    require(os.path.realpath(config) == config, "taken config path is not canonical")
    for item, spec in zip(found, WORLDS, strict=True):
        if path.parent != root / spec.world / "configs":
            continue
        ticket = path.stem
        require(
            path.name == f"{ticket}.json" and spec.ticket.fullmatch(ticket) is not None,
            f"taken config is not a {spec.world} ticket",
        )
        require(ticket in item.tickets, "taken ticket is not assigned to this worker")
        current = _file_sha(path, MAX_CONFIG_BYTES, f"configs/{ticket}.json")
        require(current == item.configs[ticket], f"configs/{ticket}.json changed")
        return ticket
    raise ValueError("taken config is outside ROOT/stock and ROOT/inflight configs")


def _admit(via: Path, digest: str, worker: int, ticket: str) -> str:
    nonce = secrets.token_hex(NONCE_BYTES)
    request = make_request(digest, worker, ticket, nonce)
    require(request["schema"] == SHIPPING_REQUEST_SCHEMA, "request is not shipping")
    sent = wire_bytes(request)
    require(read_message(sent) == request, "request does not round-trip")
    data = exchange(via, sent)
    reply = read_message(data)
    status = validate_reply(reply, request)
    require(status in (GRANTED, ALREADY_ISSUED), "coordinator status is unknown")
    receipt = make_reply(request, status)
    require(receipt["schema"] == SHIPPING_REPLY_SCHEMA, "receipt is not shipping")
    require(
        reply == receipt and data == wire_bytes(receipt),
        "coordinator reply is not the literal receipt for this request",
    )
    return status


def _existing_root(text: str) -> Path:
    raw = Path(text)
    require(raw.is_absolute(), "ROOT must be absolute")
    info = os.lstat(raw)
    require(stat.S_ISDIR(info.st_mode), "ROOT is not a plain directory")
    require(info.st_uid == os.geteuid(), "ROOT is owned by another user")
    require(stat.S_IMODE(info.st_mode) & 0o077 == 0, "ROOT is not private")
    root = raw.resolve(strict=True)
    after = os.lstat(root)
    require(
        (after.st_dev, after.st_ino) == (info.st_dev, info.st_ino),
        "ROOT changed while it was resolved",
    )
    return root


def _say(message: str) -> None:
    print(f"campaign_worker: {_safe(message)}", file=sys.stderr)


def take(args: argparse.Namespace) -> int:
    via = trusted_via(Path(args.via))
    bound = _bound(args)
    root = _existing_root(args.root)
    with ExitStack() as stack:
        found = _survey_all(stack, _studies(root, bound, args.worker))
        _check_found(found, root, bound, args.worker)
        config = _take(stack, found)
        if config is None:
            _say(f"no local ticket remains for worker {args.worker}")
            return EXIT_EXHAUSTED
        ticket = _granted_ticket(config, found, root)
    try:
        status = _admit(via, bound[1], args.worker, ticket)
    except (OSError, ValueError) as error:
        _say(
            f"ticket {ticket} stays claimed here without a known grant; "
            f"never start or relaunch it: {type(error).__name__}: {error}"
        )
        return EXIT_FAILURE
    if status == ALREADY_ISSUED:
        _say(f"ticket {ticket} was already issued; it stays claimed; take the next")
        return EXIT_ISSUED
    assert status == GRANTED
    print(config)
    return EXIT_FRESH


def _new_root(text: str) -> Path:
    require(Path(text).is_absolute(), "ROOT must be absolute")
    root = target(text)
    os.mkdir(root, ROOT_MODE)
    os.chmod(root, ROOT_MODE)
    info = os.lstat(root)
    require(stat.S_ISDIR(info.st_mode), "ROOT changed after it was created")
    require(stat.S_IMODE(info.st_mode) == ROOT_MODE, "ROOT is not private")
    return root


def _next_take(args: argparse.Namespace, bound: Bound, root: Path) -> list[str]:
    _, digest, regs, _ = bound
    command = ["python3.12", "-BS", "-m", PROG, "take"]
    paths = [str(regs[0]), str(regs[1]), os.path.abspath(args.plan), str(root)]
    bindings = ["--worker", str(args.worker), "--expected-plan", digest]
    return command + paths + bindings + ["--via", "ABS_EXEC"]


def stage(args: argparse.Namespace) -> int:
    from ..ship_inflight.materialize import materialize
    from .study import emit

    bound = _bound(args)
    try:
        native()
    except SystemExit as error:
        raise ValueError(str(error)) from None
    root = _new_root(args.root)
    materialize(root / INFLIGHT_TASK)
    shard = _shard(bound[0], args.worker)
    tasks = (None, str(root / INFLIGHT_TASK))
    for reg, spec, task in zip(bound[2], WORLDS, tasks, strict=True):
        out = str(root / spec.world)
        emit(argparse.Namespace(registration=str(reg), out=out, task=task, shard=shard))
    with ExitStack() as stack:
        found = _survey_all(stack, _studies(root, bound, args.worker))
        _check_found(found, root, bound, args.worker)
        configs = {spec.world: len(item.tickets) for item, spec in zip(found, WORLDS)}
    receipt = {
        "plan": bound[1],
        "worker": args.worker,
        "shard": shard,
        "root": str(root),
        "configs": configs,
        "next": _next_take(args, bound, root),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True))
    return EXIT_FRESH


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog=f"python -B -m {PROG}")
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("stage", "take"):
        command = commands.add_parser(name)
        command.add_argument("stock", metavar="STOCK_REG")
        command.add_argument("inflight", metavar="INFLIGHT_REG")
        command.add_argument("plan", metavar="PLAN")
        command.add_argument("root", metavar="ROOT")
        command.add_argument("--worker", type=int, required=True)
        command.add_argument("--expected-plan", required=True)
        if name == "take":
            command.add_argument("--via", required=True, metavar="ABS_EXEC")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return stage(args) if args.command == "stage" else take(args)
    except ValueError as error:
        print(f"error: invalid: {_safe(str(error))}", file=sys.stderr)
    except StorageFault as error:
        print(f"error: storage: {_safe(str(error))}", file=sys.stderr)
    except OSError as error:
        print(f"error: storage: {_safe(error.strerror or 'failed')}", file=sys.stderr)
    return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
