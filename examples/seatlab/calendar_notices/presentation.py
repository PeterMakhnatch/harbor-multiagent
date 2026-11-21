import stat
from pathlib import Path
from typing import Any
from uuid import UUID

from ..visibility import calendar_packet
from . import protocol as p

_RECEIPT = Path(__file__).resolve().parents[1] / "results/calendar-notice-live.json"
_MAX_BYTES = 1024 * 1024
_CALENDAR_START = "Your calendar: one anonymous attendee per group. "
_LOCAL_STARTS = (
    "Your calendar: 2-hour start IDs that fit all 3 attendees at your seat: "
)
_TASK = "\n\nTask:\n"
_Row = dict[str, Any]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _read(path: Path) -> bytes:
    try:
        _require(path.resolve(strict=True) == path, "unsafe evidence path")
        info = path.lstat()
        _require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "evidence is not a physical regular file",
        )
        _require(info.st_size <= _MAX_BYTES, "evidence exceeds the size limit")
        with path.open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
    except (OSError, RuntimeError):
        raise ValueError("evidence is missing or unreadable") from None
    _require(len(data) <= _MAX_BYTES, "evidence exceeds the size limit")
    return data


def _record(data: bytes | str) -> _Row:
    return calendar_packet._object(calendar_packet._json(data))


def _live_cells() -> dict[str, _Row]:
    receipt = _record(_read(_RECEIPT))
    _require(
        (receipt.get("schema"), receipt.get("mode"))
        == ("seatlab.calendar-notice-audit/v1", "live"),
        "unexpected live receipt schema or mode",
    )
    cells = calendar_packet._list(receipt.get("cells"))
    _require(len(cells) == len(p.WORLDS) * len(p.ARMS), "incomplete live cell grid")
    rows: dict[str, _Row] = {}
    trials: set[UUID] = set()
    for item in cells:
        row = calendar_packet._object(item)
        cell = p.Cell(row["world"], row["arm"])
        identifier = UUID(row.get("trial_uuid"))
        _require(
            row.get("run") == cell.run
            and row.get("control") == "live"
            and row.get("attempted") is True
            and row.get("complete") is True
            and row.get("reward_valid") is True
            and row.get("reward_verified") is True
            and row.get("error") is None
            and type(row.get("reward")) is int
            and row["reward"] in (0, 1),
            "invalid or incomplete live cell",
        )
        _require(
            identifier.version == 4
            and str(identifier) == row["trial_uuid"]
            and identifier not in trials
            and cell.run not in rows,
            "invalid or duplicate live trial identity",
        )
        trials.add(identifier)
        rows[cell.run] = row
    return rows


def _receiver(
    trial: Path, cell: p.Cell, row: _Row, own_table: calendar_packet.Table
) -> _Row:
    raw = _read(trial / "result.json")
    _require(p.digest(raw) == row["result_sha256"], "native result digest mismatch")
    result = _record(raw)
    reward = result["verifier_result"]["rewards"]["reward"]
    _require(
        result["id"] == row["trial_uuid"]
        and result["trial_name"] == cell.run
        and result["task_name"] == f"flotilla/{cell.task_name}"
        and result["task_checksum"] == row["source"]["task_sha256"]
        and result["reward_valid"] is True
        and type(reward) in (int, float)
        and reward == row["reward"],
        "native result binding mismatch",
    )
    seats = result["seat_results"]
    _require([seat["seat"] for seat in seats] == list(p.SEATS), "native seat mismatch")
    state = seats[cell.index % 2]["agent_result"]["metadata"]["calendar_notice"]
    _require(
        (state["run"], state["seat"], state["stage"], state["phase"], state["error"])
        == (cell.run, cell.receiver, "complete", "choice", None)
        and state["calendar_sha256"] == p.digest(p.wire(own_table)),
        "native receiver binding mismatch",
    )
    return state


