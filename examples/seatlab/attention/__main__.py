from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import native_panel


def experiment() -> native_panel.Experiment:
    from .. import native_panel
    from . import audit, protocol

    return native_panel.Experiment(
        tag="attention",
        grid=protocol.grid,
        stage_roles=(
            ("inventory", "inventory"),
            ("select", "dispatch"),
            ("decide", "dispatch"),
        ),
        reservation_prefix="attn-",
        agent_import="examples.seatlab.attention.agent:AttentionSeat",
        seat_sources=("agent.py", "protocol.py"),
        inspect_trial=audit.inspect_trial,
        summarize=audit.summarize,
    )


def main(argv: list[str] | None = None) -> int:
    from ..native_report import verified_main

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["verify"]:
        return verified_main(args[1:], expected="attention")
    from .. import native_panel

    return native_panel.main(
        experiment(),
        argv,
        description="Measure a two-read dispatcher in real, independently claimed Harbor trials.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
