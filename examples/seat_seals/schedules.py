import re

COMPLETE = "complete"
NOT_STARTED = "not_started"
INTERRUPTED = "interrupted"
FAILURE = re.compile(r"[A-Z][A-Za-z0-9]{0,58}Error")
VERDICTS = ("complete", "incomplete", "invalid")
CLASSIC = (
    "arrived:planner:after_publish",
    "arrived:dispatcher:before_decide",
    "witness:running:running",
    "release:planner:after_publish",
    "witness:clean:running",
    "release:dispatcher:before_decide",
    "arrived:dispatcher:after_decide",
    "release:dispatcher:after_decide",
)
TRACES: dict[str, tuple[str, ...]] = {
    "held-early": CLASSIC,
    "held-late": (
        "arrived:planner:after_publish",
        "arrived:dispatcher:before_decide",
        "witness:running:running",
        "release:dispatcher:before_decide",
        "arrived:dispatcher:after_decide",
        "witness:running:running",
        "release:planner:after_publish",
        "witness:clean:running",
        "release:dispatcher:after_decide",
    ),
    "held-window": (
        "arrived:planner:after_publish",
        "arrived:dispatcher:before_decide",
        "witness:running:running",
        "release:planner:after_publish",
        "native:planner:clean",
        "pending:clean:running",
        "release:dispatcher:before_decide",
        "arrived:dispatcher:after_decide",
        "permit:decision",
        "witness:clean:running",
        "release:dispatcher:after_decide",
    ),
    "held-leave": CLASSIC,
}
MAX_STEPS = max(len(trace) for trace in TRACES.values())

assert all(trace[-1] == "release:dispatcher:after_decide" for trace in TRACES.values())
assert FAILURE.fullmatch(COMPLETE) is None and FAILURE.fullmatch(INTERRUPTED) is None


def classify(schedule: str, status: object, trace: object) -> str:
    assert schedule in TRACES
    if type(status) is not str or type(trace) is not list:
        return "invalid"
    if len(trace) > MAX_STEPS or not all(type(step) is str for step in trace):
        return "invalid"
    expected = list(TRACES[schedule])
    if status == COMPLETE:
        return "complete" if trace == expected else "invalid"
    if status == NOT_STARTED:
        return "incomplete" if trace == [] else "invalid"
    if status != INTERRUPTED and FAILURE.fullmatch(status) is None:
        return "invalid"
    if len(trace) < len(expected) and trace == expected[: len(trace)]:
        return "incomplete"
    return "invalid"
