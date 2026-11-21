import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import Any

from .. import capsule
from ..attention.protocol import _open_pair
from ..turn_fork import analysis as fork_analysis
from ..turn_fork import journal as native
from ..turn_fork import packet as packets
from ..turn_fork import run as fork_run
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
from . import inputs, journal

type _Record = dict[str, Any]
type _Respond = Callable[[str, str], route_run.Answer]

_SCHEMA = "seatlab.decision-cross-register/v1"
_BOARD_SCHEMA = "seatlab.decision-cross-board/v1"
_REPORT_SCHEMA = "seatlab.decision-cross-report/v1"
_COMMITMENT = Path(__file__).resolve().parents[1] / "results" / "decision-cross.sha256"
_REGISTER = "register.json"
_COMMANDS = "commands.json"
_LIMIT = 64 * 1024
_TRANSPORTS = ("direct", "commands")
_SLOTS = tuple(
    (f"s{seed}-r{rep}", sender, arm)
    for seed in packets.SEEDS
    for rep in packets.REPETITIONS
    for sender in _TRANSPORTS
    for arm in ("aligned", "repeat")
)
_SOURCES = frozenset(source for source, _, _ in _SLOTS)
_INVALID = (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError)


@dataclass(frozen=True, slots=True)
class _Register:
    public: _Record
    board: _Record
    ledger: _Record | None
    dispatch: _Record | None


@dataclass(frozen=True, slots=True)
class _Cell:
    source: _Record
    opened: tuple[str, str]
    prompt: str
    claim: _Record

    @property
    def index(self) -> int:
        return self.claim["draw"]["index"]

    @property
    def run(self) -> str:
        return self.claim["run"]


def _hashes(transport: str) -> dict[str, str]:
    hashes = fork_run._hashes(transport)
    hashes["turn_fork_runner"] = hashes.pop("runner")
    hashes["runner"] = sha256(Path(__file__).read_bytes()).hexdigest()
    modules: list[ModuleType] = [capsule, fork_analysis, inputs, journal]
    if transport == "direct":
        modules.append(route_pending)
    for module in modules:
        if module.__file__ is None:
            raise StudyError("source_unavailable")
        hashes[module.__name__] = sha256(Path(module.__file__).read_bytes()).hexdigest()
    return hashes


def _digest(document: object) -> str:
    return sha256(files._encode(document)).hexdigest()


def _observations(board: _Record) -> dict[str, _Record]:
    observations = board["observations"]
    if not isinstance(observations, list) or len(observations) > len(_SLOTS):
        raise StudyError("invalid_board")
    indexed: dict[str, _Record] = {}
    previous = ("", "")
    for row in observations:
        obs = packets._object(row, "source_id", "obs_id", "prompt_sha256", "opened")
        source, prompt, opened = obs["source_id"], obs["prompt_sha256"], obs["opened"]
        if source not in _SOURCES or not files._is_digest(prompt):
            raise StudyError("invalid_board")
        obs_id = f"{source}-{prompt[:12]}"
        if (
            obs["obs_id"] != obs_id
            or obs_id in indexed
            or (source, prompt) <= previous
            or not isinstance(opened, list)
        ):
            raise StudyError("invalid_board")
        _open_pair(opened)
        indexed[obs_id] = obs
        previous = (source, prompt)
    return indexed


def _validate_selections(board: _Record, observations: dict[str, _Record]) -> None:
    rows = board["selections"]
    if not isinstance(rows, list) or len(rows) != len(_SLOTS):
        raise StudyError("invalid_board")
    selected: set[str] = set()
    for row, slot in zip(rows, _SLOTS, strict=True):
        item = packets._object(
            row, "source_id", "sender", "arm", "obs_id", "sender_response_sha256"
        )
        if (item["source_id"], item["sender"], item["arm"]) != slot:
            raise StudyError("invalid_board")
        obs_id, response = item["obs_id"], item["sender_response_sha256"]
        if obs_id is None and response is None:
            continue
        if (
            not isinstance(obs_id, str)
            or not files._is_digest(response)
            or obs_id not in observations
            or observations[obs_id]["source_id"] != slot[0]
        ):
            raise StudyError("invalid_board")
        selected.add(obs_id)
    if selected != observations.keys():
        raise StudyError("invalid_board")


