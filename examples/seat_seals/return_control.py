import argparse
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, override

from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import SeatResult
from harbor.trial.seats import SeatsTrial
from harbor.trial.trial import Trial

from examples.native_evidence import Invalid
from examples.native_study import (
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    native,
    normalize,
    require,
    task_files_digest,
    trial_request,
    write_exclusive,
)
from examples.seat_checkpoints import observe
from examples.seat_checkpoints.control_seat import ControlSeat
from examples.seat_checkpoints.gates import Qualification, until
from examples.seat_handoffs.native_records import read_record
from examples.seat_seals.contract import (
    CONTROL_IMPORT,
    PEER,
    RULE,
    TARGET,
    control_team,
    environment,
)
from examples.seat_seals.run import (
    CONTROL_INTERVAL,
    MAX_SAMPLES,
    TIMEOUT_SECONDS,
    reserve,
    trusted_task,
)
from examples.seat_seals.seal_host import attach_seal
from examples.shift_board.plan import FIXTURES, TICKET

REGISTRATION_SCHEMA = "harbor.seat-seal.return-check/v1"
WITNESS_SCHEMA = "harbor.seat-seal.return-witness/v1"
WITNESS_NAME = "return-witness.json"
HOST_PREFIX = "harbor.seat-cut.host/"
CASE_FIELDS = frozenset({"id", "ticket", "observer", "exit", "fixture", "seed", "mode"})
CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
OBSERVERS = ("v1", "v2")
EXITS = ("return", "cancel")
SEED = 17
MODE = "seal"
POLICY = "preferred"
MAX_CASES = 64
MAX_REGISTRATION_BYTES = 1 << 20
MAX_ORDER = 64
LIVE_STATES = frozenset({"not_started", "running"})
RELEASE_MARKER = "seats/dispatcher/seat-cut-control/release-before_decide"

assert TARGET == "planner" and PEER == "dispatcher" and MODE == "seal"


