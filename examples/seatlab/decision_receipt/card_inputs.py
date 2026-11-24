"""Load saved shipping teams as decision-card envelopes, in caller order.

`tickets` and `observed_pairs` read the 16 public ship-inflight controls. The pack must
match its frozen digest, `replay.load_source` must find the local stock and in-flight
verifier bytes equal to the native task pin, and `replay.replay` must return `pass`
before a card is built. The card grade must equal the replayed and native rewards.
Pairs follow the registered design (outgoing, relief, channel) across opposite fates,
never the observed outcomes.

`files` reads 1 or 2 supplied cut files with the bounded, no-follow plain-file reader
and the strict native parser. Any of the three cut schemas is accepted. The stock and
in-flight layers are checked against the same task pin, but the decision-receipt
citation layer is graded by this checkout's source, not by a pinned deployed package.
A supplied file names no ticket, Harbor job, native grade or deployment source. Errors
are fixed literals and never carry paths, runs, tickets or parser text.
"""

from pathlib import Path
from typing import Any, cast

from ...native_evidence import Invalid, canonical_uuid, same
from ...native_study import require
from ...ship_inflight import inflight_verify, replay
from . import archive_current, archive_inflight
from . import decision_verify as verify
from .card_model import card

Envelope = dict[str, Any]

PUBLIC = "public-scripted"
SUPPLIED = "supplied-cut"
PUBLIC_WORLD = "inflight"
WORLDS = ("stock", "inflight", "decision-receipt")
ENVELOPE_KEYS = ("ticket", "source", "native_grade", "card")
CARD_KEYS = frozenset(
    (
        "world",
        "channel",
        "axis",
        "state",
        "events",
        "citation",
        "before",
        "final",
        "twin",
    )
)
FINAL_KEYS = frozenset(("parcels", "outcome", "grade"))
DESIGN = ("outgoing", "relief", "channel")
MAX_SELECTED = 2
PAIRS = 8
LIMITS = (
    "public controls: frozen pack digest and task-pinned stock and in-flight verifiers; native grades are producer claims that the replay matches",
    "supplied cuts: stock and in-flight layers match the task pin; the decision-receipt citation layer is this checkout's source, not a deployed package",
    "a supplied cut names no ticket, Harbor job, native grade or deployment source",
)

SELECTOR = "select 1 or 2 inputs"
TICKET = "a ticket is not a registered public control"
REPEATED = "an input is selected more than once"
NOT_A_PATH = "a cut selector is not a file path"
PACK = "the public control pack or its native verifier pin cannot be checked"
UNQUALIFIED = "a public control does not replay to its registered result"
DISAGREES = "a public control card disagrees with its replayed grade"
NOT_A_CUT = "a cut file is not a valid shipping cut"
UNPAIRED = "the registered controls do not form 8 opposite-fate pairs"

assert PAIRS * 2 == replay.REGISTERED
assert set(inflight_verify.FATES) == {"settles", "fails"}
assert PUBLIC_WORLD in WORLDS and len(ENVELOPE_KEYS) == 4


def _source() -> tuple[str, str, list[dict[str, Any]]]:
    try:
        return replay.load_source(archive_inflight.QUESTION)
    except ValueError:
        raise ValueError(PACK) from None


def _public() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    question_sha, task_files, cases = _source()
    enrolled = [case["ticket"] for case in cases]
    try:
        raw = replay.read_bounded(archive_inflight.PACK, replay.MAX_PACK_BYTES, "pack")
        require(replay.sha256(raw) == archive_inflight.PACK_SHA256, PACK)
        rows = replay.parse_rows(raw, question_sha, task_files, enrolled)
    except ValueError:
        raise ValueError(PACK) from None
    require(len(cases) == replay.REGISTERED and set(rows) == set(enrolled), PACK)
    return cases, rows


