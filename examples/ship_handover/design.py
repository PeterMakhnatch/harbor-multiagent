import argparse
import json
import os
import random
import re
import secrets
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..native_study import (
    NATIVE_DEFAULTS,
    as_choice,
    as_list,
    as_record,
    as_text,
    native,
    normalize,
    record_bytes,
    require,
    require_normalized,
    task_files_digest,
    task_identity,
    trial_request,
)
from ..seat_handoffs.native_records import read_record
from .study import (
    CHANNELS,
    MAX_CELLS,
    MAX_REGISTRATION_BYTES,
    MODEL_FIELDS,
    REQUIRED_FIELDS,
    ROLES,
    SPECS,
    STOCK,
    TEAM,
    Spec,
    accepted_task,
    load_registration,
    refused_task,
    task_dir_for,
    validate_registration,
    world_env,
)

WORLDS = {
    name: tuple((state, channel) for state in spec.states for channel in CHANNELS)
    for name, spec in SPECS.items()
}
BLOCK_CELLS = len(WORLDS[STOCK.world])
MAX_BLOCKS = MAX_CELLS // BLOCK_CELLS
MAX_SEED = (1 << 63) - 1
SEED_TEXT = re.compile(r"0|[1-9][0-9]{0,18}")
BLOCKS_TEXT = re.compile(r"[1-9][0-9]{0,3}")
MAX_TICKET_DRAWS = 4 * MAX_CELLS
MAX_PARTICIPANTS_BYTES = 1 << 16
RAW_FIELDS = REQUIRED_FIELDS | MODEL_FIELDS | {"env"}
PARTICIPANTS_FIELDS = frozenset({"participants"})
PRIVATE_MODE = 0o600
STAGED = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
FAILED_EXIT = 1
INVALID_EXIT = 2
assert all(len(w) == len(set(w)) == BLOCK_CELLS == 4 for w in WORLDS.values())
assert 0 < MAX_BLOCKS * BLOCK_CELLS <= MAX_CELLS
assert BLOCKS_TEXT.fullmatch(str(MAX_BLOCKS)) is not None

DESCRIPTION = """\
Preregister one ship-handover team in one world. This command only writes a
preregistration file. It emits no trial configs, launches no trial, calls no
model, and reads no credential values. Participant env entries stay bare
${KEY} references that the launch host resolves later.

--world stock (default) uses the published harbor/ship-handover package. Its
cells set prior committed/dropped (SH_PRIOR) and carry sh- tickets.
--world inflight needs --task MATERIALIZED, made by
python3 -B -m examples.ship_inflight.materialize OUT. The package must be one
of exactly two harbor/ship-inflight pairs derived from the files pinned in
examples/ship_inflight/samples/task-pin.json: the legacy identity and tree,
or the role-aware identity and tree whose digest also covers the
instructions/outgoing.md and instructions/relief.md role files. The installed
packager decides which pair is registered. Its cells set fate
settles/fails (SH_FATE) and carry si- tickets. The same participants file
registers on both worlds, except oracle relief policy replace (inflight only).

The registration holds N complete blocks. Each block holds the 4 worlds
(state x channel deliver/withhold) once each, in a fresh uniform random
order. A block holds 2 states x 2 delivery conditions; it does not replay
the same agent decisions. Every cell gets an independent opaque ticket that
encodes no world, no seat, and no position.

The host sees the assignment. The native world receives the state and
SH_CHANNEL. Its role API does not return these fields. Keep the registration
outside the task package and do not mount it or the launch config in a seat
container.

Next step (host): python -m examples.ship_handover.study emit REG --out DIR
[--task MATERIALIZED]. An inflight summary lists the next command lines."""

PARTICIPANTS_HELP = """\
JSON object {"participants": [OUTGOING, RELIEF]} with exactly 2 raw native
Harbor participants. Each sets "seat" (outgoing or relief) and "name". Named
models set a model_name. Oracle controls set only env SH_POLICY. The nop
runner is not a model: it sets no model field and no env. Native Participant
normalization must keep every given field unchanged."""

