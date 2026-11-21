import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from ..attention import protocol as attention
from ..briefing import protocol as briefing
from ..visibility import clarify_run as files
from ..visibility import (
    deepseek,
    route_cli,
    route_pending,
    route_run,
    seat_command,
    spend,
)
from ..visibility.clarify_run import StudyError
from . import journal, protocol
from . import packet as packets

type _Record = dict[str, Any]
type _Respond = Callable[[str, str, str], route_run.Answer]

_SCHEMA = "seatlab.turn-fork-register/v1"
_LIMIT = 64 * 1024
_REGISTER = "register.json"
_COMMANDS = "commands.json"
_COMMITMENT = Path(__file__).resolve().parents[1] / "results" / "turn-fork.sha256"
_STAGES = (("push", "inventory"), ("decide", "dispatch"))
_FORBIDDEN_ENV = {"DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR"}


def _grid() -> list[_Record]:
    return [
        {
            "index": index,
            "source_id": f"s{seed}-r{rep}",
            "seed": seed,
            "repetition": rep,
            "arm": arm,
        }
        for index, (seed, rep, arm) in enumerate(
            (seed, rep, arm)
            for seed in packets.SEEDS
            for rep in packets.REPETITIONS
            for arm in protocol.arms(seed, rep)
        )
    ]


def _hashes(transport: str) -> dict[str, str]:
    modules: list[ModuleType] = [
        attention,
        briefing,
        protocol,
        journal,
        packets,
        route_run,
        files,
    ]
    if transport == "direct":
        modules.extend((route_cli, deepseek, spend))
    else:
        modules.append(seat_command)
    hashes = {"runner": sha256(Path(__file__).read_bytes()).hexdigest()}
    for module in modules:
        filename = module.__file__
        if filename is None:
            raise StudyError("source_unavailable")
        hashes[module.__name__] = sha256(Path(filename).read_bytes()).hexdigest()
    return hashes


def _physical(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise StudyError("physical_absolute_path_required")
    return path


def _identity(fd: int) -> list[int]:
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino]


def _ledger_identity(root: Path, fd: int) -> _Record:
    database = spend._location(root)
    with files._regular(fd, database.name, 0o600) as stream:
        return {
            "path": str(root),
            "directory_id": _identity(fd),
            "database_id": _identity(stream.fileno()),
        }


def _reservations(root: Path, digest: str, cells: list[_Record]) -> set[int]:
    registered = {
        (
            protocol.run_id(digest, cell["source_id"], cell["arm"], stage),
            role,
        ): cell["index"]
        for cell in cells
        for stage, role in _STAGES
    }
    return route_pending._ledger_blocks(root, f"tf-{digest[:12]}-", registered)


def _measured(digest: str) -> None:
    with files._directory(_COMMITMENT.parent) as parent:
        with files._regular(parent, _COMMITMENT.name) as stream:
            raw = stream.read(67)
    if raw not in (digest.encode("ascii"), digest.encode("ascii") + b"\n"):
        raise StudyError("direct_packet_not_registered")


def _config_document(raw: bytes) -> _Record:
    if not raw or len(raw) > _LIMIT:
        raise StudyError("invalid_command_config")
    config = files._object(raw)
    for role in ("reviewer", "decider"):
        spec = config.get(role)
        names = spec.get("env") if isinstance(spec, dict) else None
        if not isinstance(names, list) or any(
            not isinstance(name, str) or name.upper() in _FORBIDDEN_ENV
            for name in names
        ):
            raise StudyError("invalid_command_config")
    return config


def _settings(
    digest: str,
    transport: str,
    config: Path | None,
    spend_dir: Path | None,
    cells: list[_Record],
) -> tuple[_Record | None, _Record | None]:
    if transport == "commands" and config is not None and spend_dir is None:
        config = files._absolute(config)
        with files._directory(config.parent) as parent:
            with files._regular(parent, config.name) as stream:
                return _config_document(stream.read(_LIMIT + 1)), None
    if transport == "direct" and config is None and spend_dir is not None:
        _measured(digest)
        root = _physical(spend_dir)
        with files._directory(root) as fd:
            ledger = _ledger_identity(root, fd)
            _reservations(root, digest, cells)
        return None, ledger
    raise StudyError("invalid_transport_settings")


