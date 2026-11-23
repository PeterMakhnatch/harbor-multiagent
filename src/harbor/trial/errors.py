import asyncio


class AgentSetupTimeoutError(asyncio.TimeoutError):
    pass


class AgentTimeoutError(asyncio.TimeoutError):
    pass


class VerifierTimeoutError(asyncio.TimeoutError):
    pass


class EnvironmentStartTimeoutError(asyncio.TimeoutError):
    pass


class SeatEndOfPlayError(Exception):
    """A seat's processes could not be stopped, or proven stopped, when play
    ended, so they may have acted after play."""
