import json
from dataclasses import replace
from hashlib import sha256

import pytest

from examples.seatlab.coverage.jobs import answer, make
from examples.seatlab.coverage.source import Entry, Frozen
from examples.seatlab.handoff.episode import Turn, episode
from examples.seatlab.handoff.packet import Block, Case, Packet, Record, grid
from examples.seatlab.visibility.clarify_run import StudyError

_DATA = "\n\nData:\n"
_IDS = ("ZX", "P_1", "M", "W5")
_OPTIONS = ("B2", "T", "A")
_SOURCE_SHA = "abcdef0123456789" * 4
_PACKET_SHA = "9" * 64


def _original(block: Block, *, space: bool = False) -> Entry:
    finals: list[str] = []

    def ask(turn: Turn) -> str | None:
        if turn.stage == "note":
            return '{"note":"Mémo from the writer"}'
        if turn.stage.endswith("-select"):
            return '{"read":"M"}'
        assert turn.stage == "both-decide"
        finals.append(turn.prompt)
        return None

    episode(block, ask)
    (prompt,) = finals
    if space:
        prompt = prompt.replace(_DATA + "{", _DATA + "  {\n", 1) + "\n"
    return Entry(block, "ready", prompt, sha256(prompt.encode("utf-8")).hexdigest())


def _frozen() -> Frozen:
    records = tuple(
        Record(
            id=ident,
            old_version="old",
            new_version="new" if ident == "M" else "old",
            before="OLD-STOCK-M" if ident == "M" else f"UNOPENED-{ident}",
            after="OPENED-STOCK-M" if ident == "M" else f"UNOPENED-{ident}",
            question=f"Where should shipment {ident} go?",
            old_best="B2",
            best="A" if ident == "M" else "B2",
        )
        for ident in _IDS
    )
    cases = tuple(Case(f"case-{index}", "world", records) for index in range(3))
    blocks = grid(Packet("Public dispatch brief", _OPTIONS, cases, _PACKET_SHA))
    return Frozen(
        _PACKET_SHA,
        _SOURCE_SHA,
        (
            _original(blocks[0]),
            _original(blocks[1]),
            Entry(blocks[2], "no_note", None, None),
            _original(blocks[3], space=True),
            Entry(blocks[4], "no_lookup", None, None),
            Entry(blocks[5], "no_request", None, None),
        ),
    )


def test_coverage_jobs_rotate_and_keep_identical_evidence() -> None:
    frozen = _frozen()
    requests = make(frozen)
    expected = {
        0: ("batch", "checklist", "owner0", "owner1", "owner2", "owner3"),
        1: ("checklist", "owner1", "owner2", "owner3", "owner0", "batch"),
        3: ("owner2", "owner3", "owner0", "owner1", "batch", "checklist"),
    }
    assert [(request.slot, request.key) for request in requests] == [
        (slot, f"cv-abcdef012345-{slot:03d}-{suffix}")
        for slot, suffixes in expected.items()
        for suffix in suffixes
    ]
    for slot in expected:
        original = frozen.entries[slot].prompt
        assert original is not None
        original_data = original.split(_DATA, 1)[1]
        assert json.loads(original_data) == {
            "brief": "Public dispatch brief",
            "note": "Mémo from the writer",
            "questions": [
                {"id": ident, "question": f"Where should shipment {ident} go?"}
                for ident in _IDS
            ],
            "options": list(_OPTIONS),
            "record": {"id": "M", "body": "OPENED-STOCK-M"},
        }
        seen_targets: set[str] = set()
        for request in requests:
            if request.slot != slot:
                continue
            assert request.options == _OPTIONS
            head, data = request.prompt.split(_DATA, 1)
            assert data.encode("utf-8") == original_data.encode("utf-8")
            assert "UNOPENED" not in request.prompt
            assert "OLD-STOCK" not in request.prompt
            assert "both-decide" not in request.prompt
            if request.policy == "batch":
                assert request.prompt.encode("utf-8") == original.encode("utf-8")
                assert request.target is None and request.ids == _IDS
            elif request.policy == "checklist":
                assert json.dumps(_IDS) in head
                assert (
                    "Opening one record does not change the required answer IDs" in head
                )
                assert request.target is None and request.ids == _IDS
            else:
                assert request.policy == "owners"
                index = int(request.key.rsplit("owner", 1)[1])
                target = request.target
                assert target is not None and target == _IDS[index]
                assert target not in seen_targets
                seen_targets.add(target)
                assert request.ids == (target,)
                assert {ident for ident in _IDS if json.dumps(ident) in head} == {
                    target
                }
        assert seen_targets == set(_IDS)


