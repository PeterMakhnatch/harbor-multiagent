NATIVE_DISPOSITIONS = ("returned", "raised", "stop_requested", "cancelled")
HALTED_DISPOSITIONS = frozenset({"stop_requested", "cancelled"})
LEGACY_STATES = ("not_started", "running", "clean", "error")
NOT_RETURNED = "not_returned"
STATES = (*LEGACY_STATES, NOT_RETURNED)
OPEN_STATES = frozenset({"not_started", "running"})
LEGACY_TERMINAL_STATES = frozenset({"clean", "error"})
TERMINAL_STATES = frozenset({"clean", "error", NOT_RETURNED})
STATE_RANK = {
    "not_started": 0,
    "running": 1,
    "clean": 2,
    "error": 2,
    NOT_RETURNED: 2,
}

assert HALTED_DISPOSITIONS < frozenset(NATIVE_DISPOSITIONS)
assert OPEN_STATES | TERMINAL_STATES == frozenset(STATES) == frozenset(STATE_RANK)
assert not OPEN_STATES & TERMINAL_STATES
assert LEGACY_TERMINAL_STATES < TERMINAL_STATES
assert frozenset(LEGACY_STATES) == OPEN_STATES | LEGACY_TERMINAL_STATES


def native_disposition(disposition: object) -> str | None:
    if disposition is None:
        return None
    if type(disposition) is not str:
        raise ValueError("native disposition must be a string or None")
    if disposition not in NATIVE_DISPOSITIONS:
        raise ValueError(
            f"native disposition must be one of {list(NATIVE_DISPOSITIONS)}"
        )
    return disposition


def halting(disposition: object) -> bool:
    return native_disposition(disposition) in HALTED_DISPOSITIONS


def terminal_state(disposition: object, has_error: bool, *, legacy: bool) -> str:
    code = native_disposition(disposition)
    if type(has_error) is not bool:
        raise ValueError("has_error must be a bool")
    if type(legacy) is not bool:
        raise ValueError("legacy must be a bool")
    returned = code == "returned" or (code is None and legacy)
    if has_error or code == "raised":
        state = "error"
    elif returned:
        state = "clean"
    else:
        state = NOT_RETURNED
    assert state in TERMINAL_STATES
    assert (state == "clean") == (returned and not has_error)
    return state
