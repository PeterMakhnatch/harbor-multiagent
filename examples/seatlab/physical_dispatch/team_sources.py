import hashlib
from pathlib import Path

from ...native_study import MAX_TASK_BYTES, _task_file_sha

_KNOWLEDGE = ("__init__", "core", "pair")
_FREE_TEAM = (
    "free_team",
    "freshness",
    "route_evidence",
    "sender_coverage",
    "stage_labels",
    "team_audit",
    "team_history",
    "team_plan",
    "team_sources",
)


def extension_digest(checkout: Path) -> str:
    h = hashlib.sha256(b"seatlab.physical-free-team/v1\n")
    budget = MAX_TASK_BYTES
    for folder, names in (
        ("decision_boundary", _KNOWLEDGE),
        ("physical_dispatch", _FREE_TEAM),
    ):
        for name in names:
            path = f"examples/seatlab/{folder}/{name}.py"
            digest, size = _task_file_sha(checkout / path, budget)
            budget -= size
            h.update(f"{path}\0{digest}\n".encode("ascii"))
    return "sha256:" + h.hexdigest()