def _reseal(frozen: Frozen, prompt: str) -> Frozen:
    entry = replace(
        frozen.entries[0],
        prompt=prompt,
        prompt_sha256=sha256(prompt.encode("utf-8")).hexdigest(),
    )
    return replace(frozen, entries=(entry, *frozen.entries[1:]))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("arm", "bound"),
        ("brief", "private brief"),
        ("note", ""),
        ("note", "é" * 129),
        ("options", ["T", "B2", "A"]),
        ("questions", [{"id": "ZX", "question": "private question"}]),
        ("record", {"id": "M", "body": "OLD-STOCK-M"}),
        ("record", {"id": "M", "body": "UNOPENED-ZX"}),
        ("record", {"id": "ghost", "body": "OPENED-STOCK-M"}),
        ("record", {"id": "M", "body": "OPENED-STOCK-M", "best": "A"}),
    ],
)
def test_coverage_jobs_reject_resealed_scope_changes(
    field_name: str, value: object
) -> None:
    frozen = _frozen()
    prompt = frozen.entries[0].prompt
    assert prompt is not None
    instruction, payload = prompt.split(_DATA, 1)
    data: dict[str, object] = json.loads(payload)
    data[field_name] = value
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        make(_reseal(frozen, instruction + _DATA + json.dumps(data)))


def test_coverage_jobs_reject_unsealed_or_duplicate_evidence() -> None:
    frozen = _frozen()
    original = frozen.entries[0]
    assert original.prompt is not None
    changed = replace(original, prompt=original.prompt + "\n")
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        make(replace(frozen, entries=(changed, *frozen.entries[1:])))
    duplicate = original.prompt.replace(_DATA + "{", _DATA + '{"note":"extra",', 1)
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        make(_reseal(frozen, duplicate))
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        make(replace(frozen, sha256="0" * 63))


def test_coverage_jobs_parse_distinct_valid_deliveries() -> None:
    batch, checklist, *owners = make(_frozen())[:6]
    batch_choices = {"ZX": "A", "P_1": "T", "M": "B2", "W5": "T"}
    checklist_choices = {"ZX": "T", "P_1": "B2", "M": "A", "W5": "B2"}
    assert answer(batch, json.dumps({"choices": batch_choices})) == batch_choices
    parsed_checklist = answer(checklist, json.dumps({"choices": checklist_choices}))
    assert parsed_checklist == checklist_choices
    owner_choices = {"ZX": "B2", "P_1": "A", "M": "T", "W5": "A"}
    combined: dict[str, str] = {}
    for owner in owners:
        target = owner.target
        assert target is not None
        value = {target: owner_choices[target]}
        parsed = answer(owner, json.dumps({"choices": value}))
        assert parsed is not None and parsed == value
        combined.update(parsed)
        assert answer(owner, json.dumps({"choices": owner_choices})) is None
        other = "W5" if target == "ZX" else "ZX"
        assert answer(owner, json.dumps({"choices": {other: "T"}})) is None
    assert combined == owner_choices


@pytest.mark.parametrize(
    "text",
    [
        '{"choices":{"M":"A"}}',
        '{"choices":{"ZX":"S","P_1":"A","M":"T","W5":"B2"}}',
        '{"choices":{"ZX":"T","P_1":"A","M":"T","W5":"B2","X":"A"}}',
        '{"choices":{"ZX":"T","P_1":"A","M":"T","W5":"B2"},"note":"x"}',
        '{"choices":{},"choices":{"ZX":"T","P_1":"A","M":"T","W5":"B2"}}',
        r'{"choices":{"ZX":"T","P_1":"A","M":"T","\u004d":"A","W5":"B2"}}',
        '{"choices":{"ZX":"T","P_1":"A","M":NaN,"W5":"B2"}}',
        '{"choices":{"ZX":"T","P_1":"A","M":1e999,"W5":"B2"}}',
        '{"choices":{"ZX":"T","P_1":"A","M":true,"W5":"B2"}}',
        '{"choices":[["M","T"]]}',
        '```json\n{"choices":{"ZX":"T","P_1":"A","M":"T","W5":"B2"}}\n```',
        '{"choices":{"ZX":"T","P_1":"A","M":"T","W5":"B2"}} {}',
        '{"choices":{"ZX":"T","P_1":"A","M":"T","W5":"B2"}}' + " " * 8192,
    ],
)
def test_coverage_jobs_reject_invalid_whole_maps(text: str) -> None:
    assert answer(make(_frozen())[0], text) is None
