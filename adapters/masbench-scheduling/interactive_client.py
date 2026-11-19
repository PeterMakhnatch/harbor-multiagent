"""Reference actor for one seat in the native calendar cursor protocol."""

import argparse
import hashlib
import json
import math
import os
import stat
import tempfile
import time
from bisect import bisect_left
from pathlib import Path
from typing import Any, TypeGuard

ROLES = ("m1", "m2", "m3", "m4", "m5")
MAX_ROUNDS = 720
DEADLINE_SECONDS = 120
POLL_SECONDS = 0.025
MAX_MESSAGE_BYTES = 64 * 1024


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _is_tick(value: object, bound: int, horizon: int) -> TypeGuard[int]:
    return type(value) is int and bound <= value < horizon


def feasible_starts(view: dict[str, Any]) -> list[int]:
    """Compute local starts from occupied ticks, without crossing a day boundary."""
    if view.keys() != {"role", "mode", "horizon", "duration", "slots_per_day", "busy"}:
        raise ValueError("invalid calendar view fields")
    if view["role"] not in ROLES or view["mode"] != "cursor":
        raise ValueError("invalid calendar view role or mode")
    horizon = view["horizon"]
    duration = view["duration"]
    slots_per_day = view["slots_per_day"]
    if any(type(n) is not int or n <= 0 for n in (horizon, duration, slots_per_day)):
        raise ValueError("calendar dimensions must be positive integers")
    if horizon > MAX_ROUNDS:
        raise ValueError("calendar exceeds the cursor horizon")
    busy = view["busy"]
    if not isinstance(busy, list) or any(
        not _is_tick(tick, 0, horizon) for tick in busy
    ):
        raise ValueError("busy ticks must be integers inside the horizon")
    occupied = set(busy)
    starts = []
    free_run = 0
    for tick in range(horizon):
        if tick in occupied:
            free_run = 0
            continue
        free_run = 1 if tick % slots_per_day == 0 else free_run + 1
        if free_run >= duration:
            starts.append(tick - duration + 1)
    return starts


def next_start(starts: list[int], bound: int) -> int | None:
    """Return the first local start at or above bound from sorted feasible starts."""
    index = bisect_left(starts, bound)
    return starts[index] if index < len(starts) else None


def continue_query(number: int, bound: int, view: dict[str, Any]) -> dict[str, Any]:
    """Build a query linked to the initial view or the preceding round bundle."""
    return {"round": number, "bound": bound, "view_sha256": _digest(view)}


def finish_choice(bundle: dict[str, Any], start: int | None) -> dict[str, Any]:
    """Bind the final choice to the complete terminal round bundle."""
    return {"round": bundle["round"], "start": start, "view_sha256": _digest(bundle)}