def _board_draws(value: _Record, digest: str) -> list[_Record]:
    board = packets._object(
        value,
        "schema",
        "packet_sha256",
        "direct_report_sha256",
        "commands_report_sha256",
        "observations",
        "selections",
    )
    if board["schema"] != _BOARD_SCHEMA or not all(
        files._is_digest(board[key])
        for key in ("packet_sha256", "direct_report_sha256", "commands_report_sha256")
    ):
        raise StudyError("invalid_board")
    observations = _observations(board)
    _validate_selections(board, observations)
    if not files._is_digest(digest) or _digest(board) != digest:
        raise StudyError("board_content_changed")
    return inputs.draws(board)


def _read_bytes(root: int, name: str, *, mode: int | None = None) -> bytes:
    with files._regular(root, name, mode) as stream:
        raw = stream.read(_LIMIT + 1)
    if not raw or len(raw) > _LIMIT:
        raise StudyError("invalid_private_record")
    return raw


def _command_spec(raw: bytes) -> _Record:
    config = packets._object(files._object(raw), "dispatch")
    spec = packets._object(config["dispatch"], "argv", "env")
    argv, names = spec["argv"], spec["env"]
    if (
        not isinstance(argv, list)
        or not isinstance(names, list)
        or any(
            not isinstance(name, str)
            or not seat_command._allowed_env_name(name)
            or name.upper() in fork_run._FORBIDDEN_ENV
            for name in names
        )
    ):
        raise StudyError("invalid_command_config")
    if len(set(names)) != len(names):
        raise StudyError("invalid_command_config")
    seat_command.CommandSeat(argv)
    return spec


def _measured(digest: str) -> None:
    try:
        with files._directory(_COMMITMENT.parent) as parent:
            with files._regular(parent, _COMMITMENT.name) as stream:
                raw = stream.read(66)
    except (OSError, StudyError):
        raise StudyError("direct_board_not_registered") from None
    expected = digest.encode("ascii")
    if raw not in (expected, expected + b"\n"):
        raise StudyError("direct_board_not_registered")


def _reservations(root: Path, digest: str, draws: list[_Record]) -> set[int]:
    registered = {
        (inputs.run_id(digest, draw["obs_id"], draw["draw"]), "dispatch"): draw["index"]
        for draw in draws
    }
    return route_pending._ledger_blocks(root, f"dx-{digest[:12]}-", registered)


def _live_ledger(ledger: _Record, fd: int) -> Path:
    path = Path(ledger["path"])
    try:
        if fork_run._identity(fd) == ledger["directory_id"]:
            with files._directory(path) as current:
                if fork_run._ledger_identity(path, current) == ledger:
                    return path
    except (OSError, ValueError):
        pass
    raise StudyError("original_spend_directory_required")


@contextmanager
def _ledger_lock(ledger: _Record, mode: int) -> Iterator[int]:
    with ExitStack() as stack:
        try:
            fd = stack.enter_context(files._directory(Path(ledger["path"])))
            stack.enter_context(native._lock(fd, mode))
        except (OSError, ValueError):
            raise StudyError("original_spend_directory_required") from None
        _live_ledger(ledger, fd)
        yield fd


def _settings(
    transport: str,
    config: Path | None,
    spend_dir: Path | None,
    digest: str,
    draws: list[_Record],
) -> tuple[_Record | None, _Record | None]:
    if transport == "commands" and config is not None and spend_dir is None:
        config = files._absolute(config)
        with files._directory(config.parent) as parent:
            spec = _command_spec(_read_bytes(parent, config.name))
        if len(files._encode({"dispatch": spec})) > _LIMIT:
            raise StudyError("invalid_command_config")
        return {"dispatch": spec}, None
    if transport == "direct" and config is None and spend_dir is not None:
        _measured(digest)
        root = fork_run._physical(spend_dir)
        with files._directory(root) as fd, native._lock(fd, fcntl.LOCK_SH):
            ledger = fork_run._ledger_identity(root, fd)
            _live_ledger(ledger, fd)
            _reservations(root, digest, draws)
            _live_ledger(ledger, fd)
        return None, ledger
    raise StudyError("invalid_transport_settings")


