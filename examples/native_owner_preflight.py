import importlib.util
import json
import logging
import os
import sys
import tempfile
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, NamedTuple

from .joint_reopen import study
from .native_owner_process import VARIABLE, OwnerFailure, owner_id, owner_program
from .native_study import require
from .native_tickets import World, _registration
from .shift_board import plan

SCHEMA = "harbor.native-owner-preflight/v1"
USAGE = (
    "usage: python -B -m examples.native_owner_preflight REGISTERED_MANIFEST [--json]"
)
EXAMPLES = Path(__file__).resolve().parent
READY = "ready"
OUTCOMES = ("ready", "adapter_unavailable", "program_unavailable")
EXIT_READY = 0
EXIT_UNAVAILABLE = 1
EXIT_INVALID = 2
EXIT_NO_OWNERS = 3
MAX_MEMBERS = 2 * study.MAX_TEAMS
COST_MAP = "LITELLM_LOCAL_MODEL_COST_MAP"
Record = dict[str, Any]
Seat = tuple[type, dict[str, str]]


class Unreadable(Exception):
    pass


class Owner(NamedTuple):
    label: str
    import_path: str
    load: Callable[[], Seat]
    roles: tuple[str, ...]


def _joint_seat() -> Seat:
    from .joint_reopen.owner_seat import OWNER_VARIABLES, JointOwnerSeat

    return JointOwnerSeat, OWNER_VARIABLES


def _shift_seat() -> Seat:
    from .shift_board.owner_io import OWNER_VARIABLES
    from .shift_board.owner_seat import OwnerSeat

    return OwnerSeat, OWNER_VARIABLES


OWNERS = {
    study.SCHEMA: Owner(
        "joint_reopen",
        study.OWNER_IMPORT_PATH,
        _joint_seat,
        tuple(sorted(study.SEATS)),
    ),
    plan.SCHEMA: Owner(
        "shift_board",
        "examples.shift_board.owner_seat:OwnerSeat",
        _shift_seat,
        plan.SEATS,
    ),
}


