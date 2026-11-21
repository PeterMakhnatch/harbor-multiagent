from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
from itertools import chain
from typing import Any, Literal
from uuid import UUID

from . import dispatch_peer_case
from .visibility import calendar_list_packet, calendar_list_protocol


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    task: Literal["calendar", "dispatch"]
    world: int
    trial_sha256: str
    prompt: str
    prompt_sha256: str
    system: str
    units: int
    _grade: Callable[[str], int] = field(repr=False, compare=False)

    def score(self, text: str) -> int:
        if type(text) is not str:
            raise ValueError("Expected a text receiver reply.")
        return self._grade(text)


def _calendar_cases() -> Iterator[Case]:
    for source in calendar_list_packet.load():
        prompt = calendar_list_protocol.prompt(source, "rr")
        if sha256(prompt.encode("utf-8")).hexdigest() != source.prompt_sha256:
            raise ValueError(f"Historical Calendar prompt mismatch: {source.id}")
        yield Case(
            id=f"calendar/{source.id}",
            task="calendar",
            world=source.world,
            trial_sha256=source.trial_id_sha256,
            prompt=prompt,
            prompt_sha256=source.prompt_sha256,
            system=dispatch_peer_case.SYSTEM_PROMPT,
            units=1,
            _grade=partial(calendar_list_protocol.grade, source),
        )


def _dispatch_score(world_class: Any, source: dict[str, Any], text: str) -> int:
    if len(text) > dispatch_peer_case._REPLY_LIMIT:
        raise ValueError("Receiver reply exceeds 64 KiB.")
    reply = text.encode("utf-8")
    world = dispatch_peer_case._prepared_world(world_class, source)
    return dispatch_peer_case._score_reply(world, source["world"], reply)["optimal"]


def _dispatch_cases() -> Iterator[Case]:
    world_class = dispatch_peer_case._world_class()
    for seed, source in dispatch_peer_case._sources().items():
        _, (shown, _) = dispatch_peer_case._case(world_class, source)
        canonical_trial_id = str(UUID(source["source_uuid"]))
        yield Case(
            id=f"dispatch/{seed}",
            task="dispatch",
            world=seed,
            trial_sha256=sha256(canonical_trial_id.encode("ascii")).hexdigest(),
            prompt=shown,
            prompt_sha256=source["shown_prompt_sha256"],
            system=dispatch_peer_case.SYSTEM_PROMPT,
            units=dispatch_peer_case._EPISODES,
            _grade=partial(_dispatch_score, world_class, source),
        )


def registry() -> dict[str, Case]:
    cases = sorted(
        chain(_calendar_cases(), _dispatch_cases()), key=lambda case: case.id
    )
    result = {case.id: case for case in cases}
    if len(result) != 32:
        raise ValueError("Unexpected factual receiver panel size.")
    return result