def seal(
    study: Path,
    board: _Record,
    digest: str,
    transport: str,
    config: Path | None,
    spend_dir: Path | None,
) -> _Record:
    try:
        study = files._study_path(fork_run._physical(study))
        draws = _board_draws(board, digest)
        commands, ledger = _settings(transport, config, spend_dir, digest, draws)
        register = {
            "schema": _SCHEMA,
            "board_sha256": digest,
            "transport": transport,
            "code_sha256": _hashes(transport),
            "command_config_sha256": _digest(commands)
            if commands is not None
            else None,
            "draws": draws,
        }
        with files._directory(study.parent) as parent:
            try:
                os.mkdir(study.name, 0o700, dir_fd=parent)
            except FileExistsError:
                raise StudyError("study_already_exists") from None
            fd = os.open(study.name, files._DIRECTORY, dir_fd=parent)
            try:
                files._private(fd, mode=0o700)
                record = {
                    "register": register,
                    "study_id": fork_run._identity(fd),
                    "board": board,
                    "ledger": ledger,
                }
                if len(files._encode(record)) > _LIMIT:
                    raise StudyError("invalid_register")
                if commands is not None:
                    files._publish(fd, _COMMANDS, commands, mode=0o400)
                files._publish(fd, _REGISTER, record, mode=0o400)
            finally:
                os.close(fd)
        return register
    except StudyError:
        raise
    except _INVALID:
        raise StudyError("invalid_study_settings") from None


def _validate_ledger(value: object) -> _Record:
    ledger = packets._object(value, "path", "directory_id", "database_id")
    path = ledger["path"]
    if (
        not isinstance(path, str)
        or not 0 < len(path) <= 4096
        or "\0" in path
        or str(fork_run._physical(Path(path))) != path
    ):
        raise StudyError("invalid_register")
    for key in ("directory_id", "database_id"):
        pair = ledger[key]
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(
                type(number) is not int or not 0 <= number < 1 << 64 for number in pair
            )
        ):
            raise StudyError("invalid_register")
    return ledger


def _load(root: int, digest: str) -> _Register:
    raw = _read_bytes(root, _REGISTER, mode=0o400)
    record = packets._object(
        files._object(raw), "register", "study_id", "board", "ledger"
    )
    register = packets._object(
        record["register"],
        "schema",
        "board_sha256",
        "transport",
        "draws",
        "code_sha256",
        "command_config_sha256",
    )
    if (
        files._encode(record) != raw
        or files._encode(record["study_id"]) != files._encode(fork_run._identity(root))
        or register["schema"] != _SCHEMA
        or register["board_sha256"] != digest
        or register["transport"] not in _TRANSPORTS
    ):
        raise StudyError("invalid_register")
    board: _Record = record["board"]
    draws = _board_draws(board, digest)
    if files._encode(register["draws"]) != files._encode(draws):
        raise StudyError("invalid_register")
    transport = register["transport"]
    if register["code_sha256"] != _hashes(transport):
        raise StudyError("registered_sources_changed")
    config_digest = register["command_config_sha256"]
    ledger = dispatch = None
    if transport == "commands" and record["ledger"] is None:
        config_raw = _read_bytes(root, _COMMANDS, mode=0o400)
        if sha256(config_raw).hexdigest() != config_digest:
            raise StudyError("command_config_changed")
        dispatch = _command_spec(config_raw)
        if files._encode({"dispatch": dispatch}) != config_raw:
            raise StudyError("invalid_command_config")
    elif transport == "direct" and config_digest is None:
        ledger = _validate_ledger(record["ledger"])
    else:
        raise StudyError("invalid_register")
    return _Register(register, board, ledger, dispatch)


@contextmanager
def _registered(study: Path, digest: str) -> Iterator[tuple[int, _Register]]:
    with native._study(fork_run._physical(study)) as root:
        try:
            register = _load(root, digest)
        except StudyError:
            raise
        except _INVALID:
            raise StudyError("invalid_register") from None
        yield root, register


def load(study: Path, digest: str) -> _Record:
    with _registered(study, digest) as (_, register):
        return register.public