def coordinate_round(
    replies: dict[str, Any],
    *,
    bound: int,
    horizon: int,
    previous: dict[str, Any] | None = None,
) -> tuple[int | None, int | None]:
    """Return (next bound, choice). A null bound makes the choice final."""
    if not isinstance(replies, dict) or replies.keys() != set(ROLES):
        raise ValueError("round must have one reply from each seat")
    if not _is_tick(bound, 0, horizon):
        raise ValueError("invalid round bound")
    highest: int = bound
    exhausted = False
    for role in ROLES:
        tick = replies[role]
        if tick is None:
            exhausted = True
        elif _is_tick(tick, bound, horizon):
            highest = max(highest, tick)
        else:
            raise ValueError(f"{role}: invalid reply at bound {bound}")
        if previous is not None:
            known = previous[role]
            if known is not None and known >= bound and tick != known:
                raise ValueError(f"{role}: reply contradicts known start {known}")
    if exhausted:
        return None, None
    if all(tick == highest for tick in replies.values()):
        return None, highest
    return highest, None


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _read_message(path: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MESSAGE_BYTES:
            raise ValueError(f"invalid message file: {path.name}")
        data = stream.read(MAX_MESSAGE_BYTES + 1)
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError(f"oversized message: {path.name}")
    try:
        message = json.loads(data, object_pairs_hook=_unique_fields)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"invalid JSON message: {path.name}") from exc
    if not isinstance(message, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return message


def _wait_for(deadline: float, *paths: Path) -> tuple[Path, dict[str, Any]]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            names = " or ".join(path.name for path in paths)
            raise TimeoutError(f"timed out waiting for {names}")
        received = []
        for path in paths:
            message = _read_message(path)
            if message is not None:
                received.append((path, message))
        if len(received) > 1:
            raise ValueError("received conflicting messages for this round")
        if received:
            return received[0]
        time.sleep(min(POLL_SECONDS, remaining))


def _submit(path: Path, document: dict[str, Any]) -> None:
    payload = _canonical(document)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".submit-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.link(name, path)
    finally:
        Path(name).unlink()


def _check_done(message: dict[str, Any]) -> None:
    if message.keys() != {"done"} or message["done"] is not True:
        raise ValueError("invalid end-of-play message")


def run_actor(role: str, exchange: Path, *, timeout: float = DEADLINE_SECONDS) -> None:
    """Run exactly one actor, using only the files in the supplied seat port."""
    if role not in ROLES:
        raise ValueError("CALENDAR_ROLE must name an active seat (m1 through m5)")
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be finite and nonnegative")
    deadline = time.monotonic() + timeout
    _, view = _wait_for(deadline, exchange / "input.json")
    if view.get("role") != role:
        raise ValueError("calendar view is not for this seat")
    starts = feasible_starts(view)
    horizon = view["horizon"]
    initial_digest = _digest(view)
    out = exchange / "out"
    done = exchange / "done.json"
    coordinator = role == "m1"
    expected_bound = 0
    previous_bound = -1
    previous_replies = None
    prior_view = view
    number = 1
    while True:
        suffix = f"{number:03d}.json"
        query_name = f"query-{suffix}"
        if coordinator:
            _submit(
                out / query_name, continue_query(number, expected_bound, prior_view)
            )
        path, query = _wait_for(deadline, done, exchange / query_name)
        if path == done:
            if coordinator or number == 1:
                raise ValueError("end-of-play message preceded the required rounds")
            _check_done(query)
            return
        bound = query.get("bound")
        if (
            query.keys() != {"round", "bound"}
            or type(query["round"]) is not int
            or query["round"] != number
            or number > min(horizon, MAX_ROUNDS)
            or not _is_tick(bound, previous_bound + 1, horizon)
            or ((coordinator or number == 1) and bound != expected_bound)
        ):
            raise ValueError(f"invalid query: {query_name}")
        tick = next_start(starts, bound)
        _submit(
            out / f"reply-{suffix}",
            {
                "role": role,
                "round": number,
                "next": tick,
                "initial_sha256": initial_digest,
                "query_sha256": _digest(query),
            },
        )
        if coordinator:
            path, bundle = _wait_for(deadline, done, exchange / f"round-{suffix}")
            if (
                path == done
                or bundle.keys() != {"round", "bound", "replies"}
                or type(bundle["round"]) is not int
                or bundle["round"] != number
                or type(bundle["bound"]) is not int
                or bundle["bound"] != bound
            ):
                raise ValueError(f"invalid round bundle: {suffix}")
            replies = bundle["replies"]
            next_bound, choice = coordinate_round(
                replies, bound=bound, horizon=horizon, previous=previous_replies
            )
            if replies[role] != tick:
                raise ValueError("round bundle changed the coordinator's own reply")
            if next_bound is None:
                _submit(out / "choice.json", finish_choice(bundle, choice))
                _, message = _wait_for(deadline, done)
                _check_done(message)
                return
            expected_bound = next_bound
            previous_replies = replies
            prior_view = bundle
        previous_bound = bound
        number += 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role", choices=ROLES, default=os.environ.get("CALENDAR_ROLE")
    )
    parser.add_argument("--exchange", type=Path, default=Path("/exchange"))
    parser.add_argument("--timeout", type=float, default=DEADLINE_SECONDS)
    args = parser.parse_args(argv)
    try:
        run_actor(args.role, args.exchange, timeout=args.timeout)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"calendar actor: {exc}") from exc


if __name__ == "__main__":
    main()