SEED_HELP = f"""\
Optional integer 0..{MAX_SEED}. Use it only to reproduce the within-block
order of a schedule. It is not stored in the registration. Tickets stay fresh
OS-random draws. Scoring never needs it: score from the registered tickets and
the trial results collected for them. A seeded order is predictable to anyone
who holds the seed, so keep the seed host-only. The same seed gives the same
order only under the same CPython random module. Omit it for a hidden order."""


class InputError(ValueError):
    pass


def _blocks(text: str) -> int:
    if BLOCKS_TEXT.fullmatch(text) is None or not 0 < int(text) <= MAX_BLOCKS:
        raise argparse.ArgumentTypeError(f"needs an integer 1..{MAX_BLOCKS}")
    return int(text)


def _seed(text: str) -> int:
    if SEED_TEXT.fullmatch(text) is None or int(text) > MAX_SEED:
        raise argparse.ArgumentTypeError(f"needs an integer 0..{MAX_SEED}")
    return int(text)


def _fresh_target(text: str, task_dir: Path) -> Path:
    out = Path(text).absolute()
    require(out.name not in ("", ".", ".."), "output names no new file")
    try:
        parent = out.parent.resolve(strict=True)
    except OSError as error:
        raise ValueError("output directory does not exist") from error
    require(parent.is_dir(), "output parent is not a directory")
    target = parent / out.name
    require(not os.path.lexists(target), "output already exists; nothing is replaced")
    require(
        not target.is_relative_to(task_dir),
        "output inside the task package would reach the participants",
    )
    return target


def _check_raw_participant(value: object, where: str) -> dict[str, Any]:
    raw = as_record(value, where)
    require(set(raw) <= RAW_FIELDS, f"{where} has fields outside the v1 allowlist")
    require(isinstance(raw.get("name"), str), f"{where} names no agent")
    as_choice(raw.get("seat"), f"{where} seat", ROLES)
    for field, default in NATIVE_DEFAULTS.items():
        found = raw.get(field, default)
        require(
            type(found) is type(default) and found == default,
            f"{where} changes a native default",
        )
    return raw


def _read_participants(path: Path) -> list[dict[str, Any]]:
    try:
        record = read_record(path, MAX_PARTICIPANTS_BYTES)
    except OSError as error:
        raise ValueError("participants file is unreadable") from error
    require(record is not None, "participants file does not exist")
    roster = as_record(record, "participants file", PARTICIPANTS_FIELDS)
    members = as_list(roster["participants"], "participants", 2, 2)
    raws = [
        _check_raw_participant(member, f"participant {position}")
        for position, member in enumerate(members)
    ]
    seats = {raw["seat"] for raw in raws}
    require(seats == set(ROLES), "participants need one outgoing and one relief seat")
    return raws