def _cells(
    packet: _Record, board: _Record, register: _Register, packet_path: Path | None
) -> list[_Cell]:
    if _digest(board) != register.public["board_sha256"]:
        raise StudyError("board_content_changed")
    packet = packets._validate(packet)
    encoded = files._encode(packet)
    packet_digest = register.board["packet_sha256"]
    if packet_path is None:
        if (
            len(encoded) > packets._MAX_PACKET_BYTES
            or sha256(encoded).hexdigest() != packet_digest
        ):
            raise StudyError("packet_content_changed")
        packet = packets._validate(packets._decode(encoded, integers_only=True))
    else:
        packet = packets.load(packet_path, packet_digest)
        if files._encode(packet) != encoded:
            raise StudyError("packet_content_changed")
    sources = inputs.source_index(packet)
    observations = {obs["obs_id"]: obs for obs in register.board["observations"]}
    cells: list[_Cell] = []
    for draw in register.public["draws"]:
        source = sources[draw["source_id"]]
        claim, prompt, opened = journal._prepare(
            register.public["board_sha256"], draw, source, observations[draw["obs_id"]]
        )
        cells.append(_Cell(source, opened, prompt, claim))
    return cells


def _occupied(root: int, cell: _Cell) -> bool:
    return any(journal._exists(root, name) for name in journal._names(cell.claim))


def _validate_usage(receipt: _Record, transport: str) -> None:
    if receipt["status"] == "received":
        usage = receipt["usage"]
        if transport == "direct" and usage is not None:
            try:
                deepseek._served_model(usage["served_model"])
            except deepseek.SeatUnavailable:
                raise StudyError("invalid_evidence") from None
        elif transport == "direct" or usage is not None:
            raise StudyError("invalid_evidence")


def _collect(root: int, cell: _Cell, transport: str) -> _Record:
    claim_name, turn_name = journal._names(cell.claim)
    claimed = journal._exists(root, claim_name)
    has_turn = journal._exists(root, turn_name)
    if (
        claimed
        and files._encode(native._read(root, claim_name)) != files._encode(cell.claim)
    ) or (not claimed and has_turn):
        raise StudyError("invalid_evidence")
    receipt = native._read(root, turn_name) if has_turn else None
    row = journal._row(cell.claim, cell.source, cell.opened, receipt, claimed=claimed)
    if row["turn"] is not None:
        _validate_usage(row["turn"], transport)
    return row


def _claim(root: int, cell: _Cell, guard: Callable[[], None] | None) -> bool:
    claim_name, _ = journal._names(cell.claim)
    with native._lock(root, fcntl.LOCK_EX):
        if _occupied(root, cell):
            return False
        if guard is not None:
            guard()
        files._publish(root, claim_name, cell.claim, mode=0o600)
    return True


def _respond_commands(dispatch: _Record) -> _Respond:
    seat: seat_command.CommandSeat | None = None

    def respond(prompt: str, _run: str) -> route_run.Answer:
        nonlocal seat
        if seat is None:
            seat = seat_command._configured_seat(dispatch)
        return route_run.Answer(seat(prompt))

    return respond


def _admission(
    ledger: _Record, fd: int, digest: str
) -> tuple[Callable[[], None], Callable[[], bool]]:
    admitted = False

    def guard() -> None:
        _measured(digest)
        path = _live_ledger(ledger, fd)
        env = os.environ.get("SEATLAB_SPEND_DIR")
        bound = spend._HOST_LEDGER
        if (
            (not admitted and env != str(path))
            or (bound is not None and bound != path)
            or (admitted and bound is None and env != str(path))
        ):
            raise StudyError("original_spend_directory_required")

    def ready() -> bool:
        nonlocal admitted
        guard()
        if not admitted:
            try:
                route_cli._admit()
            except (Exception, SystemExit):
                return False
            admitted = True
            guard()
        return True

    return guard, ready


def _respond_direct(prompt: str, run: str) -> route_run.Answer:
    return route_cli._direct(prompt, run, "dispatch")


def _receive(cell: _Cell, transport: str, respond: _Respond) -> _Record:
    try:
        fields = native._received(respond(cell.prompt, cell.run))
        _validate_usage(fields, transport)
        return fields
    except (Exception, SystemExit):
        return native._FAILED


