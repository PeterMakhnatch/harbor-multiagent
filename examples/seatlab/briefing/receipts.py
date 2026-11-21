from collections.abc import Sequence
from pathlib import Path

from .. import native_receipts
from ..attention import protocol as attention_protocol
from . import protocol

_REPORT_SCHEMA = "seatlab.native-briefing-report/v1"
_STAGES = (
    ("inventory", "inventory"),
    ("brief", "dispatch"),
    ("push", "inventory"),
    ("decide", "dispatch"),
)
_SEAT_SOURCES = {
    "agent.py",
    "protocol.py",
    "channel.py",
    "shared/attention/agent.py",
    "shared/attention/protocol.py",
    "shared/seat_agent.py",
    "shared/visibility/native_pairs.py",
    "shared/visibility/route_cli.py",
    "shared/visibility/seat_command.py",
}


def _verify_briefing(source: native_receipts.Report) -> None:
    with native_receipts.audit_import_path():
        from . import audit

        native_receipts.verify_command_report(
            source,
            plan_schema="seatlab.native-briefing-plan/v1",
            cells=list(protocol.grid()),
            episodes=attention_protocol.EPISODES,
            seat_sources=_SEAT_SOURCES,
            metrics=audit.attention._METRICS,
            validate_opened=attention_protocol._open_pair,
            summarize=audit.summarize,
            extra_outcome_fields=("brief_correct", "brief_slots"),
        )


def audit_receipts(
    report: Path, inventory_receipts: Path, dispatch_receipts: Path, model: str
) -> native_receipts.Report:
    return {
        "schema": "seatlab.native-briefing-external/v1",
        **native_receipts.audit_native_receipts(
            report,
            inventory_receipts,
            dispatch_receipts,
            model,
            report_schema=_REPORT_SCHEMA,
            verify=_verify_briefing,
            stages=_STAGES,
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    return native_receipts.receipt_main(argv, study="briefing", collect=audit_receipts)


if __name__ == "__main__":
    raise SystemExit(main())