def seal(
    study: Path,
    digest: str,
    transport: str,
    config: Path | None,
    spend_dir: Path | None,
) -> _Record:
    if not files._is_digest(digest):
        raise StudyError("invalid_packet_digest")
    if transport not in ("direct", "commands"):
        raise StudyError("invalid_transport_settings")
    study = files._study_path(_physical(study))
    cells = _grid()
    try:
        spec, ledger = _settings(digest, transport, config, spend_dir, cells)
        register = {
            "schema": _SCHEMA,
            "study_tag": uuid4().hex,
            "packet_sha256": digest,
            "transport": transport,
            "code_sha256": _hashes(transport),
            "command_config_sha256": (
                sha256(files._encode(spec)).hexdigest() if spec is not None else None
            ),
            "cells": cells,
        }
        with files._directory(study.parent) as parent:
            os.mkdir(study.name, 0o700, dir_fd=parent)
            fd = os.open(study.name, files._DIRECTORY, dir_fd=parent)
            try:
                files._private(fd, mode=0o700)
                if spec is not None:
                    files._publish(fd, _COMMANDS, spec, mode=0o400)
                    seat_command.load_commands(study / _COMMANDS)
                files._publish(
                    fd,
                    _REGISTER,
                    {"register": register, "study_id": _identity(fd), "ledger": ledger},
                    mode=0o400,
                )
            finally:
                os.close(fd)
    except FileExistsError:
        raise StudyError("study_already_exists") from None
    except (OSError, ValueError, RecursionError) as error:
        if isinstance(error, StudyError):
            raise
        raise StudyError("invalid_study_settings") from None
    return register


def _read_register(study: Path, digest: str) -> tuple[_Record, _Record | None]:
    study = files._study_path(_physical(study))
    with files._directory(study) as fd:
        files._private(fd, mode=0o700)
        with files._regular(fd, _REGISTER, 0o400) as stream:
            raw = stream.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise StudyError("invalid_register")
        record = files._object(raw)
        register = record["register"]
        ledger = record["ledger"]
        if (
            record.keys() != {"register", "study_id", "ledger"}
            or record["study_id"] != _identity(fd)
            or not isinstance(register, dict)
            or register.keys()
            != {
                "schema",
                "study_tag",
                "packet_sha256",
                "transport",
                "cells",
                "code_sha256",
                "command_config_sha256",
            }
            or register["schema"] != _SCHEMA
            or register["packet_sha256"] != digest
            or not files._is_digest(digest)
            or not _valid_tag(register["study_tag"])
            or not isinstance(register["code_sha256"], dict)
            or not all(
                files._is_digest(value) for value in register["code_sha256"].values()
            )
            or files._encode(register["cells"]) != files._encode(_grid())
            or files._encode(record) != raw
        ):
            raise StudyError("invalid_register")
        transport = register["transport"]
        if transport == "commands" and ledger is None:
            with files._regular(fd, _COMMANDS, 0o400) as stream:
                config_raw = stream.read(_LIMIT + 1)
            if (
                len(config_raw) > _LIMIT
                or sha256(config_raw).hexdigest() != register["command_config_sha256"]
            ):
                raise StudyError("command_config_changed")
            _config_document(config_raw)
        elif transport == "direct" and isinstance(ledger, dict):
            if (
                register["command_config_sha256"] is not None
                or ledger.keys() != {"path", "directory_id", "database_id"}
                or not isinstance(ledger["path"], str)
            ):
                raise StudyError("invalid_register")
        else:
            raise StudyError("invalid_register")
    return register, ledger


def _valid_tag(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(char in "0123456789abcdef" for char in value)
    )


def load(study: Path, digest: str) -> _Record:
    try:
        register, _ = _read_register(study, digest)
        return register
    except StudyError:
        raise
    except (OSError, ValueError, KeyError, RecursionError):
        raise StudyError("invalid_register") from None


