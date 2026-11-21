import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path

from . import (
    calendar_client,
    calendar_list_run,
    calendar_protocol,
    calendar_tools_protocol,
    calendar_tools_run,
    seat_command,
)

SCHEMA = "seatlab.calendar-query-turn/v1"
_WORLDS = (1, 2, 3, 5, 6, 7, 9, 10, 11)
_BLOCKS = tuple(
    calendar_protocol.Block(world, direction, 0)
    for world in _WORLDS
    for direction in (0, 1)
)


def blocks() -> tuple[calendar_protocol.Block, ...]:
    return _BLOCKS


def model_prompt(role: str, base: str) -> str:
    if role == "cal_decide":
        return base
    if role in ("cal_initial_p1", "cal_initial_p2"):
        return (
            base + "\n\nGive your teammate at most two distinct starts "
            "that fit your own calendar, not the full list.\n"
        )
    if role == "cal_recheck":
        return (
            base
            + "\n\nRecommend at most two distinct starts that fit your own calendar. "
            "Check any suggested slots first. If none fit, suggest other feasible starts.\n"
        )
    raise ValueError("invalid native role")


def play(
    tasks: Path,
    study: Path,
    block: calendar_protocol.Block,
    *,
    sender: Callable[[str], str],
    receiver: Callable[[str], str],
) -> str:
    run = calendar_tools_protocol.native_id(block)
    if block not in _BLOCKS:
        raise calendar_list_run.CaptureError("unregistered_run")
    if not callable(sender) or not callable(receiver):
        raise TypeError("seat callbacks must be callable")
    seats = {block.sender: ("sender", sender), block.receiver: ("receiver", receiver)}
    try:
        with (
            calendar_list_run._root(study) as root,
            calendar_tools_run._claim_directory(root, run) as pair,
            calendar_tools_run._claim_directory(pair, "actions") as actions,
        ):
            session = calendar_tools_protocol.Session(block)

            def turn(identifier: str, base: str) -> str:
                view = session.view(identifier, base)
                role, respond = seats[view.seat]
                name = identifier.replace(":", "_") + ".json"
                try:
                    fd = os.open(name, calendar_tools_run._CLAIM, 0o600, dir_fd=actions)
                except FileExistsError:
                    raise calendar_list_run.CaptureError(
                        "action_already_exists"
                    ) from None
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    os.fchmod(out.fileno(), 0o600)
                    prompt = model_prompt(view.role, view.base)
                    request = calendar_client._text_bytes(
                        prompt, "invalid_callback_prompt"
                    )
                    try:
                        text = respond(prompt)
                    except Exception:  # noqa: BLE001
                        raise calendar_client.CalendarClientError(
                            "model_callback_failed"
                        ) from None
                    response = calendar_client._text_bytes(
                        text, "invalid_callback_text"
                    )
                    session.accept(identifier, text)
                    receipt = {
                        "schema": SCHEMA,
                        "id": identifier,
                        "seat": view.seat,
                        "role": role,
                        "base_sha256": calendar_tools_protocol.digest(view.base),
                        "prompt_sha256": sha256(request).hexdigest(),
                        "response_sha256": sha256(response).hexdigest(),
                    }
                    out.write(json.dumps(receipt, separators=(",", ":")) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                return text

            return calendar_client._drive(tasks, study / run / "block", block, turn)
    except OSError:
        raise calendar_list_run.CaptureError("private_storage_unavailable") from None
    except (calendar_client.CalendarClientError, calendar_list_run.CaptureError):
        raise
    except Exception:  # noqa: BLE001
        raise calendar_client.CalendarClientError("native_pair_failed") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--commands", type=Path, required=True)
    parser.add_argument("--world", type=int, choices=_WORLDS, required=True)
    parser.add_argument("--direction", type=int, choices=(0, 1), required=True)
    args = parser.parse_args(argv)
    try:
        sender, receiver = seat_command.load_commands(
            args.commands, roles=("sender", "receiver")
        )
    except (OSError, ValueError):
        print("calendar-query: invalid_command_config", file=sys.stderr)
        return 1
    try:
        status = play(
            args.tasks,
            args.study,
            calendar_protocol.Block(args.world, args.direction, 0),
            sender=sender,
            receiver=receiver,
        )
    except (calendar_client.CalendarClientError, calendar_list_run.CaptureError) as exc:
        print(f"calendar-query: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("calendar-query: interrupted", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