@contextmanager
def _sealed() -> Iterator[None]:
    disabled = logging.root.manager.disable
    cost_map = os.environ.get(COST_MAP)
    logging.disable(logging.CRITICAL)
    os.environ[COST_MAP] = "True"
    try:
        with (
            open(os.devnull, "w") as sink,
            redirect_stdout(sink),
            redirect_stderr(sink),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore")
            yield
    finally:
        logging.disable(disabled)
        if cost_map is None:
            os.environ.pop(COST_MAP, None)
        else:
            os.environ[COST_MAP] = cost_map


def _enrolled(registration: Record) -> list[Record]:
    members: dict[str, Record] = {}
    for label in sorted({cell["team"] for cell in registration["cells"]}):
        for member in registration["teams"][label]:
            members.setdefault(json.dumps(member, sort_keys=True), member)
    require(len(members) <= MAX_MEMBERS, "registration enrolls too many participants")
    return list(members.values())


def _check_owner(member: Record) -> None:
    require(set(member) == study.OWNER_FIELDS, "owner seat fields differ")
    require(member["skip_model_parsing"] is True, "owner seat parses its model")
    require(owner_id(member["model_name"]), "owner seat identifier is malformed")


def _load(path: str) -> tuple[World, Record]:
    try:
        Path(path).lstat()
        world, registration, _ = _registration(path, None)
    except OSError:
        raise Unreadable() from None
    return world, registration


def _local(module_name: str) -> bool:
    try:
        spec = importlib.util.find_spec(module_name)
    except (Exception, SystemExit):
        return False
    origin = None if spec is None else spec.origin
    return type(origin) is str and Path(origin).resolve().is_relative_to(EXAMPLES)


def _adapter(owner: Owner, member: Record) -> str | None:
    try:
        from harbor.agents.factory import AgentFactory
        from harbor.models.trial.config import Participant
    except (Exception, SystemExit):
        return None
    participant = Participant.model_validate(member)
    normalized = participant.model_dump(mode="json", exclude_none=True)
    require(normalized == member, "native normalization changes the owner seat")
    if not _local(owner.import_path.partition(":")[0]):
        return None
    with tempfile.TemporaryDirectory(prefix="owner-preflight-") as logs:
        try:
            agent = AgentFactory.create_agent_from_config(
                participant, logs_dir=Path(logs)
            )
            seat, variables = owner.load()
        except (Exception, SystemExit):
            return None
    variable = variables.get(member["seat"])
    if type(agent) is not seat or type(variable) is not str:
        return None
    return variable if VARIABLE.fullmatch(variable) else None


def _program(role: str, variable: str) -> str:
    try:
        owner_program(role, variable)
    except OwnerFailure as failure:
        return failure.reason
    return READY


def _role(counts: Record, program: str | None) -> Record:
    external = counts["external"]
    assert external == sum(counts[outcome] for outcome in OUTCOMES)
    if external == 0:
        status, program = "not_external", "not_applicable"
    else:
        status = READY if counts[READY] == external else "unavailable"
    return {**counts, "program": program or "not_checked", "status": status}


def _probe(owner: Owner | None, registration: Record) -> Record:
    blank = {"external": 0, "not_external": 0, **dict.fromkeys(OUTCOMES, 0)}
    members = _enrolled(registration)
    roles = (
        owner.roles if owner else tuple(sorted({member["seat"] for member in members}))
    )
    counts = {role: dict(blank) for role in roles}
    externals: list[Record] = []
    for member in members:
        require(member["seat"] in counts, "participant seat is unknown")
        require(
            owner is not None or not member.get("import_path"), "unregistered adapter"
        )
        if owner is not None and member.get("import_path") == owner.import_path:
            _check_owner(member)
            externals.append(member)
        else:
            counts[member["seat"]]["not_external"] += 1
    programs: dict[str, str] = {}
    for member in externals:
        assert owner is not None
        role = member["seat"]
        variable = _adapter(owner, member)
        if variable is not None and role not in programs:
            programs[role] = _program(role, variable)
        if variable is None:
            outcome = "adapter_unavailable"
        else:
            outcome = READY if programs[role] == READY else "program_unavailable"
        counts[role]["external"] += 1
        counts[role][outcome] += 1
    return {role: _role(counts[role], programs.get(role)) for role in roles}


def preflight(path: str) -> Record:
    world, registration = _load(path)
    owner = OWNERS.get(world.schema)
    roles = _probe(owner, registration)
    keys = ("external", "not_external", *OUTCOMES)
    total = {key: sum(role[key] for role in roles.values()) for key in keys}
    if total["external"] == 0:
        status = "no_external_owners"
    else:
        status = READY if total[READY] == total["external"] else "unavailable"
    return {
        "schema": SCHEMA,
        "study": owner.label
        if owner
        else world.task_name.rsplit("/", 1)[-1].replace("-", "_"),
        "status": status,
        "roles": roles,
        "total": total,
    }


def _arguments(argv: list[str]) -> tuple[str, bool] | None:
    paths = [argument for argument in argv if argument != "--json"]
    if len(paths) != 1 or not 1 <= len(argv) <= 2 or paths[0].startswith("-"):
        return None
    return paths[0], len(argv) == 2


def _text(result: Record) -> str:
    keys = ("external", "not_external", *OUTCOMES)
    lines = [f"native owner preflight: {result['status']}"]
    if "roles" in result:
        for role, counts in result["roles"].items():
            fields = " ".join(f"{key}={counts[key]}" for key in keys)
            lines.append(
                f"{role}: {counts['status']} {fields} program={counts['program']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(sys.argv[1:] if argv is None else argv)
    if arguments is None:
        print(USAGE, file=sys.stderr)
        return EXIT_INVALID
    path, as_json = arguments
    try:
        with _sealed():
            result = preflight(path)
    except Unreadable:
        result = {"schema": SCHEMA, "status": "registration_unreadable"}
    except ValueError:
        result = {"schema": SCHEMA, "status": "registration_invalid"}
    except Exception:
        result = {"schema": SCHEMA, "status": "preflight_failed"}
    code = {
        READY: EXIT_READY,
        "unavailable": EXIT_UNAVAILABLE,
        "no_external_owners": EXIT_NO_OWNERS,
    }.get(result["status"], EXIT_INVALID)
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(_text(result), file=sys.stdout if code != EXIT_INVALID else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
