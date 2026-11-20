"""Run both calendar tool policies with separate first-attempt receipts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

from . import calendar_client, calendar_list_run, calendar_protocol
from . import calendar_tools_protocol as tools

SCHEMA = "seatlab.calendar-tool-turn/v1"
_PICK = "cal_tool_pick"
_REPLY = "cal_tool_reply"
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CLAIM = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
# Arguments: meter descriptor, stage ID, native role, prompt.
_StageResponse = Callable[[int, str, str, str], str]


@contextmanager
def _claim_directory(parent: int, name: str) -> Generator[int, None, None]:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        raise calendar_list_run.CaptureError("attempt_already_exists") from None
    directory = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        calendar_list_run._owner(directory, 0o700)
        yield directory
    finally:
        os.close(directory)


def _reply(
    respond: _StageResponse, meter: int, stage: str, role: str, prompt: str
) -> str:
    try:
        text = respond(meter, stage, role, prompt)
    except Exception:  # noqa: BLE001 - Do not expose callback diagnostics.
        raise calendar_client.CalendarClientError("model_callback_failed") from None
    calendar_client._text_bytes(text, "invalid_callback_text")
    return text


def _collect(
    block: calendar_protocol.Block,
    policy: str,
    session: tools.Session,
    actions: int,
    meter: int,
    respond: _StageResponse,
    identifier: str,
    base: str,
) -> str:
    view = session.view(identifier, base)
    run = tools.action_id(block, policy, identifier)
    try:
        fd = os.open(f"{run}.json", _CLAIM, 0o600, dir_fd=actions)
    except FileExistsError:
        raise calendar_list_run.CaptureError("action_already_exists") from None
    # An empty file consumes the attempt without claiming completion. The
    # independent meter can still record usage if either model call fails.
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        os.fchmod(out.fileno(), 0o600)
        selection = tools.select_prompt(view)
        selected = _reply(respond, meter, f"{run}:{_PICK}", view.role, selection)
        result = tools.tool_result(view, policy, selected)
        final = tools.final_prompt(view, result)
        text = _reply(respond, meter, f"{run}:{_REPLY}", view.role, final)
        session.accept(identifier, text)
        receipt = {
            "schema": SCHEMA,
            "run": run,
            "native_id": identifier,
            "seat": view.seat,
            "base_sha256": tools.digest(view.base),
            "select_prompt_sha256": tools.digest(selection),
            "select_text": selected,
            "select_response_sha256": tools.digest(selected),
            "tool_status": result["status"],
            "tool_result_sha256": tools.result_digest(result),
            "final_prompt_sha256": tools.digest(final),
            "final_response_sha256": tools.digest(text),
        }
        out.write(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n")
        out.flush()
        os.fsync(out.fileno())
    return text


def _play_policy(
    tasks: Path,
    parent: Path,
    pair: int,
    block: calendar_protocol.Block,
    policy: str,
    respond: _StageResponse,
) -> str:
    session = tools.Session(block)
    with (
        _claim_directory(pair, policy) as directory,
        _claim_directory(directory, "actions") as actions,
        _claim_directory(directory, "meter") as meter,
    ):

        def turn(identifier: str, prompt: str) -> str:
            return _collect(
                block, policy, session, actions, meter, respond, identifier, prompt
            )

        return calendar_client._drive(tasks, parent / policy / "block", block, turn)


def _play_pair(
    tasks: Path,
    study: Path,
    block: calendar_protocol.Block,
    respond: _StageResponse,
    *,
    prepare: Callable[[], None] | None = None,
) -> dict[str, str]:
    # Reject unregistered coordinates before claiming any storage.
    calendar_client._request_plan(block)
    run = tools.native_id(block)
    parent = study / run
    try:
        with (
            calendar_list_run._root(study) as root,
            _claim_directory(root, run) as pair,
        ):
            if prepare is not None:
                prepare()
            return {
                policy: _play_policy(tasks, parent, pair, block, policy, respond)
                for policy in tools.policies(block)
            }
    except OSError:
        raise calendar_list_run.CaptureError("private_storage_unavailable") from None


def play_pair(
    tasks: Path,
    study: Path,
    block: calendar_protocol.Block,
    respond: Callable[[str], str],
) -> dict[str, str]:
    """Claim one pair and give the callback only the text for each model call."""

    def answer(_meter: int, _stage: str, _role: str, prompt: str) -> str:
        return respond(prompt)

    return _play_pair(tasks, study, block, answer)


def _demo(_meter: int, stage: str, role: str, _prompt: str) -> str:
    if stage.endswith(f":{_PICK}"):
        return '{"tool":"local_starts"}'
    return '{"slot":0}' if role == "cal_decide" else '{"starts":[]}'


def _direct(meter: int, stage: str, _role: str, prompt: str) -> str:
    return calendar_client._direct_turn(meter, stage, prompt)


def _prepare_direct() -> None:
    from . import deepseek

    calendar_client._bind_direct()
    try:
        if deepseek.preflight()["balance_ok"] is not True:
            raise calendar_client.CalendarClientError("direct_preflight_failed")
    except deepseek.SeatUnavailable:
        raise calendar_client.CalendarClientError("direct_preflight_failed") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("demo", "direct"))
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--direction", type=int, required=True)
    parser.add_argument("--repetition", type=int, required=True)
    args = parser.parse_args(argv)
    block = calendar_protocol.Block(args.world, args.direction, args.repetition)
    try:
        if args.mode == "demo":
            statuses = _play_pair(args.tasks, args.study, block, _demo)
        else:
            statuses = _play_pair(
                args.tasks, args.study, block, _direct, prepare=_prepare_direct
            )
    except (calendar_client.CalendarClientError, calendar_list_run.CaptureError) as exc:
        print(f"calendar-tools: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("calendar-tools: interrupted", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - No private paths or provider text on the CLI.
        print("calendar-tools: pair_unavailable", file=sys.stderr)
        return 1
    print(json.dumps(statuses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