@contextmanager
def _direct_reservations(
    ledger: _Record, digest: str, cells: list[_Record]
) -> Iterator[set[int]]:
    root = _physical(Path(ledger["path"]))
    if os.environ.get("SEATLAB_SPEND_DIR") != str(root):
        raise StudyError("original_spend_directory_required")
    with files._directory(root) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _ledger_identity(root, fd) != ledger:
            raise StudyError("original_spend_directory_required")
        yield _reservations(root, digest, cells)


def reserved_indices(study: Path, digest: str) -> set[int]:
    """Read ledger-only reservations without admitting a provider or modifying the ledger."""
    register, ledger = _read_register(study, digest)
    if ledger is None:
        return set()
    root = _physical(Path(ledger["path"]))
    with files._directory(root) as fd:
        if _ledger_identity(root, fd) != ledger:
            raise StudyError("original_spend_directory_required")
        return _reservations(root, digest, register["cells"])


def _callback(study: Path, *, direct: bool) -> _Respond:
    admitted = False
    commands: tuple[seat_command.CommandSeat, seat_command.CommandSeat] | None = None

    def respond(prompt: str, run: str, role: str) -> route_run.Answer:
        nonlocal admitted, commands
        if role not in ("inventory", "dispatch"):
            raise StudyError("invalid_role")
        if direct:
            if not admitted:
                route_cli._admit()
                admitted = True
            return route_cli._direct(prompt, run, role)
        if commands is None:
            commands = seat_command.load_commands(study / _COMMANDS)
        seat = commands[0] if role == "inventory" else commands[1]
        return route_run.Answer(seat(prompt))

    return respond


def _run_pending(
    study: Path,
    digest: str,
    cells: list[_Record],
    sources: dict[str, _Record],
    indices: range,
    reserved: set[int],
    respond: _Respond,
) -> list[_Record]:
    rows = []
    for index in indices:
        cell = cells[index]
        source_id, arm = cell["source_id"], cell["arm"]
        row = {"index": index, "source_id": source_id, "arm": arm, "state": "no_source"}
        rows.append(row)
        source = sources.get(source_id)
        if source is None:
            continue
        row["state"] = "skipped_not_ready"
        if index in reserved:
            continue
        if journal.collect(study, digest, source, arm)["state"] != "unclaimed":
            continue
        try:
            result = journal.play(study, digest, source, arm, respond)
        except StudyError as error:
            if error.args != ("cell_already_claimed",):
                raise
            continue
        row["state"] = result["state"]
        if row["state"] != "complete":
            break
    return rows


def run_cells(
    packet: _Record,
    digest: str,
    study: Path,
    indices: range,
    *,
    packet_path: Path | None = None,
) -> list[_Record]:
    try:
        register, ledger = _read_register(study, digest)
        cells = register["cells"]
        if not isinstance(indices, range) or (
            indices
            and (
                min(indices[0], indices[-1]) < 0
                or max(indices[0], indices[-1]) >= len(cells)
            )
        ):
            raise StudyError("unregistered_index")
        raw = files._encode(packets._validate(packet))
        if packet_path is None:
            if sha256(raw).hexdigest() != digest:
                raise StudyError("packet_content_changed")
            checked = packets._validate(packets._decode(raw, integers_only=True))
        else:
            checked = packets.load(packet_path, digest)
            if files._encode(checked) != raw:
                raise StudyError("packet_content_changed")
        if _hashes(register["transport"]) != register["code_sha256"]:
            raise StudyError("registered_sources_changed")
        direct = ledger is not None
        if direct:
            _measured(digest)
        reservation_scan = (
            nullcontext(set())
            if ledger is None
            else _direct_reservations(ledger, digest, cells)
        )
        sources = {source["source_id"]: source for source in checked["sources"]}
        with reservation_scan as reserved:
            return _run_pending(
                study,
                digest,
                cells,
                sources,
                indices,
                reserved,
                _callback(study, direct=direct),
            )
    except StudyError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError):
        raise StudyError("invalid_run_inputs") from None
