"""Read one native box-ownership arm without contacting the world or the model."""

from pathlib import Path
from typing import Any, cast

from . import boxes_choice, intent_packet, intent_protocol, ownership_protocol
from . import boxes_packet as bp
from .boxes_run import MAX_RESPONSE_BYTES
from .intent_protocol import Block, Choices
from .ownership_source import Action, Cut, _task_identity, prefix_sha256

_AGENT_IMPORT = "examples.seatlab.visibility.ownership_seat:OwnershipSeat"
_AGENT_INFO = {"name": "seatlab-box-ownership", "version": "1.0.0", "model_info": None}
_TURN_SCHEMA = "seatlab.box-ownership-turn/v1"
_SEAT_SCHEMA = "seatlab.box-ownership-seat/v1"
_READS = ("mine", "rules", "msgs")
_WRITES = ("open", "msg")
_EVENT_FIELDS = {
    "seq",
    "t",
    "seat",
    "kind",
    "method",
    "status",
    "ok",
    "request",
    "response",
}


def _need(condition: bool) -> None:
    if not condition:
        raise ValueError("native_evidence_invalid")


def _same(actual: object, expected: object) -> bool:
    # Python equality would accept True for box 1 or an integer budget.
    return bp._encode(actual) == bp._encode(expected)


def _object(path: Path) -> dict[str, Any]:
    value = bp._decode(bp._read(path, bp._NATIVE_LIMIT))
    _need(isinstance(value, dict))
    return value


