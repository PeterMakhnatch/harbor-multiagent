from pathlib import Path

from examples.seatlab.visibility import seat_command

ROLES = ("sales", "finance")
_SEATS: dict[str, seat_command.CommandSeat] = {}
_BOUND = False


class CommandUnavailable(RuntimeError):
    pass


def bind(path: Path) -> None:
    global _BOUND
    if _BOUND:
        raise CommandUnavailable("seat commands already bound")
    if not isinstance(path, Path):
        raise TypeError("seat command config must be a path")
    _BOUND = True
    sales, finance = seat_command.load_commands(path, roles=ROLES)
    _SEATS.update(zip(ROLES, (sales, finance), strict=True))
    assert tuple(_SEATS) == ROLES


def turn(role: str, prompt: str) -> str:
    if role not in ROLES or not isinstance(prompt, str) or not prompt:
        raise ValueError("invalid seat command turn")
    seat = _SEATS.get(role)
    if seat is None:
        raise CommandUnavailable("seat commands are not bound")
    text = seat(prompt)
    assert isinstance(text, str)
    return text