def load_case(path: Path, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = read_record(path, MAX_REGISTRATION_BYTES)
    require(record is not None, f"registration {path} does not exist")
    study = as_record(record, "registration")
    require(study.get("schema") == REGISTRATION_SCHEMA, "registration schema differs")
    cases = as_list(study.get("cases"), "cases", 1, MAX_CASES)
    ids, tickets = set(), set()
    for raw in cases:
        case = as_record(raw, "case", CASE_FIELDS)
        ids.add(as_text(case["id"], "case id", CASE_ID))
        tickets.add(as_text(case["ticket"], "case ticket", TICKET))
        as_choice(case["observer"], "case observer", OBSERVERS)
        as_choice(case["exit"], "case exit", EXITS)
        as_choice(case["fixture"], "case fixture", FIXTURES)
        as_int(case["seed"], "case seed", SEED, SEED)
        require(case["mode"] == MODE, "every return check runs the seal mode")
    require(len(ids) == len(cases) == len(tickets), "case ids or tickets repeat")
    matched = [case for case in cases if case["id"] == case_id]
    require(len(matched) == 1, f"case {case_id!r} is not registered")
    return study, matched[0]


def world_cell(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket": case["ticket"],
        "seed": case["seed"],
        "fixture": case["fixture"],
        "rule": RULE,
        "mode": case["mode"],
    }


def build_config(
    args: argparse.Namespace, study: dict[str, Any], case: dict[str, Any]
) -> TrialConfig:
    require(
        observe.HOST_SCHEMA == HOST_PREFIX + case["observer"],
        f"loaded observer emits {observe.HOST_SCHEMA}, not {case['observer']}",
    )
    require(
        "execution_disposition" in SeatResult.model_fields,
        "native core lacks execution_disposition",
    )
    harbor = native()
    task = args.task.resolve(strict=True)
    root = args.trials.resolve(strict=True)
    require(task.is_absolute() and root.is_absolute(), "use absolute paths")
    files = trusted_task(harbor, study, task, root)
    team = [normalize(harbor, member) for member in control_team(POLICY)]
    require(
        all(m["import_path"] == CONTROL_IMPORT and "name" not in m for m in team)
        and all("model_name" not in m for m in team),
        "return checks start scripted control seats only",
    )
    request = trial_request(
        harbor, task, root, case["ticket"], team, environment(world_cell(case))
    )
    require(
        task_files_digest(task, study["task"]) == files,
        "task changed before the trial started",
    )
    return TrialConfig.model_validate(request)


def check_seats(trial: Trial) -> SeatsTrial:
    if not isinstance(trial, SeatsTrial):
        raise TypeError("return check did not create a native SeatsTrial")
    seats = {seat.name: seat for seat in trial.seats}
    require(set(seats) == {TARGET, PEER}, "return check needs planner and dispatcher")
    for name, seat in seats.items():
        agent = seat.agent
        require(
            seat.under_test
            and type(agent) is ControlSeat
            and agent.role == name
            and agent.model_name is None,
            f"{name} is not an under-test scripted ControlSeat",
        )
    planner, dispatcher = seats[TARGET].agent, seats[PEER].agent
    require(
        planner.policy == "publish" and planner.gates == ("after_publish",),
        "planner policy or gates differ",
    )
    require(
        dispatcher.policy == POLICY
        and dispatcher.gates == ("before_decide", "after_decide"),
        "dispatcher policy or gates differ",
    )
    return trial


def _outcome(error: BaseException | None) -> str:
    return "returned" if error is None else f"raised:{type(error).__name__}"


class PlannerExit:
    def __init__(self, agent: ControlSeat, declared: str, order: list[str]) -> None:
        assert declared in EXITS and type(agent) is ControlSeat
        self.stock: Callable[..., Awaitable[None]] = agent.run
        self.exit = declared
        self.order = order
        self.witness: dict[str, Any] = {
            "entry_cancelling": None,
            "stock": None,
            "action": None,
            "action_cancelling": None,
            "done": None,
            "done_cancelling": None,
        }
        agent.run = self.run

    def log(self, event: str) -> None:
        if len(self.order) < MAX_ORDER:
            self.order.append(event)

    def _done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            done = "cancelled"
        else:
            done = _outcome(task.exception())
        self.witness["done"] = done
        self.witness["done_cancelling"] = task.cancelling()
        self.log(f"planner:done:{done}")

    async def run(self, *, instruction: str, environment: Any, context: Any) -> None:
        task = asyncio.current_task()
        assert task is not None and self.witness["entry_cancelling"] is None
        self.witness["entry_cancelling"] = task.cancelling()
        task.add_done_callback(self._done)
        self.log("planner:entry")
        try:
            await self.stock(
                instruction=instruction, environment=environment, context=context
            )
        except BaseException as error:
            self.witness["stock"] = _outcome(error)
            self.log(f"planner:stock:{self.witness['stock']}")
            raise
        self.witness["stock"] = "returned"
        self.log("planner:stock:returned")
        self.witness["action"] = self.exit
        self.witness["action_cancelling"] = task.cancelling()
        self.log(f"planner:action:{self.exit}")
        if self.exit == "cancel":
            raise asyncio.CancelledError()


class ReturnGates(Qualification):
    SCHEDULES = frozenset({"held-early"})

    def __init__(self, trial: SeatsTrial, planner: PlannerExit) -> None:
        super().__init__(trial, "held-early")
        self.planner = planner
        self.terminal_sample: dict[str, Any] | None = None
        self.peer_at_release: str | None = None
        self.release_marker: str | None = None
        self.status = "not_started"

    def step(self, event: str) -> None:
        self.trace.append(event)
        self.planner.log(event)

    async def arrival(self, role: str, stage: str) -> None:
        await until(self.marker(role, "arrived", stage).is_file)
        self.step(f"arrived:{role}:{stage}")

    def release(self, role: str, stage: str) -> None:
        assert self.marker(role, "arrived", stage).is_file()
        self.marker(role, "release", stage).touch(mode=0o600, exist_ok=False)
        self.step(f"release:{role}:{stage}")

    async def witness(self, state: str) -> None:
        assert state == "running"
        await super().witness(state)
        self.planner.log(self.trace[-1])

    def native_peer(self) -> str:
        states = observe.role_states(self.trial.result.seat_results or [], TARGET, PEER)
        return states["peer"]

    def _terminal(self) -> bool:
        rows = self.samples()
        if any(row["ack"] == "failed" for row in rows):
            raise RuntimeError("the world never acknowledged a host sample")
        for index, row in enumerate(rows):
            before = row["before"]
            if (
                row["ack"] == "ok"
                and row["after"] == before
                and before["peer"] == "running"
                and before["target"] not in LIVE_STATES
            ):
                self.terminal_sample = {"index": index, **row}
                return True
        return False

    @override
    async def play(self) -> None:
        planner, before, after = "after_publish", "before_decide", "after_decide"
        await self.arrival(TARGET, planner)
        await self.arrival(PEER, before)
        await self.witness("running")
        self.release(TARGET, planner)
        await until(lambda: self.planner.witness["done"] is not None)
        self.step("planner:done")
        await until(self._terminal)
        assert self.terminal_sample is not None
        self.step(f"terminal:{self.terminal_sample['before']['target']}:running")
        self.peer_at_release = self.native_peer()
        if self.peer_at_release != "running":
            raise RuntimeError("the dispatcher left before its decision release")
        self.release(PEER, before)
        self.release_marker = RELEASE_MARKER
        await self.arrival(PEER, after)
        self.release(PEER, after)

    @override
    async def finish(self) -> bool:
        if self.job is not None:
            if not self.job.done():
                self.job.cancel()
            try:
                await self.job
                self.status = "complete"
            except asyncio.CancelledError:
                self.status = "interrupted"
            except (TimeoutError, RuntimeError, KeyError, ValueError, OSError) as error:
                self.status = type(error).__name__
        return self.status == "complete"

    def record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trace": list(self.trace),
            "terminal_sample": self.terminal_sample,
            "peer_at_release": self.peer_at_release,
            "release_marker": self.release_marker,
        }


