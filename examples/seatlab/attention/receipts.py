from collections.abc import Sequence
from pathlib import Path

from .. import native_receipts
from . import protocol

_REPORT_SCHEMA = "seatlab.native-attention-report/v1"
_STAGES = (("inventory", "inventory"), ("select", "dispatch"), ("decide", "dispatch"))

audit_native_receipts = native_receipts.audit_native_receipts


def _verify_attention(source: native_receipts.Report) -> None:
    with native_receipts.audit_import_path():
        from . import audit

        native_receipts.verify_command_report(
            source,
            plan_schema="seatlab.native-attention-plan/v1",
            cells=list(protocol.grid()),
            episodes=protocol.EPISODES,
            seat_sources={"agent.py", "protocol.py"},
            metrics=audit._METRICS,
            validate_opened=protocol._open_pair,
            summarize=audit.summarize,
        )


def audit_receipts(
    report: Path, inventory_receipts: Path, dispatch_receipts: Path, model: str
) -> native_receipts.Report:
    return {
        "schema": "seatlab.native-attention-external/v1",
        **audit_native_receipts(
            report,
            inventory_receipts,
            dispatch_receipts,
            model,
            report_schema=_REPORT_SCHEMA,
            verify=_verify_attention,
            stages=_STAGES,
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    return native_receipts.receipt_main(argv, study="attention", collect=audit_receipts)


if __name__ == "__main__":
    raise SystemExit(main())