def _card(cut: object, reason: str) -> dict[str, Any]:
    try:
        built = card(cut)
    except (ValueError, verify.CutError, inflight_verify.CutError):
        raise ValueError(reason) from None
    assert type(built) is dict and built.keys() == CARD_KEYS
    assert built["world"] in WORLDS and built["final"].keys() == FINAL_KEYS
    return built


def _envelope(case: dict[str, Any], rows: dict[str, dict[str, Any]]) -> Envelope:
    row = rows[case["ticket"]]
    record = replay.replay(case, row)
    require(record["status"] == "pass", UNQUALIFIED)
    reward = record["replay"]["reward"]
    native = row["native"]
    require(native["reward_valid"] is True and native["reward"] == reward, UNQUALIFIED)
    built = _card({**row["cut"], "run": replay.PLACEHOLDER_RUN}, DISAGREES)
    require(built["world"] == PUBLIC_WORLD, DISAGREES)
    require(built["final"]["grade"] == reward, DISAGREES)
    values = (case["ticket"], PUBLIC, reward, built)
    return dict(zip(ENVELOPE_KEYS, values, strict=True))


def tickets(selected: list[str]) -> list[Envelope]:
    require(type(selected) is list and 0 < len(selected) <= MAX_SELECTED, SELECTOR)
    for ticket in selected:
        require(
            type(ticket) is str and replay.TICKET.fullmatch(ticket) is not None, TICKET
        )
    require(len(set(selected)) == len(selected), REPEATED)
    cases, rows = _public()
    registered = {case["ticket"]: case for case in cases}
    require(all(ticket in registered for ticket in selected), TICKET)
    envelopes = [_envelope(registered[ticket], rows) for ticket in selected]
    assert [envelope["ticket"] for envelope in envelopes] == selected
    return envelopes


def _read(path: Path) -> bytes:
    try:
        return archive_current._read(path)
    except Invalid as error:
        missing = str(error) == archive_current.MISSING
        raise ValueError(
            archive_current.MISSING if missing else archive_current.UNREADABLE
        ) from None


def _parse(data: bytes) -> dict[str, object]:
    try:
        cut = verify.stock.parse_cut(data)
    except verify.CutError:
        raise ValueError(NOT_A_CUT) from None
    require(type(cut) is dict, NOT_A_CUT)
    return cast(dict[str, object], cut)


def files(paths: list[Path]) -> list[Envelope]:
    require(type(paths) is list and 0 < len(paths) <= MAX_SELECTED, SELECTOR)
    require(all(isinstance(path, Path) for path in paths), NOT_A_PATH)
    require(len(set(paths)) == len(paths), REPEATED)
    _source()
    cuts: list[dict[str, object]] = []
    envelopes: list[Envelope] = []
    for path in paths:
        cut = _parse(_read(path))
        built = _card(cut, NOT_A_CUT)
        require(canonical_uuid(cut.get("run")), NOT_A_CUT)
        for earlier in cuts:
            if earlier["run"] == cut["run"]:
                copied = same(earlier, cut)
                raise ValueError(
                    archive_current.COPIED if copied else archive_current.CONFLICT
                )
        cuts.append(cut)
        values = (None, SUPPLIED, None, built)
        envelopes.append(dict(zip(ENVELOPE_KEYS, values, strict=True)))
    assert len(envelopes) == len(paths) <= MAX_SELECTED
    return envelopes


def observed_pairs() -> list[tuple[Envelope, Envelope]]:
    cases, rows = _public()
    designs: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for case in cases:
        designs.setdefault(tuple(case[key] for key in DESIGN), []).append(case)
    require(len(designs) == PAIRS, UNPAIRED)
    pairs: list[tuple[Envelope, Envelope]] = []
    for members in designs.values():
        require(len(members) == 2, UNPAIRED)
        left, right = members
        require(left["ticket"] != right["ticket"], UNPAIRED)
        require({left["fate"], right["fate"]} == set(inflight_verify.FATES), UNPAIRED)
        pairs.append((_envelope(left, rows), _envelope(right, rows)))
    assert len(pairs) == PAIRS
    return pairs
