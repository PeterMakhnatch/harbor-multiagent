from typing import Any

RUN_PREFIX = "attnc-"
PLAN_SCHEMA = "seatlab.native-attention-confirmation-plan/v1"
REPORT_SCHEMA = "seatlab.native-attention-confirmation-report/v1"
AGENT_IMPORT = "examples.seatlab.attention.confirmation_agent:ConfirmationSeat"


def grid() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "cell_id": f"{RUN_PREFIX}s{seed}-r0-{arm}",
            "seed": seed,
            "repetition": 0,
            "condition": "reordered",
            "pairing": "MM",
            "arm": arm,
        }
        for index, seed in enumerate(range(6801, 6809))
        for arm in (("count", "named") if index % 2 else ("named", "count"))
    )