def _normalize_team(
    harbor: SimpleNamespace, label: str, raws: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    assert len(raws) == len(ROLES)
    team: list[dict[str, Any]] = []
    for position, raw in enumerate(raws):
        try:
            member = normalize(harbor, raw)
        except (ValueError, TypeError) as error:
            raise ValueError(
                f"participant {position} fails native Participant validation"
            ) from error
        changed = sorted(key for key, value in raw.items() if member.get(key) != value)
        require(not changed, f"native normalization changes participant {position}")
        team.append(member)
    require_normalized(harbor, {label: team})
    return team


def _verified_task(
    harbor: SimpleNamespace, task_dir: Path, spec: Spec
) -> tuple[dict[str, str], str]:
    try:
        found = task_identity(harbor, task_dir, spec.task_name)
        before = task_files_digest(task_dir, found)
        again = task_identity(harbor, task_dir, spec.task_name)
        after = task_files_digest(task_dir, again)
    except (ValueError, OSError) as error:
        raise ValueError("native Task/Packager cannot load the task") from error
    require(found == again and before == after, "task changed while it was checked")
    if not accepted_task(spec, found, before):
        raise InputError(f"{refused_task(spec, f'task {task_dir}')}: {found}")
    return dict(found), before


def _schedule(blocks: int, seed: int | None, spec: Spec) -> list[tuple[str, str]]:
    assert 0 < blocks <= MAX_BLOCKS
    assert seed is None or 0 <= seed <= MAX_SEED
    order = secrets.SystemRandom() if seed is None else random.Random(seed)
    plan: list[tuple[str, str]] = []
    for _ in range(blocks):
        block = list(WORLDS[spec.world])
        order.shuffle(block)
        plan.extend(block)
    assert len(plan) == blocks * BLOCK_CELLS
    return plan


def _draw_tickets(count: int, spec: Spec) -> list[str]:
    assert 0 < count <= MAX_CELLS
    tickets: list[str] = []
    seen: set[str] = set()
    for _ in range(MAX_TICKET_DRAWS):
        if len(tickets) == count:
            break
        ticket = as_text(spec.prefix + secrets.token_hex(8), "ticket", spec.ticket)
        if ticket not in seen:
            seen.add(ticket)
            tickets.append(ticket)
    require(len(tickets) == count, "ticket draws kept colliding")
    return tickets


def _cells(label: str, plan: list[tuple[str, str]], spec: Spec) -> list[dict[str, Any]]:
    tickets = _draw_tickets(len(plan), spec)
    return [
        {"ticket": ticket, "team": label, spec.axis: state, "channel": channel}
        for ticket, (state, channel) in zip(tickets, plan, strict=True)
    ]


def _check_blocks(
    cells: list[dict[str, Any]], blocks: int, label: str, spec: Spec
) -> None:
    require(len(cells) == blocks * BLOCK_CELLS, "design holds other cell counts")
    require(len({cell["ticket"] for cell in cells}) == len(cells), "tickets repeat")
    expected = sorted(WORLDS[spec.world])
    for start in range(0, len(cells), BLOCK_CELLS):
        block = cells[start : start + BLOCK_CELLS]
        worlds = sorted((cell[spec.axis], cell["channel"]) for cell in block)
        require(worlds == expected, f"block {start // BLOCK_CELLS} is incomplete")
        require(all(cell["team"] == label for cell in block), "cell names another team")


def _probe_worlds(
    harbor: SimpleNamespace,
    task_dir: Path,
    target: Path,
    registration: dict[str, Any],
    spec: Spec,
) -> None:
    team = registration["teams"][registration["cells"][0]["team"]]
    probes = {(c[spec.axis], c["channel"]): c for c in registration["cells"]}
    require(len(probes) == BLOCK_CELLS, "design misses a world")
    for cell in probes.values():
        env = world_env(cell)
        trial_request(harbor, task_dir, target.parent, cell["ticket"], team, env)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(target: Path, registration: dict[str, Any]) -> None:
    payload = record_bytes(registration)
    require(len(payload) <= MAX_REGISTRATION_BYTES, "registration exceeds its bound")
    staged = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(staged, STAGED, PRIVATE_MODE)
    try:
        with open(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        require(load_registration(staged) == registration, "staged copy differs")
        try:
            os.link(staged, target)
        except FileExistsError as error:
            raise InputError("output appeared; nothing is replaced") from error
    finally:
        staged.unlink(missing_ok=True)
    _sync_directory(target.parent)


def _inputs(
    args: argparse.Namespace,
) -> tuple[Spec, Path, Path, str, list[dict[str, Any]]]:
    spec = SPECS[args.world]
    try:
        task_dir = task_dir_for(spec, args.task)
        target = _fresh_target(args.out, task_dir)
        label = as_text(args.team, "team label", TEAM)
        raws = _read_participants(Path(args.participants))
    except ValueError as error:
        raise InputError(str(error)) from error
    return spec, task_dir, target, label, raws


def _next_commands(target: Path, task_dir: Path) -> list[str]:
    reg, workspace = str(target), str(target.with_name(f"{target.stem}-w0"))
    task = ("--task", str(task_dir))
    module = ("python3", "-BS", "-m")
    study = ("uv", "run", "python", "-B", "-m", "examples.ship_handover.study")
    take = shlex.join(
        (*module, "examples.native_worklist", "take", "--study", reg, workspace, "0/1")
    )
    return [
        shlex.join((*study, "emit", reg, "--out", workspace, *task, "--shard", "0/1")),
        f'cfg=$({take}) && uv run harbor trial start -c "$cfg"',
        shlex.join((*module, "examples.ship_handover.collect", reg, workspace, *task)),
        shlex.join((*module, "examples.ship_handover.contrast", reg, workspace, *task)),
    ]


def register(args: argparse.Namespace) -> dict[str, Any]:
    spec, task_dir, target, label, raws = _inputs(args)
    harbor = native()
    try:
        team = _normalize_team(harbor, label, raws)
    except ValueError as error:
        raise InputError(str(error)) from error
    task, files = _verified_task(harbor, task_dir, spec)
    cells = _cells(label, _schedule(args.blocks, args.seed, spec), spec)
    _check_blocks(cells, args.blocks, label, spec)
    registration = {
        "schema": spec.schema,
        "task": task,
        "teams": {label: team},
        "cells": cells,
    }
    try:
        validate_registration(registration)
        _probe_worlds(harbor, task_dir, target, registration, spec)
    except (ValueError, TypeError) as error:
        raise InputError(f"team fails the study contract: {error}") from error
    _publish(target, registration)
    summary = {
        "status": "registered",
        "registration": str(target),
        "task": task,
        "team": label,
        "seats": [member["seat"] for member in team],
        "blocks": args.blocks,
        "cells": len(cells),
        "worlds_per_block": BLOCK_CELLS,
        "order": "hidden" if args.seed is None else "seeded",
    }
    if spec is not STOCK:
        summary |= {
            "world": spec.world,
            "task_files": files,
            "next": _next_commands(target, task_dir),
        }
    return summary


def _render(summary: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    lines = [
        f"{key}: {json.dumps(value, sort_keys=True, allow_nan=False)}"
        for key, value in summary.items()
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m examples.ship_handover.design",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root.add_argument("--participants", required=True, help=PARTICIPANTS_HELP)
    root.add_argument(
        "--team", required=True, help="team label for the registration and its cells"
    )
    root.add_argument(
        "--blocks",
        required=True,
        type=_blocks,
        help=f"complete 4-world blocks, 1..{MAX_BLOCKS} (cells cap {MAX_CELLS})",
    )
    root.add_argument(
        "--out",
        required=True,
        help="new registration file; written once, atomically, never replaced",
    )
    root.add_argument("--seed", type=_seed, help=SEED_HELP)
    root.add_argument(
        "--world",
        choices=tuple(SPECS),
        default=STOCK.world,
        help="stock (default) or inflight; inflight needs --task MATERIALIZED",
    )
    root.add_argument(
        "--task",
        help=f"task package directory (stock default {STOCK.task_dir}; "
        "inflight: the materialized harbor/ship-inflight package)",
    )
    root.add_argument("--json", action="store_true", help="print the summary as JSON")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        summary = register(args)
    except InputError as error:
        print(f"error: invalid input: {error}", file=sys.stderr)
        return INVALID_EXIT
    except ValueError as error:
        print(f"error: register failed: {error}", file=sys.stderr)
        return FAILED_EXIT
    except OSError as error:
        print(f"error: register failed: {type(error).__name__}", file=sys.stderr)
        return FAILED_EXIT
    print(_render(summary, args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
