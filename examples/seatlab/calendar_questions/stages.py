from collections.abc import Sequence
from hashlib import sha256

from ..rolepack import evidence, protocol
from .source import validate

_SLOTS = tuple(range(35))
_STATUSES = ("yes", "no")
_KEYS = ("first", "second")
_QUESTION_TASK = "calendar-question"
_ANSWER_TASK = "calendar-answer"
_FINAL_TASK = "calendar-final"
_QUESTION = (
    "\nNew task: Do not choose the meeting start yet. Choose up to two slots to ask "
    "the other seat about. Choose the slots that give you the best chance of finding "
    "a joint 2-hour meeting. If you want to ask about only one slot, repeat its ID.\n"
    'Return only JSON in this format: {"ask":{"first":<integer>,"second":<integer>}}. '
    "Both integers must be slot IDs from 0 to 34.\n"
)
_ANSWER = (
    "For each requested slot, report whether both hours are free for all attendees "
    'at your seat. Use "yes" if the slot fits your seat, and "no" otherwise. '
    "If both IDs are the same, check the slot once and give its status for both keys.\n"
    'Return only JSON in this format: {"availability":{"first":<status>,'
    '"second":<status>}}. Each status must be the JSON string "yes" or "no".\n'
)
_FINAL = (
    "Choose one start that best fits both seats. Recheck your own calendar if needed. "
    "You may choose any legal slot ID, including a slot that was not requested.\n"
    'Return only a JSON object with a single "slot" key and its integer value.\n'
)

type _Doc = protocol.Document
type _Values = dict[str, list[protocol.Primitive]]


def _item(
    key: str, valid: Sequence[protocol.Primitive], optimal: protocol.Primitive
) -> _Doc:
    return {"key": key, "valid": list(valid), "optimal": [optimal]}


def _record(
    source: _Doc,
    task: str,
    text: str,
    field: str,
    items: list[_Doc],
    *,
    scalar: bool,
    suffix: str = "",
) -> _Doc:
    return {
        "id": source["id"] + suffix,
        "task": task,
        "world": source["world"],
        "prompt": text,
        "prompt_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "peer": None,
        "rubric": {"field": field, "scalar": scalar, "items": items},
    }


def _pack(records: list[_Doc]) -> _Doc:
    return protocol.validate({"schema": protocol.SINGLE_SCHEMA, "records": records})


def _question_pack(records: list[_Doc]) -> _Doc:
    return _pack(
        [
            _record(
                source,
                _QUESTION_TASK,
                "Previous task (for context only):\n"
                + source["receiver_prompt"]
                + _QUESTION,
                "ask",
                [_item(key, _SLOTS, source["optimum"]) for key in _KEYS],
                scalar=False,
            )
            for source in records
        ]
    )


def question(source_document: object) -> _Doc:
    return _question_pack(validate(source_document)["records"])


def _first_draw(pack: _Doc, report: _Doc) -> _Values:
    verified = evidence.verify(pack, report)
    return {
        row["record_id"]: row["outcome"]["values"]
        for row in verified["rows"]
        if row["arm"] == "original" and row["draw"] == 0 and row["state"] == "complete"
    }


def _request(slots: list[protocol.Primitive]) -> str:
    first, second = slots
    return f"Requested slot IDs: first={first}; second={second}.\n"


def _answer_pack(records: list[_Doc], requested: _Values) -> _Doc | None:
    answers = []
    for source in records:
        if (slots := requested.get(source["id"])) is None:
            continue
        answers.append(
            _record(
                source,
                _ANSWER_TASK,
                source["sender_view"] + _request(slots) + _ANSWER,
                "availability",
                [
                    _item(key, _STATUSES, "yes" if slot in source["sender_starts"] else "no")
                    for key, slot in zip(_KEYS, slots, strict=True)
                ],
                scalar=False,
            )
        )
    return _pack(answers) if answers else None


def answer(source_document: object, question_report: _Doc) -> _Doc | None:
    records = validate(source_document)["records"]
    requested = _first_draw(_question_pack(records), question_report)
    return _answer_pack(records, requested)


def _visible_first(record_id: str) -> bool:
    _, _, world, direction, repetition, _ = record_id.split("-")
    return (int(world[1:]) // 4 + int(direction[1:]) + int(repetition[1:])) % 2 == 0


def final(
    source_document: object, question_report: _Doc, answer_report: _Doc | None
) -> _Doc | None:
    """Register pairs in source ID order, reversing each odd coordinate-parity pair.

    Coordinate parity uses the world ordinal (w00=0, w04=1, w08=2), direction,
    and repetition. It does not depend on which other sources complete.
    """
    records = validate(source_document)["records"]
    requested = _first_draw(_question_pack(records), question_report)
    if answer_report is None:
        return None
    answers = _answer_pack(records, requested)
    if answers is None:
        raise ValueError("no calendar answer pack for the supplied report")
    availability = _first_draw(answers, answer_report)
    finals = []
    for source in records:
        record_id = source["id"]
        if record_id not in availability:
            continue
        first, second = availability[record_id]
        prefix = source["receiver_prompt"] + "\n" + _request(requested[record_id])
        visible = _record(
            source,
            _FINAL_TASK,
            prefix + f"Peer availability: first={first}; second={second}.\n" + _FINAL,
            "slot",
            [_item("slot", _SLOTS, source["optimum"])],
            scalar=True,
            suffix="--visible",
        )
        withheld = _record(
            source,
            _FINAL_TASK,
            prefix + "Peer availability: no answer is available.\n" + _FINAL,
            "slot",
            [_item("slot", _SLOTS, source["optimum"])],
            scalar=True,
            suffix="--withheld",
        )
        finals.extend((visible, withheld) if _visible_first(record_id) else (withheld, visible))
    return _pack(finals) if finals else None