def _messages(trial: Path, cell: p.Cell, state: _Row) -> tuple[_Row, _Row, _Row]:
    lines = _read(trial / "artifacts/logs/world/messages.jsonl").splitlines()
    _require(len(lines) == 3, "expected exactly 3 native messages")
    first, second, third = map(_record, lines)
    directions = (
        (cell.sender, cell.receiver),
        (cell.receiver, cell.sender),
        (cell.sender, cell.receiver),
    )
    previous = 0
    for i, (message, direction) in enumerate(
        zip((first, second, third), directions, strict=True), 1
    ):
        _require(
            message.keys() == {"id", "from", "to", "text", "t"}
            and message["id"] == f"m{i}"
            and (message["from"], message["to"]) == direction
            and isinstance(message["text"], str)
            and 0 < len(message["text"]) <= 4000
            and type(message["t"]) in (int, float)
            and previous <= message["t"] < float("inf"),
            "invalid native message scope or order",
        )
        previous = message["t"]
    _require(
        p.wire(state["received"]) == p.wire([first, third])
        and p.wire(state["sent"]) == p.wire([second]),
        "service messages differ from the authenticated receiver record",
    )
    bodies = [_record(message["text"]) for message in (first, second, third)]
    _require(
        all(
            p.wire(body) == message["text"]
            for body, message in zip(bodies, (first, second, third), strict=True)
        ),
        "noncanonical service message",
    )
    return bodies[0], bodies[1], bodies[2]


def _turn(
    trial: Path, cell: p.Cell, row: _Row, phase: str, prompt: str
) -> list[int] | int:
    receipt = _record(
        _read(trial / "seats" / cell.receiver / "agent" / f"{phase}.json")
    )
    text = receipt["response_text"]
    _require(
        (
            receipt["trial_uuid"],
            receipt["run"],
            receipt["seat"],
            receipt["phase"],
            receipt["ledger_role"],
            receipt["status"],
        )
        == (
            row["trial_uuid"],
            cell.run,
            cell.receiver,
            phase,
            p.role(cell, phase),
            "accepted",
        )
        and receipt["prompt_sha256"] == p.digest(prompt)
        and isinstance(text, str)
        and receipt["response_sha256"] == p.digest(text),
        f"native receiver {phase} receipt mismatch",
    )
    value = p.decode(text, phase)
    _require(
        p.wire(value) == p.wire(receipt["value"]), "native response value mismatch"
    )
    return value


def _prompts(
    trial: Path, cell: p.Cell, row: _Row, world: calendar_packet.World
) -> tuple[str, str]:
    receiver_index = cell.index % 2
    own_table = world.tables[receiver_index]
    state = _receiver(trial, cell, row, own_table)
    index, request, opened = _messages(trial, cell, state)
    fields = {"initial", "count"}
    if cell.arm == "named":
        fields.add("changed")
    _require(index.keys() == fields, "notice does not match the native arm")
    pick = _turn(trial, cell, row, "pick", p.pick_prompt(own_table, index))
    _require(
        p.wire(request) == p.request_wire(pick)
        and p.wire([report["day"] for report in opened["reports"]]) == p.wire(pick),
        "opened weekday order does not match the native pick",
    )
    raw = p.choice_prompt(own_table, index["initial"], opened)
    choice = _turn(trial, cell, row, "choice", raw)
    if type(choice) is not int:
        raise ValueError("invalid native choice")
    own_starts = list(calendar_packet.starts(own_table))
    locally_fits = choice in own_starts
    _require(
        row["chosen_locally_fits"] is locally_fits
        and row["reward"]
        == int(
            locally_fits
            and choice in calendar_packet.starts(world.tables[1 - receiver_index])
        ),
        "historical choice does not match the verified outcomes",
    )
    prefix, marker, remainder = raw.partition(_CALENDAR_START)
    _, task, suffix = remainder.partition(_TASK)
    _require(bool(marker and task), "native calendar section not found")
    return raw, prefix + _LOCAL_STARTS + p.wire(own_starts) + "." + task + suffix


def contexts(native_root: Path) -> dict[str, tuple[str, str]]:
    try:
        rows = _live_cells()
    except (TypeError, AttributeError):
        raise ValueError("invalid checked-in live cell evidence") from None
    worlds = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)
    root = native_root.absolute()
    prompts = {}
    for world in p.WORLDS:
        for arm in p.ARMS:
            cell = p.Cell(world, arm)
            trial = root / "cells" / cell.run / "trials" / cell.run
            try:
                prompts[cell.run] = _prompts(
                    trial, cell, rows[cell.run], worlds[cell.index]
                )
            except (KeyError, TypeError, IndexError, RecursionError):
                raise ValueError(f"{cell.run}: invalid native evidence") from None
            except ValueError as exc:
                raise ValueError(f"{cell.run}: {exc}") from None
    return prompts