def _batch(
    study: Path,
    root: int,
    cells: list[_Cell],
    indices: range,
    reserved: set[int],
    transport: str,
    respond: _Respond,
    admission: tuple[Callable[[], None], Callable[[], bool]] | None = None,
) -> list[_Record]:
    guard, ready = admission if admission is not None else (None, None)
    rows: list[_Record] = []
    for index in indices:
        cell = cells[index]
        if index in reserved or not _claim(root, cell, guard):
            rows.append({"index": index, "state": "skipped_not_ready"})
            continue
        ready_to_call = ready is None or ready()
        if ready_to_call:
            with files._directory(study) as current:
                if fork_run._identity(current) != fork_run._identity(root):
                    raise StudyError("original_study_directory_required")
        fields = _receive(cell, transport, respond) if ready_to_call else native._FAILED
        receipt = journal._header(cell.claim) | fields
        with native._lock(root, fcntl.LOCK_EX):
            files._publish(root, journal._names(cell.claim)[1], receipt, mode=0o600)
        state = journal._row(
            cell.claim, cell.source, cell.opened, receipt, claimed=True
        )["state"]
        rows.append({"index": index, "state": state})
        if state == "incomplete":
            break
    return rows


def _indices(indices: range, count: int) -> None:
    if not isinstance(indices, range) or (
        indices and (not 0 <= indices[0] < count or not 0 <= indices[-1] < count)
    ):
        raise StudyError("unregistered_index")


def run_cells(
    packet: _Record,
    board: _Record,
    digest: str,
    study: Path,
    indices: range,
    *,
    packet_path: Path | None = None,
) -> list[_Record]:
    try:
        with _registered(study, digest) as (root, register):
            cells = _cells(packet, board, register, packet_path)
            _indices(indices, len(cells))
            ledger = register.ledger
            if ledger is None:
                assert register.dispatch is not None
                return _batch(
                    study,
                    root,
                    cells,
                    indices,
                    set(),
                    "commands",
                    _respond_commands(register.dispatch),
                )
            _measured(digest)
            with _ledger_lock(ledger, fcntl.LOCK_EX) as fd:
                path = _live_ledger(ledger, fd)
                reserved = _reservations(path, digest, register.public["draws"])
                _live_ledger(ledger, fd)
                return _batch(
                    study,
                    root,
                    cells,
                    indices,
                    reserved,
                    "direct",
                    _respond_direct,
                    _admission(ledger, fd, digest),
                )
    except StudyError:
        raise
    except _INVALID:
        raise StudyError("invalid_run_inputs") from None


def pending(
    packet: _Record,
    board: _Record,
    digest: str,
    study: Path,
    *,
    packet_path: Path | None = None,
) -> list[int]:
    try:
        with _registered(study, digest) as (root, register):
            cells = _cells(packet, board, register, packet_path)
            ledger = register.ledger
            reserved: set[int] = set()
            if ledger is not None:
                with files._directory(Path(ledger["path"])) as fd:
                    path = _live_ledger(ledger, fd)
                    reserved = _reservations(path, digest, register.public["draws"])
                    _live_ledger(ledger, fd)
            with native._lock(root, fcntl.LOCK_SH):
                return [
                    cell.index
                    for cell in cells
                    if cell.index not in reserved and not _occupied(root, cell)
                ]
    except StudyError:
        raise
    except _INVALID:
        raise StudyError("invalid_run_inputs") from None


def report(
    packet: _Record,
    board: _Record,
    digest: str,
    study: Path,
    *,
    packet_path: Path | None = None,
) -> _Record:
    try:
        with _registered(study, digest) as (root, register):
            cells = _cells(packet, board, register, packet_path)
            transport = register.public["transport"]
            with native._lock(root, fcntl.LOCK_SH):
                return {
                    "schema": _REPORT_SCHEMA,
                    "board_sha256": digest,
                    "transport": transport,
                    "rows": [_collect(root, cell, transport) for cell in cells],
                }
    except StudyError:
        raise
    except _INVALID:
        raise StudyError("invalid_run_inputs") from None
