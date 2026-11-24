AGENT_TIMEOUT_MULTIPLIER = 0.05
AGENT_TIMEOUT_SECONDS = 30.0
NATIVE_DEADLINE = "AgentTimeoutError"
TIMEOUTS: dict[str, tuple[str, ...]] = {
    "timeout-peer-after": ("dispatcher",),
    "timeout-peer-before": ("dispatcher",),
    "timeout-target": ("planner",),
    "timeout-both": ("planner", "dispatcher"),
    "timeout-off": ("dispatcher",),
}
UNOBSERVED = frozenset({"timeout-off"})
SEED = 17