def _roster(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_seat = {row["seat"]: row for row in rows}
    _need(len(rows) == len(bp._SEATS) and by_seat.keys() == set(bp._SEATS))
    return by_seat


def _native_identity(trial: Path, cut: Cut, result: dict[str, Any]) -> None:
    config, lock = (_object(trial / name) for name in ("config.json", "lock.json"))
    run = trial.name
    task = str(cut.task)
    task_name = bp._task_name(cut.instance)
    embedded = dict(result["config"])
    embedded.pop("agent")  # Seat trials omit the unused fallback in config.json.
    _need(_same(config, embedded))
    _need(
        _same(config["schema_version"], 1)
        and _same(result["schema_version"], 1)
        and _same(lock["schema_version"], 2)
        and config["trial_name"] == result["trial_name"] == run
        and config["trials_dir"] == str(trial.parent)
        and result["trial_uri"] == trial.as_uri()
        and result["task_name"] == f"muster/{task_name}"
        and _same(result["task_id"], {"path": task})
        and result.get("source") is None
        and result["task_checksum"] == cut.task_checksum
        and result["exception_info"] is None
        and result.get("step_results") is None
        and _same(result["agent_info"], _AGENT_INFO)
        and result["verifier_environment_mode"] == "shared"
        and result["reward_valid"] is True
        and isinstance(result["finished_at"], str)
        and bool(result["finished_at"])
    )
    _need(
        config["task"]["path"] == lock["task"]["path"] == task
        and lock["task"]["type"] == "local"
        and lock["task"]["name"] == task_name
        and lock["task"]["digest"] == cut.task_digest
        and not any(
            record.get(key) is not None
            for record in (config["task"], lock["task"])
            for key in ("source", "git_url", "git_commit_id", "ref", "download_dir")
        )
        and config["task"].get("name") is None
        and config["task"].get("overwrite", False) is False
        and lock["verifier"]["environment_mode"] == "shared"
    )
    for record in (config, lock):
        environment = record["environment"]
        verifier = record["verifier"]
        _need(
            record["install_only"] is False
            and not any(
                record.get(key)
                for key in (
                    "source_trial",
                    "user_agent",
                    "extra_instructions",
                    "extra_instruction_paths",
                    "extra_docker_compose",
                    "skills",
                )
            )
            and environment["type"] == "docker"
            and environment.get("import_path") is None
            and not any(
                environment.get(key)
                for key in (
                    "mounts",
                    "mounts_json",
                    "extra_docker_compose",
                    "env",
                    "kwargs",
                    "extra_allowed_hosts",
                )
            )
            and verifier["disable"] is False
            and verifier.get("import_path") is None
            and not verifier.get("env")
            and not verifier.get("kwargs")
            and not verifier.get("extra_allowed_hosts")
        )
        for seat, participant in _roster(record["participants"]).items():
            _need(
                participant["import_path"] == _AGENT_IMPORT
                and _same(participant["kwargs"], {"seat": seat})
                and all(
                    participant.get(key) is None
                    for key in (
                        "name",
                        "model_name",
                        "model_api_key_name",
                        "load_trajectory",
                    )
                )
                and not any(
                    participant.get(key)
                    for key in ("env", "mcp_servers", "resume_trajectory", "skills")
                )
            )


def _turns(
    directory: Path,
    cut: Cut,
    block: Block,
    arm: str,
    proposals: Choices,
    world: intent_packet.World,
    result: dict[str, Any],
) -> tuple[dict[str, int | None], list[str]]:
    run = directory.name
    seats = _roster(result["seat_results"])
    decoded: dict[str, int | None] = {}
    attestations = {}
    for label in range(len(bp._SEATS)):
        seat = bp._SEATS[block.seat_index(label)]
        turn = _object(directory / "turns" / f"{seat}.json")
        text = turn["response_text"]
        _need(isinstance(text, str))
        text_bytes = text.encode()
        _need(len(text_bytes) <= MAX_RESPONSE_BYTES)
        hashes = {
            "prompt_sha256": bp._sha(
                ownership_protocol.prompt(world, block, label, arm, proposals).encode()
            ),
            "response_sha256": bp._sha(text_bytes),
        }
        _need(
            _same(
                turn,
                {
                    "schema": _TURN_SCHEMA,
                    "run": run,
                    "seat": seat,
                    "response_text": text,
                    **hashes,
                },
            )
        )
        try:
            decoded[seat] = boxes_choice.decode(text, block.numbering)
        except (ValueError, RecursionError):
            decoded[seat] = None
        attestations[seat] = hashes

    invalid = [seat for seat in bp._SEATS if decoded[seat] is None]
    for seat, row in seats.items():
        box = decoded[seat]
        status = (
            "model_invalid" if box is None else "peer_invalid" if invalid else "opened"
        )
        _need(
            row["under_test"] is True
            and row["exception_info"] is None
            and _same(row["agent_info"], _AGENT_INFO)
            and _same(
                row["agent_result"]["metadata"]["box_ownership"],
                {
                    "schema": _SEAT_SCHEMA,
                    "seat": seat,
                    "run": run,
                    "prefix_sha256": cut.prefix_sha256,
                    "status": status,
                    "original_box": box,
                    **attestations[seat],
                },
            )
        )
    return decoded, invalid


def _views(
    cut: Cut, world: intent_packet.World
) -> dict[tuple[str, str], dict[str, Any]]:
    messages = [action.response for action in cut.actions if action.kind == "msg"]
    views = {}
    for seat, case in zip(bp._SEATS, world.seats, strict=True):
        opens = [
            {"box": box, "room": room, "flag": flag} for box, room, flag in case.own
        ]
        views[seat, "mine"] = {"opens": opens}
        views[seat, "rules"] = {
            "you": seat,
            "seats": list(bp._SEATS),
            "channel": True,
            "budget_left": {"open": case.budget_left},
        }
        views[seat, "msgs"] = {"messages": messages}
    return views


def _play(path: Path, cut: Cut, world: intent_packet.World) -> list[Action]:
    """Use stock in_play: only successful, authenticated events before the first close."""
    views = _views(cut, world)
    seen_views: set[tuple[str, str]] = set()
    actions: list[Action] = []
    closed = False
    lines = bp._read(path, bp._NATIVE_LIMIT).splitlines()
    for seq, line in enumerate((line for line in lines if line.strip()), 1):
        event = bp._decode(line)
        _need(
            bp._object(event, _EVENT_FIELDS)
            or (
                bp._object(event, _EVENT_FIELDS | {"late"})
                and event["late"] is True
                and closed
            )
        )
        seat, kind, method, ok = (
            event[key] for key in ("seat", "kind", "method", "ok")
        )
        request, response, status = (
            event[key] for key in ("request", "response", "status")
        )
        _need(
            bp._integer(event["seq"], seq, seq)
            and type(event["t"]) in (int, float)
            and method in ("GET", "POST")
            and isinstance(kind, str)
            and (seat is None or seat in bp._SEATS)
            and (request is None or isinstance(request, dict))
            and isinstance(response, dict)
            and (bp._integer(status, 200, 200) or bp._integer(status, 400, 599))
            and ok is (status == 200)
        )
        if not ok:
            continue
        _need(not closed)
        if kind == "admin.close":
            _need(
                seat is None
                and method == "POST"
                and request == {}
                and _same(response, {"closed": True, "events": seq})
            )
            closed = True
        elif kind in _WRITES:
            _need(seat in bp._SEATS and method == "POST" and isinstance(request, dict))
            _need(len(actions) < len(cut.actions) or seen_views == views.keys())
            actions.append(Action(seat, kind, request, response))
        else:
            _need(
                seat in bp._SEATS
                and method == "GET"
                and kind in _READS
                and request is None
            )
            if len(actions) == len(cut.actions):
                key = (seat, kind)
                _need(_same(response, views[key]))
                seen_views.add(key)
    _need(closed and seen_views == views.keys() and len(actions) >= len(cut.actions))
    for action, expected in zip(actions, cut.actions):
        _need(
            action.seat == expected.seat
            and action.kind == expected.kind
            and _same(action.request, expected.request)
            and _same(action.response, expected.response)
        )
    return actions


def _treasure_rooms(cut: Cut) -> set[int]:
    # Use the trusted task's data, never an instance bundled with the result.
    _need(_task_identity(cut.task) == (cut.task_checksum, cut.task_digest))
    instance = _object(cut.task / "environment" / "world" / "instance.json")
    rooms = instance["treasure_rooms"]
    _need(
        len(rooms) == 2
        and all(bp._integer(room, 1, 6) for room in rooms)
        and len(set(rooms)) == 2
        and _same(instance["room_size"], 10)
        and _same(instance["rooms"], 6)
        and _same(instance["budget"], bp._BUDGET)
        and instance["channel"] is True
        and instance["seats"] == list(bp._SEATS)
    )
    return set(rooms)


def _score(actions: list[Action], cut: Cut) -> tuple[int, dict[str, float | int]]:
    treasure = _treasure_rooms(cut)
    left = dict.fromkeys(bp._SEATS, bp._BUDGET)
    empty = {seat: set() for seat in bp._SEATS}
    boxes: set[int] = set()
    baseline: set[int] = set()
    first_flags: dict[int, str] = {}
    counts = dict.fromkeys(
        ("opens", "messages", "duplicate_opens", "empty_room_repeats"), 0
    )
    for index, action in enumerate(actions):
        in_prefix = index < len(cut.actions)
        if action.kind == "msg":
            counts["messages"] += 1
            continue
        seat, box = action.seat, action.request["box"]
        _need(bp._box(box) and _same(action.request, {"box": box}))
        room = bp._room(box)
        flag = room in treasure
        left[seat] -= 1
        _need(
            left[seat] >= 0
            and _same(
                action.response,
                {
                    "box": box,
                    "room": room,
                    "flag": flag,
                    "budget_left": left[seat],
                },
            )
        )
        counts["opens"] += 1
        counts["duplicate_opens"] += box in boxes
        counts["empty_room_repeats"] += room in empty[seat]
        boxes.add(box)
        if flag:
            first_flags.setdefault(box, seat)
            if in_prefix:
                baseline.add(box)
        else:
            empty[seat].add(room)
    _need(len(baseline) == cut.baseline_flags)
    return len(first_flags) - len(baseline), {
        "reward": len(first_flags) / 20,
        "distinct_boxes": len(boxes),
        **counts,
        **{
            f"seat.{seat}": sum(owner == seat for owner in first_flags.values()) / 20
            for seat in bp._SEATS
        },
    }


def _checked_rewards(actual: dict[str, Any], expected: dict[str, float | int]) -> None:
    _need(
        actual.keys() == expected.keys()
        and all(
            type(actual[key]) in (int, float) and actual[key] == value
            for key, value in expected.items()
        )
    )


def read_arm(
    directory: Path, cut: Cut, block: Block, arm: str, proposals: Choices
) -> dict[str, Any]:
    """Return only public-safe outcomes; invalid evidence never becomes a zero gain."""
    trial_id = None
    try:
        run = ownership_protocol.run_id(block, arm)
        directory = directory.absolute()
        _need(
            cut.instance == block.instance
            and directory.name == run
            and directory.resolve() == directory
        )
        try:
            directory.lstat()
        except FileNotFoundError:
            return {"status": "not_started"}
        trial = directory / "trials" / run
        result = _object(trial / "result.json")
        trial_id = bp._sha(bp._uuid(result["id"]).encode())
        _need(
            cut.task.resolve() == cut.task
            and bp._SHA256.fullmatch(cut.task_checksum) is not None
            and bp._SHA256.fullmatch(cut.task_digest.removeprefix("sha256:"))
            is not None
            and prefix_sha256(cut.actions) == cut.prefix_sha256
        )
        _native_identity(trial, cut, result)
        world = intent_packet.load()[block.instance]
        decoded, invalid = _turns(directory, cut, block, arm, proposals, world, result)
        actions = _play(trial / "verifier" / "events.jsonl", cut, world)
        final = actions[len(cut.actions) :]
        expected = (
            [] if invalid else [(seat, "open", decoded[seat]) for seat in bp._SEATS]
        )
        _need(
            _same(
                [(row.seat, row.kind, row.request.get("box")) for row in final],
                expected,
            )
        )
        new_flags, rewards = _score(actions, cut)
        _checked_rewards(result["verifier_result"]["rewards"], rewards)
        _checked_rewards(_object(trial / "verifier" / "reward.json"), rewards)
        choices = (
            None
            if invalid
            else cast(
                Choices,
                tuple(
                    decoded[bp._SEATS[block.seat_index(label)]]
                    for label in range(len(bp._SEATS))
                ),
            )
        )
        return {
            "status": "model_invalid" if invalid else "complete",
            "trial_id_sha256": trial_id,
            "task_checksum": cut.task_checksum,
            "task_digest": cut.task_digest,
            "prefix_sha256": cut.prefix_sha256,
            "baseline_flags": cut.baseline_flags,
            "new_flags": None if invalid else new_flags,
            "team_reward": rewards["reward"],
            "native_rewards": rewards,
            "choices": choices,
            "metrics": None
            if choices is None
            else intent_protocol.native_metrics(world, choices),
            "model_invalid_seats": invalid,
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        rejected = {"status": "invalid", "reason": "native_evidence_invalid"}
        return (
            rejected if trial_id is None else {**rejected, "trial_id_sha256": trial_id}
        )
