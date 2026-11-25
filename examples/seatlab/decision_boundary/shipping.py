from pathlib import Path
from types import ModuleType

from ...native_study import require
from ...ship_handover import receiver_options as receiver
from .core import World

_RELIEF = (
    Path(__file__).resolve().parents[2] / "tasks/ship-handover/instructions/relief.md"
)


def _world(
    module: ModuleType, task: receiver.Task, cell: receiver.Cell, instruction: bytes
) -> World:
    native_world = receiver.open_world(module, task, cell)
    status, _ = native_world.handle(
        "outgoing", "POST", "/handoff", {"reference": receiver.X}
    )
    require(status == 200, "reference handoff refused")
    reads = [
        (path, *native_world.handle("relief", "GET", path, {}))
        for path in receiver.READS
    ]
    require(all(status == 200 for _, status, _ in reads), "relief read refused")
    stamp = receiver.view_digest(reads)
    framed = b"".join(
        f"GET {path} {status}\n".encode("ascii") + body + b"\n"
        for path, status, body in reads
    )
    observation = len(instruction).to_bytes(4, "big") + instruction + framed
    scores = {}
    for action in receiver.available(task, cell.view):
        trial = receiver.trial(module, task, cell, action)
        if (
            trial.problem is not None
            or trial.digest != stamp
            or type(trial.grade) is not int
        ):
            raise ValueError("stock native verifier did not confirm a finite rule")
        scores[action] = trial.grade
    require(
        len(scores) == (4 if cell.view == "received" else 3),
        "unexpected ship rule space",
    )
    return World(f"{cell.state}/{cell.offer}", 1, observation, scores)


def studies(seed: int) -> dict[str, tuple[int, list[World]]]:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid development seed")
    module = receiver.verified_world()
    task = next(task for task in receiver.TASKS if task.name == "stock")
    instruction = _RELIEF.read_bytes()
    specs = (
        ("original-delivered", "deliver", ("original",)),
        ("original-withheld", "withhold", ("original",)),
        ("ambiguous-delivered", "deliver", ("original", "draft")),
    )
    cells = {
        receiver.Cell(state, offer, channel)
        for _, channel, offers in specs
        for offer in offers
        for state in task.states
    }
    worlds = {cell: _world(module, task, cell, instruction) for cell in sorted(cells)}
    return {
        f"ship/{name}": (
            1,
            [
                worlds[receiver.Cell(state, offer, channel)]
                for state in task.states
                for offer in offers
            ],
        )
        for name, channel, offers in specs
    }
