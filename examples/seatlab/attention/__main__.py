from .. import native_panel
from . import audit, protocol


def experiment() -> native_panel.Experiment:
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
    return native_panel.main(
        experiment(),
        argv,
        description="Measure a two-read dispatcher in real, independently claimed Harbor trials.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