def host_schema(trial: SeatsTrial) -> str:
    document = json.loads((trial.paths.trial_dir / observe.HOST_DOCUMENT).read_bytes())
    schema = document.get("schema")
    require(type(schema) is str, "host journal has no schema")
    return schema


def witness_record(
    case: dict[str, Any],
    trial: SeatsTrial,
    schema: str,
    planner: PlannerExit,
    gates: ReturnGates,
) -> dict[str, Any]:
    return {
        "schema": WITNESS_SCHEMA,
        "case": case["id"],
        "ticket": case["ticket"],
        "trial_id": str(trial.id),
        "declared": {key: case[key] for key in sorted(CASE_FIELDS - {"id", "ticket"})},
        "observer": {
            "host_schema": schema,
            "source": str(Path(observe.__file__).resolve()),
        },
        "planner": dict(planner.witness),
        "gate": gates.record(),
        "order": list(planner.order),
    }


async def run(args: argparse.Namespace) -> bool:
    study, case = load_case(args.registration, args.case)
    config = build_config(args, study, case)
    directory = config.trials_dir / config.trial_name
    reserve(directory)
    trial = check_seats(await Trial.create(config))
    seats = {seat.name: seat for seat in trial.seats}
    order: list[str] = []
    planner = PlannerExit(seats[TARGET].agent, case["exit"], order)
    attach_seal(
        trial,
        target=TARGET,
        peer=PEER,
        interval_seconds=CONTROL_INTERVAL,
        max_samples=MAX_SAMPLES,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    schema = host_schema(trial)
    require(schema == HOST_PREFIX + case["observer"], f"host journal is {schema}")
    gates = ReturnGates(trial, planner)
    try:
        result = await trial.run()
    finally:
        complete = await gates.finish()
        write_exclusive(
            directory / WITNESS_NAME,
            witness_record(case, trial, schema, planner, gates),
            0o600,
        )
    print(
        json.dumps(
            {
                "case": case["id"],
                "ticket": case["ticket"],
                "trial_id": str(trial.id),
                "schedule": gates.status,
                "planner_done": planner.witness["done"],
                "reward_valid": result.reward_valid,
            }
        )
    )
    return complete and planner.witness["action"] == case["exit"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run exactly one registered native return check. Never retries."
    )
    parser.add_argument("registration", type=Path)
    parser.add_argument("task", type=Path)
    parser.add_argument("trials", type=Path)
    parser.add_argument("case")
    args = parser.parse_args()
    try:
        ok = asyncio.run(run(args))
    except (FileExistsError, ValueError, Invalid) as error:
        parser.exit(2, f"return_control: {error}\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
