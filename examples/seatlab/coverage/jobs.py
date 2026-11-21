import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from ..capsule import _reject_constant, _unique_object
from ..visibility.clarify_run import StudyError, _is_digest
from ..visibility.route_run import _MAX_REPLY_BYTES
from .source import Entry, Frozen

_DATA = "\n\nData:\n"
_BATCH = (
    "Use the note and the current record to answer all the questions. Return only a JSON "
    'object with one field, "choices", mapping each record ID to one of the options.'
)
_POLICIES = ("batch", "checklist", "owners")
_MAX_PROMPT_BYTES = 8192


def checklist_instruction(ids: tuple[str, ...]) -> str:
    return (
        f"{_BATCH} Required answer IDs: {json.dumps(ids)}. "
        "Opening one record does not change the required answer IDs."
    )


@dataclass(frozen=True, slots=True)
class Request:
    key: str
    slot: int
    policy: str
    target: str | None
    prompt: str = field(repr=False)
    ids: tuple[str, ...]
    options: tuple[str, ...]


def _decode(text: str) -> dict[str, Any]:
    value = json.loads(
        text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return value


def _verified_data(entry: Entry, prompt: str) -> str:
    if len(prompt) > _MAX_PROMPT_BYTES:
        raise StudyError("invalid_evidence")
    instruction, separator, payload = prompt.partition(_DATA)
    if instruction != _BATCH or not separator:
        raise StudyError("invalid_evidence")
    try:
        raw = prompt.encode("utf-8")
        if (
            len(raw) > _MAX_PROMPT_BYTES
            or sha256(raw).hexdigest() != entry.prompt_sha256
        ):
            raise ValueError("invalid prompt seal")
        data = _decode(payload)
    except (TypeError, ValueError, RecursionError):
        raise StudyError("invalid_evidence") from None

    if data.keys() != {
        "brief",
        "note",
        "questions",
        "options",
        "record",
    }:
        raise StudyError("invalid_evidence")
    block = entry.block
    records = block.case.records
    note = data["note"]
    if (
        data["brief"] != block.packet.brief
        or not isinstance(note, str)
        or not note
        or not note.isprintable()
        or len(note.encode("utf-8")) > 256
        or data["questions"]
        != [{"id": record.id, "question": record.question} for record in records]
        or data["options"] != list(block.packet.choices)
        or not any(
            data["record"] == {"id": record.id, "body": record.after}
            for record in records
        )
    ):
        raise StudyError("invalid_evidence")
    return separator + payload


def _slot_jobs(entry: Entry, slot: int, prefix: str) -> list[Request]:
    prompt = entry.prompt
    if prompt is None or not _is_digest(entry.prompt_sha256):
        raise StudyError("invalid_evidence")
    data = _verified_data(entry, prompt)
    block = entry.block
    ids = tuple(record.id for record in block.case.records)
    options = block.packet.choices
    rotation = block.index + block.rep
    key = f"cv-{prefix}-{slot:03d}"
    requests: list[Request] = []
    for offset in range(len(_POLICIES)):
        policy = _POLICIES[(offset + rotation) % len(_POLICIES)]
        if policy == "batch":
            requests.append(
                Request(f"{key}-batch", slot, policy, None, prompt, ids, options)
            )
        elif policy == "checklist":
            requests.append(
                Request(
                    f"{key}-checklist",
                    slot,
                    policy,
                    None,
                    checklist_instruction(ids) + data,
                    ids,
                    options,
                )
            )
        else:
            for owner_offset in range(len(ids)):
                index = (owner_offset + rotation) % len(ids)
                target = ids[index]
                instruction = (
                    "Use the note and the current record to answer only the question for "
                    f"record ID {json.dumps(target)}. Return only a JSON object with one "
                    'field, "choices", mapping that record ID to one of the options. '
                    "Include no other record IDs."
                )
                requests.append(
                    Request(
                        f"{key}-owner{index}",
                        slot,
                        policy,
                        target,
                        instruction + data,
                        (target,),
                        options,
                    )
                )
    return requests


def make(frozen: Frozen) -> tuple[Request, ...]:
    if (
        not _is_digest(frozen.sha256)
        or not _is_digest(frozen.packet_sha256)
        or len(frozen.entries) > 1000
    ):
        raise StudyError("invalid_evidence")
    requests: list[Request] = []
    for slot, entry in enumerate(frozen.entries):
        if entry.block.fingerprint != frozen.packet_sha256:
            raise StudyError("invalid_evidence")
        if entry.status == "ready":
            requests.extend(_slot_jobs(entry, slot, frozen.sha256[:12]))
        elif (
            entry.status not in {"no_note", "no_lookup", "no_request"}
            or entry.prompt is not None
            or entry.prompt_sha256 is not None
        ):
            raise StudyError("invalid_evidence")
    return tuple(requests)


def answer(request: Request, text: str) -> dict[str, str] | None:
    if len(text) > _MAX_REPLY_BYTES:
        return None
    if request.policy == "owners" and (
        request.target is None or request.ids != (request.target,)
    ):
        return None
    try:
        if len(text.encode("utf-8")) > _MAX_REPLY_BYTES:
            return None
        value = _decode(text)
    except (TypeError, ValueError, RecursionError):
        return None
    if value.keys() != {"choices"}:
        return None
    choices = value["choices"]
    if not isinstance(choices, dict) or choices.keys() != set(request.ids):
        return None
    result: dict[str, str] = {}
    for key in request.ids:
        choice = choices[key]
        if not isinstance(choice, str) or choice not in request.options:
            return None
        result[key] = choice
    return result
