from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from examples.seat_handoffs.core import Finding

SOURCE_REPOSITORY = "https://github.com/PeterMakhnatch/harbor-multiagent"
SOURCE_BRANCH = "experiments/seatlab-report-budget"
SOURCE_COMMIT = "eead793e3e59c42b4ea619a1ce10cab65f147c92"
SOURCE_PATH = "examples/seatlab/results/report-budget/rows.json"
SOURCE_SHA256 = "d42f481e38c9d6bf819a546b666f9749fece0c1985fd0fdda96a9927c9ea2e5e"

MESSAGE = "pricing_report"
SENDER = "sales"
RECEIVER = "finance"
ISSUED = ("observed", "unobserved")
UNPRICED = ("invalid", "unanswered")
MAX_BYTES = 1 << 20

Row = dict[str, Any]


def inspect(path: Path) -> list[Finding]:
    _require(path.is_file(), f"bespoke rows file is missing: {path.name}")
    _require(path.stat().st_size <= MAX_BYTES, "bespoke rows file is too large")
    raw = path.read_bytes()
    _require(
        hashlib.sha256(raw).hexdigest() == SOURCE_SHA256,
        f"bespoke adapter reads only the audited public file {SOURCE_PATH} "
        f"at {SOURCE_COMMIT}; this file's SHA-256 differs",
    )
    rows = json.loads(raw)
    return [_finding(row) for row in rows if row["status"] in ISSUED]


def _finding(row: Row) -> Finding:
    label = f"world {row['world']} target {row['target']}"
    finance, sales_status = row[RECEIVER], row["sales_status"]
    delivered, pick = row["delivered"], row["finance_pick"]
    requested = (
        finance is not None
        and finance["request"] is True
        and row["finance_seen"] is True
    )
    _require(
        (delivered is False) == (sales_status in UNPRICED)
        and (delivered is not True or (requested and sales_status == "accepted")),
        f"{label} delivery disagrees with the sales resolution or finance request",
    )
    _require(
        pick is None
        or (
            requested
            and finance["verified"] is True
            and finance["status"] == "accepted"
        ),
        f"{label} committed price is not a verified accepted finance reply",
    )
    if requested and delivered is not None:
        receipt = "confirmed" if delivered else "absent"
    else:
        receipt = "unknown"
    if pick is not None:
        commit = "recorded"
    elif (
        row["status"] == "observed"
        and finance is not None
        and finance["status"] in UNPRICED
    ):
        commit = "not_recorded"
    else:
        commit = "unknown"
    reward = row["protocol_reward"]
    _require(
        reward is None
        or row["status"] != "observed"
        or reward == int(pick is not None),
        f"{label} native grade disagrees with the committed price",
    )
    timing = "unknown"
    if receipt == "confirmed" and commit != "unknown":
        timing = "before" if commit == "recorded" else "no_commit"
    return Finding(
        trial=row["native_id"],
        message=MESSAGE,
        sender=SENDER,
        receiver=RECEIVER,
        receipt=receipt,
        timing=timing,
        commit=commit,
        reward=reward,
        basis="none" if receipt == "unknown" else "model_request",
    )


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)
