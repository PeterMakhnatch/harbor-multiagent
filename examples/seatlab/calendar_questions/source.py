from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import cast

from ..rolepack import calendar_source, protocol

SCHEMA = "seatlab.calendar-question-sources/v1"
_WORLDS = (0, 4, 8)
_IDS = {
    (
        world,
        direction,
        repetition,
    ): f"cal-rx-w{world:02d}-d{direction}-r{repetition}-blind"
    for world in _WORLDS
    for direction in range(2)
    for repetition in range(2)
}
_PACKET = Path("examples/seatlab/results/flotilla-calendar-packet.json")
_PACKET_SHA256 = "b7073bdb5c2bdd44e6e6b2445137b08d3856db6793bdf8d17ba2a0eb7af86de0"
_TASK = "\n\nTask:\n"
_CELL_INTRO = "Unlisted cells are free; '-' means no busy cells.\n"
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_HOUR_BITS = {f"{9 + bit:02d}:00": 1 << bit for bit in range(8)}
_STARTS_PER_DAY = 7
_SLOT_COUNT = len(_DAYS) * _STARTS_PER_DAY
_MAX_PROMPT_BYTES = 4096


def _view(prompt: str) -> str:
    text, marker, task = prompt.partition(_TASK)
    if not marker or not task.startswith("Your initial candidate list: "):
        raise ValueError("invalid original receiver prompt")
    return text + marker


def _starts(table: list[list[int]]) -> list[int]:
    return [
        day * _STARTS_PER_DAY + offset
        for day, (a, b, c) in enumerate(zip(*table, strict=True))
        for offset in range(_STARTS_PER_DAY)
        if not (a | b | c) & (3 << offset)
    ]


def _view_starts(view: str) -> list[int]:
    _, marker, busy = view.removesuffix(_TASK).partition(_CELL_INTRO)
    groups = busy.split("\n\n")
    if not marker or len(groups) != 3:
        raise ValueError("invalid calendar view groups")
    table: list[list[int]] = []
    for group in groups:
        lines = group.split("\n")
        if len(lines) != len(_DAYS):
            raise ValueError("invalid calendar view weekdays")
        masks: list[int] = []
        for day, line in zip(_DAYS, lines, strict=True):
            prefix = f"{day}: "
            if not line.startswith(prefix):
                raise ValueError("invalid calendar view weekday")
            times = line.removeprefix(prefix)
            hours = [] if times == "-" else times.split(", ")
            if any(hour not in _HOUR_BITS for hour in hours) or any(
                left >= right for left, right in pairwise(hours)
            ):
                raise ValueError("invalid calendar view busy hours")
            masks.append(sum(_HOUR_BITS[hour] for hour in hours))
        table.append(masks)
    return _starts(table)


def _check_starts(value: object) -> list[int]:
    slots = protocol._list(value, 1, _SLOT_COUNT, "private calendar starts")
    if any(type(slot) is not int or not 0 <= slot < _SLOT_COUNT for slot in slots):
        raise ValueError("invalid private calendar starts")
    if any(left >= right for left, right in pairwise(slots)):
        raise ValueError("private calendar starts must be unique and sorted")
    return cast(list[int], slots)


def _optimum(receiver: list[int], sender: list[int]) -> int:
    joint = set(receiver).intersection(sender)
    if len(joint) != 1:
        raise ValueError("calendar must have exactly one joint start")
    return joint.pop()


