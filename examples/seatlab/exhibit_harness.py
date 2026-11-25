import hashlib
from pathlib import Path

from ..native_study import MAX_TASK_BYTES, _task_file_sha

HARNESS_PATHS = (
    "examples/native_study.py",
    "examples/seat_checkpoints/materialize.py",
    "examples/seatlab/decision_receipt/control_inputs.py",
    "examples/seatlab/exhibit_author_prepare.py",
    "examples/seatlab/exhibit_authored.py",
    "examples/seatlab/exhibit_bind.py",
    "examples/seatlab/exhibit_capture.py",
    "examples/seatlab/exhibit_enroll.py",
    "examples/seatlab/exhibit_filing.py",
    "examples/seatlab/exhibit_harness.py",
    "examples/seatlab/exhibit_options.py",
    "examples/seatlab/exhibit_owner.py",
    "examples/seatlab/exhibit_pairs.py",
    "examples/seatlab/exhibit_plan.py",
    "examples/seatlab/exhibit_prepare.py",
    "examples/seatlab/exhibit_saved.py",
    "examples/seatlab/exhibit_team_inputs.py",
    "examples/seatlab/exhibit_team_receipt.py",
    "examples/seatlab/exhibit_turn_receipt.py",
    "examples/seatlab/exhibit_wire.py",
    "examples/seatlab/visibility/local_seat.py",
    "examples/seatlab/workbench/arm_runtime.py",
    "examples/seatlab/workbench/arm_sources.py",
    "examples/seatlab/workbench/isolate.py",
    "examples/seatlab/workbench/seat_stream.py",
    "examples/ship_inflight/materialize.py",
    "src/harbor/environments/seat.py",
    "src/harbor/models/job/lock.py",
    "src/harbor/models/task/task.py",
    "src/harbor/models/trial/config.py",
    "src/harbor/models/trial/result.py",
    "src/harbor/publisher/packager.py",
    "src/harbor/trial/seats.py",
    "src/harbor/trial/trial.py",
)


def fingerprint(root: Path) -> str:
    assert HARNESS_PATHS == tuple(sorted(set(HARNESS_PATHS)))
    outer = hashlib.sha256(b"seatlab.exhibit-harness/v1\n")
    budget = MAX_TASK_BYTES
    for relative in HARNESS_PATHS:
        digest, size = _task_file_sha(root / relative, budget)
        budget -= size
        outer.update(f"{relative}\0{digest}\n".encode("ascii"))
    assert 0 <= budget <= MAX_TASK_BYTES
    return "sha256:" + outer.hexdigest()
