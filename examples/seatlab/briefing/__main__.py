from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import native_panel


def experiment() -> native_panel.Experiment:
    from .. import native_panel
    from . import audit, protocol

    return native_panel.Experiment(
        tag="briefing",
        grid=protocol.grid,
        stage_roles=(
            ("inventory", "inventory"),
            ("brief", "dispatch"),
            ("push", "inventory"),
            ("decide", "dispatch"),
        ),
        reservation_prefix="brf-",
        agent_import="examples.seatlab.briefing.agent:BriefingSeat",
        seat_sources=("agent.py", "protocol.py", "channel.py"),
        inspect_trial=audit.inspect_trial,
        summarize=audit.summarize,
        channel_kwarg="channel_id",
        shared_seat_sources=(
            "attention/agent.py",
            "attention/protocol.py",
            "seat_agent.py",
            "visibility/native_pairs.py",
            "visibility/route_cli.py",
            "visibility/seat_command.py",
        ),
    )


def main(argv: list[str] | None = None) -> int:
    from ..native_report import verified_main

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["verify"]:
        return verified_main(args[1:], expected="briefing")
    from .. import native_panel

    return native_panel.main(
        experiment(),
        argv,
        description="Measure a cooperative six-letter brief in independently claimed Harbor trials.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