def _record(value: object, record_id: str, world: int) -> protocol.Document:
    record = protocol._object(
        value,
        "calendar question source record",
        "id",
        "world",
        "receiver_prompt",
        "receiver_sha256",
        "sender_view",
        "sender_sha256",
        "receiver_starts",
        "sender_starts",
        "optimum",
    )
    if (
        type(record["id"]) is not str
        or record["id"] != record_id
        or type(record["world"]) is not str
        or record["world"] != f"w{world:02d}"
    ):
        raise ValueError("calendar source record coordinates or order differ")
    for text, digest in (
        ("receiver_prompt", "receiver_sha256"),
        ("sender_view", "sender_sha256"),
    ):
        raw = protocol._utf8(record[text], _MAX_PROMPT_BYTES, text)
        protocol._sha(record[digest], digest)
        if sha256(raw).hexdigest() != record[digest]:
            raise ValueError(f"{text} hash mismatch")
    receiver = _check_starts(record["receiver_starts"])
    sender = _check_starts(record["sender_starts"])
    if type(record["optimum"]) is not int or record["optimum"] != _optimum(
        receiver, sender
    ):
        raise ValueError("calendar source optimum differs from the joint start")
    return record


def validate(doc: object) -> protocol.Document:
    document = protocol._object(doc, "calendar question sources", "schema", "records")
    if type(document["schema"]) is not str or document["schema"] != SCHEMA:
        raise ValueError("unsupported calendar question source schema")
    rows = protocol._list(document["records"], len(_IDS), len(_IDS), "calendar sources")
    records: dict[tuple[int, int, int], protocol.Document] = {}
    views: dict[tuple[int, int, int], str] = {}
    for coords, raw in zip(_IDS, rows, strict=True):
        record = _record(raw, _IDS[coords], coords[0])
        view = _view(record["receiver_prompt"])
        if _view_starts(view) != record["receiver_starts"]:
            raise ValueError("receiver starts differ from the receiver view")
        records[coords] = record
        views[coords] = view
    for (world, direction, repetition), record in records.items():
        opposite = (world, 1 - direction, repetition)
        if (
            record["sender_view"] != views[opposite]
            or record["sender_starts"] != records[opposite]["receiver_starts"]
        ):
            raise ValueError("sender fields differ from the opposite receiver role")
        if views[world, direction, repetition] != views[world, direction, 0]:
            raise ValueError("calendar view differs between repetitions")
    return document


def _native_starts(root: Path) -> dict[int, tuple[list[int], list[int]]]:
    try:
        raw = calendar_source._pinned(root / _PACKET, _PACKET_SHA256)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("missing, altered, or unsafe native calendar packet") from None
    worlds = protocol.loads(raw)["worlds"]
    return {
        world: (
            _starts(worlds[world]["tables"][0]),
            _starts(worlds[world]["tables"][1]),
        )
        for world in _WORLDS
    }


def build(calendar_checkout: Path, mixed_pack: protocol.Document) -> protocol.Document:
    pack = protocol.validate(mixed_pack)
    native = _native_starts(calendar_checkout)
    stock = calendar_source.export(calendar_checkout)
    calendar = sorted(
        (record for record in pack["records"] if record["task"] == "calendar"),
        key=lambda record: record["id"],
    )
    if calendar != stock:
        raise ValueError("calendar RolePack records differ from the published source")
    by_id = {record["id"]: record for record in calendar}
    rows = []
    for (world, direction, repetition), record_id in _IDS.items():
        original = by_id[record_id]
        opposite = by_id[_IDS[world, 1 - direction, repetition]]
        sender_view = _view(opposite["prompt"])
        receiver = native[world][1 - direction]
        sender = native[world][direction]
        optimum = _optimum(receiver, sender)
        if original["rubric"]["items"][0]["optimal"] != [optimum]:
            raise ValueError(
                "native calendar joint start differs from the RolePack grade"
            )
        rows.append(
            {
                "id": record_id,
                "world": original["world"],
                "receiver_prompt": original["prompt"],
                "receiver_sha256": original["prompt_sha256"],
                "sender_view": sender_view,
                "sender_sha256": sha256(sender_view.encode("utf-8")).hexdigest(),
                "receiver_starts": receiver.copy(),
                "sender_starts": sender.copy(),
                "optimum": optimum,
            }
        )
    return validate({"schema": SCHEMA, "records": rows})
