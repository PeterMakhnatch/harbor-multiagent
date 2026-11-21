from .. import native_panel
from . import audit, protocol


def experiment() -> native_panel.Experiment:
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
    return native_panel.main(
        experiment(),
        argv,
        description="Measure a cooperative six-letter brief in independently claimed Harbor trials.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
