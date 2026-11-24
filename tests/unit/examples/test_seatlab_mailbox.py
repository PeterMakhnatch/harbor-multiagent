import copy
import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

import pytest

from examples.seatlab.fanin import (
    evidence,
    fanout_panel,
    mailbox,
    mailbox_events,
    mailbox_panel,
    panel,
)
from examples.seatlab.visibility import calendar_protocol

Json = dict[str, Any]
Change = Callable[[Any], None]
SEATS = evidence.SEATS
STAGES = mailbox_panel.STAGES
VIEW = ("edges", "submit", "through")
REPORTERS = ("p2", "p3", "p4")
BASE_MS = 5000
OPENING_MS = -250
REQUEST = mailbox_events.REQUEST_CHARS
REPLY = 41
SECRET = "SECRET-ROOM-7"
OFFICIAL = len(fanout_panel.WORLDS) * len(fanout_panel.ARMS) + len(
    fanout_panel.CONTROLS
)


def _event(kind: str, seat: str | None, ms: int, data: Json | None = None) -> Json:
    return {
        "kind": kind,
        "seat": seat,
        "t": (BASE_MS + ms) / 1000,
        "data": {} if data is None else data,
    }


def _opening() -> list[Json]:
    start = _event("start", None, OPENING_MS, copy.deepcopy(evidence.WORLD))
    return [start, *(_event("whoami", seat, OPENING_MS) for seat in SEATS)]


def _send(seat: str, ms: int, mid: str, to: str, chars: int = REQUEST) -> Json:
    return _event("send", seat, ms, {"id": mid, "to": to, "chars": chars})


def _poll(seat: str, ms: int, *ids: str) -> Json:
    return _event("deliver", seat, ms, {"ids": list(ids)})


def _book(seat: str, ms: int) -> Json:
    return _event("submit", seat, ms, {"n": 1, "answer": calendar_protocol.answer(0)})


SERIAL = [
    *_opening(),
    _send("p1", 0, "m1", "p2"),
    _poll("p2", 4, "m1"),
    _poll("p2", 900, "m1"),
    _send("p2", 3200, "m2", "p1", REPLY),
    _poll("p1", 3201, "m2"),
    _poll("p1", 3500, "m2"),
    _send("p1", 3600, "m3", "p3"),
    _poll("p3", 3600, "m3"),
    _send("p3", 7000, "m4", "p1", REPLY),
    _event("health", None, 7000),
    _poll("p1", 7100, "m2", "m4"),
    _send("p1", 7300, "m5", "p4"),
    _poll("p4", 7350, "m5"),
    _send("p4", 9900, "m6", "p1", REPLY),
    _poll("p1", 9950, "m2", "m4", "m6"),
    _book("p1", 10400),
    _poll("p4", 10500, "m5"),
]

PARALLEL = [
    *_opening(),
    _send("p1", 0, "m1", "p2"),
    _send("p1", 0, "m2", "p3"),
    _send("p1", 1, "m3", "p4"),
    _poll("p1", 2),
    _poll("p3", 40, "m2"),
    _poll("p2", 41, "m1"),
    _poll("p4", 41, "m3"),
    _send("p2", 2500, "m4", "p1", REPLY),
    _poll("p1", 2600, "m4"),
    _send("p4", 3100, "m5", "p1", REPLY),
    _send("p3", 3300, "m6", "p1", REPLY),
    _poll("p1", 3400, "m4", "m5", "m6"),
    _poll("p1", 3700, "m4", "m5", "m6"),
    _book("p1", 4000),
]


def _numbered(rows: list[Json]) -> list[Json]:
    assert 0 < len(rows) <= mailbox_events.MAX_EVENTS
    return [{"seq": seq, **row} for seq, row in enumerate(rows, 1)]


def _encode(numbered: list[Json]) -> bytes:
    return b"".join(
        json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in numbered
    )


def _bytes(rows: list[Json]) -> bytes:
    return _encode(_numbered(rows))


def _project_raw(
    raw: bytes, booker: str = "p1", reporters: tuple[str, ...] = REPORTERS
) -> Json:
    digest = hashlib.sha256(raw).hexdigest()
    return mailbox_events.project(
        raw, expected_sha256=digest, booker=booker, reporters=reporters
    )


def _project(
    rows: list[Json], booker: str = "p1", reporters: tuple[str, ...] = REPORTERS
) -> Json:
    return _project_raw(_bytes(rows), booker, reporters)


def _view(cut: Json) -> Json:
    return {key: cut[key] for key in VIEW}


def _refused(
    category: str,
    call: Callable[[], object],
    error: type[ValueError] = mailbox_events.Bad,
) -> ValueError:
    with pytest.raises(error) as refused:
        call()
    assert refused.value.args == (category,)
    return refused.value


def _oracle(rows: list[Json], booker: str, reporters: tuple[str, ...]) -> Json:
    found = {seat: dict.fromkeys(STAGES) for seat in reporters}
    sends = [row for row in rows if row["kind"] == "send" and row["seat"] == booker]
    if not sends:
        edges = [{"seat": seat, **found[seat]} for seat in reporters]
        return {"edges": edges, "submit": None, "through": None}
    origin = round(sends[0]["t"] * 1000)
    owner: dict[str, tuple[str, str]] = {}
    submit = None
    for seq, row in enumerate(rows, 1):
        at = [seq, round(row["t"] * 1000) - origin]
        if row["kind"] == "send":
            stage = "request" if row["seat"] == booker else "reply"
            reporter = row["data"]["to"] if stage == "request" else row["seat"]
            owner[row["data"]["id"]] = (reporter, f"{stage}_inbox")
            found[reporter][stage] = at
        elif row["kind"] == "deliver":
            for mid in row["data"]["ids"]:
                reporter, stage = owner[mid]
                if found[reporter][stage] is None:
                    found[reporter][stage] = at
        elif row["kind"] == "submit":
            submit = at
    last = [len(rows), round(rows[-1]["t"] * 1000) - origin]
    edges = [{"seat": seat, **found[seat]} for seat in reporters]
    return {"edges": edges, "submit": submit, "through": last}


def _step(
    stage: str, seat: str, ms: int, booker: str, inbox: dict[str, list[str]]
) -> Json:
    if stage == "submit":
        return _book(booker, ms)
    if stage in ("request", "reply"):
        mid = f"m{sum(map(len, inbox.values())) + 1}"
        sender, to = (booker, seat) if stage == "request" else (seat, booker)
        inbox[to].append(mid)
        return _send(sender, ms, mid, to, REQUEST if stage == "request" else REPLY)
    reader = seat if stage == "request_inbox" else booker
    return _poll(reader, ms, *inbox[reader])


def _realize(cut: Json) -> list[Json]:
    booker = cut["booker"]
    marks: dict[int, list[tuple[str, str, int]]] = {}
    for edge in cut["edges"]:
        for stage in STAGES:
            if edge[stage] is not None:
                marks.setdefault(edge[stage][0], []).append(
                    (stage, edge["seat"], edge[stage][1])
                )
    if cut["submit"] is not None:
        marks.setdefault(cut["submit"][0], []).append(
            ("submit", booker, cut["submit"][1])
        )
    rows = _opening()
    end, end_ms = cut["through"]
    assert len(rows) < min(marks) and max(marks) <= end <= mailbox_events.MAX_EVENTS
    inbox: dict[str, list[str]] = {seat: [] for seat in SEATS}
    ms = OPENING_MS
    for seq in range(len(rows) + 1, end + 1):
        here = marks.get(seq, [])
        assert len(here) <= 1 or {stage for stage, _, _ in here} == {"reply_inbox"}
        if not here:
            ms = end_ms if seq == end else ms
            rows.append(_event("health", None, ms))
            continue
        stage, seat, ms = here[0]
        rows.append(_step(stage, seat, ms, booker, inbox))
    return rows


def _repoll(rows: list[Json]) -> list[Json]:
    polled = []
    for row in rows:
        polled.append(row)
        if row["kind"] == "deliver":
            polled.append(copy.deepcopy(row))
    return polled


def _official() -> tuple[Json, list[Json]]:
    release = panel.load(mailbox.ROWS)
    raw = mailbox.CUTS.read_bytes()
    assert len(raw) <= mailbox.MAX_DOCUMENT
    bundle = json.loads(raw)
    assert isinstance(release, dict)
    assert bundle.keys() == {"schema", "cuts"} and bundle["schema"] == mailbox.BUNDLE
    assert len(bundle["cuts"]) == OFFICIAL
    return cast(Json, release), bundle["cuts"]


def _rows(release: Json) -> list[Json]:
    return [*release["rows"], *release["controls"]]


def _key(row: Json) -> tuple[int, str, str]:
    return row["world"], row["arm"], row["tag"]


def _cut_of(release: Json, cuts: list[Json], team: tuple[int, str, str]) -> Json:
    digest = next(row for row in _rows(release) if _key(row) == team)["inputs"][
        "events"
    ]
    return copy.deepcopy(next(cut for cut in cuts if cut["events_sha256"] == digest))


def _structural(cut: Json) -> None:
    release, _ = _official()
    _refused(
        "cut_foreign",
        lambda: mailbox_panel.summarize(release, [cut]),
        mailbox_panel.Rejected,
    )


def test_repeated_polls_keep_the_first_cumulative_return() -> None:
    cut = _project(SERIAL)
    assert _view(cut) == _oracle(SERIAL, "p1", REPORTERS)
    edges = {edge["seat"]: edge for edge in cut["edges"]}
    assert edges["p2"]["request_inbox"] == [7, 4]
    assert edges["p2"]["reply_inbox"] == [10, 3201]
    assert edges["p3"]["request"][1] == edges["p3"]["request_inbox"][1] == 3600
    repeats = (8, 11, len(SERIAL))
    points = [edge[stage] for edge in cut["edges"] for stage in STAGES]
    assert all(point[0] not in repeats for point in points)
    assert cut["through"] == [len(SERIAL), 10500]
    trimmed = [row for seq, row in enumerate(SERIAL, 1) if seq not in repeats]
    once = _project(trimmed)
    assert [[edge[stage][1] for stage in STAGES] for edge in once["edges"]] == [
        [edge[stage][1] for stage in STAGES] for edge in cut["edges"]
    ]
    assert once["submit"][1] == cut["submit"][1]


def test_repeated_polls_shift_only_sequence_numbers_in_every_public_cut() -> None:
    _, cuts = _official()
    for cut in cuts:
        booker, reporters = cut["booker"], tuple(cut["reporters"])
        rows = _realize(cut)
        polled = _repoll(rows)
        projected = _project(polled, booker, reporters)
        assert _view(projected) == _oracle(polled, booker, reporters)

        def shifted(point: list[int] | None, journal: list[Json]) -> list[int] | None:
            if point is None:
                return None
            earlier = sum(row["kind"] == "deliver" for row in journal[: point[0] - 1])
            return [point[0] + earlier, point[1]]

        for old, new in zip(cut["edges"], projected["edges"], strict=True):
            assert all(new[stage] == shifted(old[stage], rows) for stage in STAGES)
        assert projected["submit"] == shifted(cut["submit"], rows)
        assert projected["through"] == [len(polled), cut["through"][1]]


def test_one_booker_snapshot_returns_several_replies_at_one_point() -> None:
    cut = _project(PARALLEL)
    assert _view(cut) == _oracle(PARALLEL, "p1", REPORTERS)
    edges = {edge["seat"]: edge for edge in cut["edges"]}
    assert edges["p3"]["reply_inbox"] == edges["p4"]["reply_inbox"] == [17, 3400]
    assert edges["p2"]["reply_inbox"] == [14, 2600]
    assert [edges[seat]["request"] for seat in REPORTERS] == [[6, 0], [7, 0], [8, 1]]
    assert edges["p2"]["request_inbox"][1] == edges["p4"]["request_inbox"][1] == 41
    assert cut["submit"] == cut["through"] == [19, 4000]
    _structural(cut)


def test_zero_time_capture_orders_by_sequence_alone() -> None:
    rows = [row | {"t": BASE_MS / 1000} for row in PARALLEL]
    cut = _project(rows)
    assert _view(cut) == _oracle(rows, "p1", REPORTERS)
    points = [edge[stage] for edge in cut["edges"] for stage in STAGES]
    assert {point[1] for point in points} == {0} and cut["through"] == [len(rows), 0]
    assert [edge[stage][0] for edge in cut["edges"] for stage in STAGES] == [
        edge[stage][0] for edge in _project(PARALLEL)["edges"] for stage in STAGES
    ]
    _structural(cut)


def test_capture_without_a_request_has_no_origin() -> None:
    rows = [*_opening(), _poll("p1", OPENING_MS), _event("health", None, 0)]
    cut = _project(rows)
    assert cut["through"] is None and cut["submit"] is None
    assert all(edge[stage] is None for edge in cut["edges"] for stage in STAGES)
    assert _view(cut) == _oracle(rows, "p1", REPORTERS)
    _structural(cut)


def test_cutoff_after_any_complete_line_is_the_earlier_cut() -> None:
    _, cuts = _official()

    def until(point: list[int] | None, seq: int) -> list[int] | None:
        return point if point is not None and point[0] <= seq else None

    for cut in cuts:
        booker, reporters = cut["booker"], tuple(cut["reporters"])
        rows = _realize(cut)
        for seq in range(1, len(rows) + 1):
            part = _project(rows[:seq], booker, reporters)
            assert _view(part) == _oracle(rows[:seq], booker, reporters)
            assert part["edges"] == [
                {
                    "seat": edge["seat"],
                    **{stage: until(edge[stage], seq) for stage in STAGES},
                }
                for edge in cut["edges"]
            ]
            assert part["submit"] == until(cut["submit"], seq)


def test_observed_absence_is_a_cut_while_lost_or_faulted_captures_are_refused() -> None:
    rows = SERIAL[:13]
    cut = _project(rows)
    assert _view(cut) == _oracle(rows, "p1", REPORTERS)
    stages = [sum(edge[stage] is not None for stage in STAGES) for edge in cut["edges"]]
    assert stages == [4, 2, 0] and cut["submit"] is None
    assert cut["through"] == [13, 3600]
    _structural(cut)
    raw = _bytes(SERIAL)
    for broken in (b"", raw[:-6], raw.replace(b"\n", b"\n\n", 1), b"\n"):
        _refused("events_framing", lambda broken=broken: _project_raw(broken))


def _edit(seq: int, **data: object) -> Change:
    return lambda rows: rows[seq - 1]["data"].update(data)


def _field(seq: int, key: str, value: object) -> Change:
    return lambda rows: rows[seq - 1].__setitem__(key, value)


def _drop(*seqs: int) -> Change:
    def change(rows: list[Json]) -> None:
        for seq in sorted(seqs, reverse=True):
            del rows[seq - 1]

    return change


def _insert(seq: int, row: Json) -> Change:
    return lambda rows: rows.insert(seq - 1, copy.deepcopy(row))


JOURNAL_REFUSALS = [
    ("request_to_other_reporter", _edit(6, to="p3"), "request_binding"),
    ("request_wrong_size", _edit(6, chars=REQUEST + 1), "request_binding"),
    ("reply_to_other_reporter", _edit(9, to="p3"), "reply_binding"),
    ("message_id_skipped", _edit(9, id="m3"), "send_binding"),
    ("reply_before_request_returned", _drop(7, 8), "reply_before_request"),
    (
        "duplicate_reply",
        _insert(10, _send("p2", 3200, "m3", "p1", REPLY)),
        "duplicate_reply",
    ),
    ("booking_before_last_return", _drop(20), "submit_before_gather"),
    ("booking_by_reporter", _field(21, "seat", "p2"), "submit_before_gather"),
    ("snapshot_out_of_order", _edit(16, ids=["m4", "m2"]), "deliver_binding"),
    ("snapshot_not_cumulative", _edit(16, ids=["m4"]), "deliver_binding"),
    ("snapshot_ahead_of_world", _edit(7, ids=["m1", "m2"]), "deliver_binding"),
    ("snapshot_of_other_inbox", _edit(13, ids=["m1"]), "deliver_binding"),
    (
        "booking_after_freeze",
        _insert(21, _event("freeze", "admin", 10300)),
        "late_mutation",
    ),
    ("second_booking", _insert(22, _book("p1", 10450)), "late_mutation"),
    ("answer_as_text", _edit(21, answer="Mon 09:00"), "submit_event_binding"),
    (
        "answer_off_calendar",
        _edit(21, answer={"day": "Mon", "start": "16:00"}),
        "submit_event_binding",
    ),
    ("actor_without_whoami", _drop(5), "world_event_actor"),
    ("second_whoami", _insert(6, _event("whoami", "p2", OPENING_MS)), "world_identity"),
    ("health_with_seat", _field(15, "seat", "p3"), "world_health"),
    ("clock_backwards", _field(9, "t", 4.0), "world_event_sequence"),
    ("extra_envelope_key", _field(6, "note", "x"), "world_event_sequence"),
    ("unknown_kind", _field(9, "kind", "broadcast"), "unexpected_world_event"),
    ("start_differs", _field(1, "data", {}), "world_start"),
]


@pytest.mark.parametrize(
    ("change", "category"),
    [
        pytest.param(change, category, id=name)
        for name, change, category in JOURNAL_REFUSALS
    ],
)
def test_projector_refuses_broken_causal_chains(change: Change, category: str) -> None:
    rows = copy.deepcopy(SERIAL)
    change(rows)
    _refused(category, lambda: _project(rows))


def test_projector_refuses_gapped_or_repeated_sequence() -> None:
    for seq, value, category in (
        (6, 7, "world_event_sequence"),
        (6, 5, "world_event_sequence"),
        (1, 2, "world_event_sequence"),
    ):
        numbered = _numbered(SERIAL)
        numbered[seq - 1]["seq"] = value
        _refused(category, lambda numbered=numbered: _project_raw(_encode(numbered)))
    swapped = _numbered([SERIAL[1], SERIAL[0], *SERIAL[2:]])
    _refused("world_start", lambda: _project_raw(_encode(swapped)))


@pytest.mark.parametrize(
    ("booker", "reporters", "category"),
    [
        pytest.param(
            "p1", ("p3", "p2", "p4"), "request_binding", id="reporter_order_swapped"
        ),
        pytest.param(
            "p2", ("p1", "p3", "p4"), "reply_before_request", id="booker_swapped"
        ),
        pytest.param("p1", ("p2", "p3", "p3"), "mailbox_roles", id="reporter_repeated"),
        pytest.param(
            "p1", ["p2", "p3", "p4"], "mailbox_roles", id="reporters_not_tuple"
        ),
        pytest.param("p5", ("p2", "p3", "p4"), "mailbox_roles", id="seat_outside_team"),
    ],
)
def test_projector_refuses_swapped_roles(
    booker: str, reporters: Any, category: str
) -> None:
    _refused(category, lambda: _project(SERIAL, booker, reporters))


def test_projector_refuses_wrong_digest_and_unbounded_input() -> None:
    raw = _bytes(SERIAL)
    other = hashlib.sha256(raw + b"\n").hexdigest()

    def project(data: Any, digest: str) -> Json:
        return mailbox_events.project(
            data, expected_sha256=digest, booker="p1", reporters=REPORTERS
        )

    _refused("events_digest", lambda: project(raw, other))
    _refused(
        "mailbox_arguments",
        lambda: project(raw, hashlib.sha256(raw).hexdigest().upper()),
    )
    _refused(
        "mailbox_arguments",
        lambda: project(bytearray(raw), hashlib.sha256(raw).hexdigest()),
    )
    oversize = b"{" + b" " * mailbox_events.MAX_BYTES + b"}"
    _refused("events_size", lambda: project(oversize, "0" * 64))
    long_line = (
        _bytes(SERIAL[:1]) + b'{"pad":"' + b"a" * mailbox_events.MAX_LINE + b'"}\n'
    )
    _refused("events_framing", lambda: _project_raw(long_line))
    _refused(
        "events_count", lambda: _project_raw(b"{}\n" * (mailbox_events.MAX_EVENTS + 1))
    )
    _refused("events_framing", lambda: _project_raw(raw.replace(b"\n", b"\r\n", 1)))


def test_refusals_never_echo_journal_text() -> None:
    head = _bytes(SERIAL[:1])
    note = SECRET.encode()
    broken = (
        head
        + b'{"seq":2,"kind":"whoami","seat":"p1","t":4.75,"data":{"n":"'
        + note
        + b'"},"seq":2}\n',
        head + b'{"seq":2,"note":"' + note + b'","t":NaN}\n',
        head + b'{"seq":2,"note":"' + note + b'\xff"}\n',
    )
    errors = [
        _refused("events_json", lambda raw=raw: _project_raw(raw)) for raw in broken
    ]
    rows = copy.deepcopy(SERIAL)
    rows[20]["data"]["answer"] = {"day": SECRET, "start": "09:00"}
    errors.append(_refused("submit_event_binding", lambda: _project(rows)))
    rows = copy.deepcopy(SERIAL)
    rows[8]["data"]["to"] = SECRET
    errors.append(_refused("reply_binding", lambda: _project(rows)))
    for error in errors:
        assert SECRET not in str(error) and SECRET not in repr(error)
        assert error.__cause__ is None


def test_every_public_cut_is_realized_by_a_journal_the_projector_accepts() -> None:
    release, cuts = _official()
    official = mailbox_panel.summarize(release, cuts)
    rebuilt = []
    for cut in cuts:
        projected = _project(_realize(cut), cut["booker"], tuple(cut["reporters"]))
        assert projected.keys() == mailbox_panel.CUT_KEYS
        assert projected == cut | {"events_sha256": projected["events_sha256"]}
        rebuilt.append(projected)
    swap = {
        old["events_sha256"]: new["events_sha256"] for old, new in zip(cuts, rebuilt)
    }
    for row in _rows(release):
        row["inputs"]["events"] = swap[row["inputs"]["events"]]
    report = mailbox_panel.summarize(release, rebuilt)
    assert report["arms"] == official["arms"]
    identities = {}
    for cut in (*cuts, *rebuilt):
        encoded = json.dumps(cut, sort_keys=True, separators=(",", ":")).encode("ascii")
        identities[cut["events_sha256"]] = hashlib.sha256(encoded).hexdigest()
    for old, new in zip(official["teams"], report["teams"], strict=True):
        assert swap[old["events_sha256"]] == new["events_sha256"]
        assert old["cut_sha256"] != new["cut_sha256"]
        assert old["cut_sha256"] == identities[old["events_sha256"]]
        assert new["cut_sha256"] == identities[new["events_sha256"]]
        assert new == old | {
            "events_sha256": new["events_sha256"],
            "cut_sha256": new["cut_sha256"],
        }


def test_all_public_cuts_join_their_release_rows_one_to_one() -> None:
    release, cuts = _official()
    report = mailbox_panel.summarize(release, cuts)
    digests = sorted(row["inputs"]["events"] for row in _rows(release))
    assert len(digests) == len(set(digests)) == OFFICIAL
    assert sorted(cut["events_sha256"] for cut in cuts) == digests
    recorded = [t for t in report["teams"] if t["coverage"] == "recorded"]
    assert len(report["teams"]) == OFFICIAL
    assert sorted(t["events_sha256"] for t in recorded) == digests
    assert mailbox_panel.summarize(release, cuts[::-1]) == report
    by_digest = {cut["events_sha256"]: cut for cut in cuts}
    for arm in fanout_panel.ARMS:
        mine = [
            by_digest[row["inputs"]["events"]]
            for row in release["rows"]
            if row["arm"] == arm
        ]
        summary = report["arms"][arm]
        assert summary["teams"] == summary["recorded_teams"] == len(fanout_panel.WORLDS)
        assert summary["edges_reached"] == {
            stage: sum(edge[stage] is not None for cut in mine for edge in cut["edges"])
            for stage in STAGES
        }
        assert summary["teams_submitted"] == sum(
            cut["submit"] is not None for cut in mine
        )
    for team in recorded:
        counts = [team["stage_counts"][stage] for stage in STAGES]
        assert counts == sorted(counts, reverse=True)


def test_tagged_controls_join_without_entering_model_arms() -> None:
    release, cuts = _official()
    report = mailbox_panel.summarize(release, cuts)
    controls = [t for t in report["teams"] if t["control"]]
    assert {_key(t) for t in controls} == set(fanout_panel.CONTROLS)
    assert all(t["coverage"] == "recorded" and t["tag"] != panel.LIVE for t in controls)
    tagged = {row["inputs"]["events"] for row in release["controls"]}
    live_only = mailbox_panel.summarize(
        release, [cut for cut in cuts if cut["events_sha256"] not in tagged]
    )
    assert live_only["arms"] == report["arms"]
    for team in live_only["teams"]:
        if team["control"]:
            assert team["coverage"] == "missing" and team["stages"] is None
    assert sum(
        report["arms"][arm]["teams"] for arm in fanout_panel.ARMS
    ) == OFFICIAL - len(fanout_panel.CONTROLS)


def test_dropping_any_cut_leaves_only_that_team_unmeasured() -> None:
    release, cuts = _official()
    full = mailbox_panel.summarize(release, cuts)
    for index, dropped in enumerate(cuts):
        report = mailbox_panel.summarize(release, cuts[:index] + cuts[index + 1 :])
        changed = [
            (a, b)
            for a, b in zip(full["teams"], report["teams"], strict=True)
            if a != b
        ]
        assert len(changed) == 1
        before, after = changed[0]
        assert (
            before["events_sha256"]
            == after["events_sha256"]
            == dropped["events_sha256"]
        )
        assert after["coverage"] == "missing"
        assert (
            after["stages"]
            is after["stage_counts"]
            is after["submitted"]
            is after["path"]
            is None
        )
        assert after["native_reward"] == before["native_reward"]
        assert after["submit_ms"] == before["submit_ms"]
        if before["control"]:
            assert report["arms"] == full["arms"]
            continue
        arm, whole = report["arms"][before["arm"]], full["arms"][before["arm"]]
        assert (
            arm["missing_teams"] == 1
            and arm["recorded_teams"] == whole["recorded_teams"] - 1
        )
        assert arm["edges_reached"] == {
            stage: whole["edges_reached"][stage] - before["stage_counts"][stage]
            for stage in STAGES
        }
        assert arm["teams_submitted"] == whole["teams_submitted"] - before["submitted"]


def test_missing_release_row_stays_visible_and_its_cut_is_foreign() -> None:
    release, cuts = _official()
    gone = next(
        row for row in release["rows"] if _key(row) == (1210, "parallel", panel.LIVE)
    )
    release["rows"].remove(gone)
    kept = [cut for cut in cuts if cut["events_sha256"] != gone["inputs"]["events"]]
    report = mailbox_panel.summarize(release, kept)
    team = next(t for t in report["teams"] if _key(t) == _key(gone))
    assert team["coverage"] == "no_release_row"
    assert team["events_sha256"] is team["native_reward"] is team["stages"] is None
    arm = report["arms"]["parallel"]
    assert arm["teams"] == len(fanout_panel.WORLDS)
    assert arm["without_release_row"] == arm["missing_teams"] == 1
    _refused(
        "cut_foreign",
        lambda: mailbox_panel.summarize(release, cuts),
        mailbox_panel.Rejected,
    )


def test_booking_and_path_follow_the_release_submit_time() -> None:
    release, cuts = _official()
    report = mailbox_panel.summarize(release, cuts)
    rows = {_key(row): row for row in _rows(release)}
    unbooked = 0
    for team in report["teams"]:
        submit_ms = rows[_key(team)]["timing"]["submit_ms"]
        booked = submit_ms is not None
        assert team["submitted"] is booked and (team["path"] is not None) is booked
        if booked:
            assert sum(team["path"]["phase_ms"].values()) == submit_ms
            continue
        unbooked += 1
        assert all(stage["seat"] in SEATS for stage in team["stages"])
        if not team["control"]:
            incomplete = {
                "world": team["world"],
                "stages": team["stages"],
                "submitted": False,
            }
            assert incomplete in report["arms"][team["arm"]]["incomplete"]
    assert unbooked >= 1


def _put(*path: str | int, value: object) -> Change:
    def change(cut: Json) -> None:
        target: Any = cut
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


def _upper(cut: Json) -> None:
    cut["events_sha256"] = cut["events_sha256"].upper()


SERIAL_TEAM = (1208, "serial", panel.LIVE)
CONTROL_TEAM = (1208, "parallel", "control-correct")
CUT_REFUSALS = [
    (
        "roles_swapped",
        SERIAL_TEAM,
        [
            _put("booker", value="p2"),
            _put("reporters", value=["p1", "p3", "p4"]),
            _put("edges", 0, "seat", value="p1"),
        ],
        "cut_roles_differ",
    ),
    (
        "foreign_digest",
        SERIAL_TEAM,
        [_put("events_sha256", value="0" * 64)],
        "cut_foreign",
    ),
    ("digest_not_lowercase", SERIAL_TEAM, [_upper], "cut_events_digest"),
    (
        "request_missing_mid_chain",
        SERIAL_TEAM,
        [_put("edges", 1, "request", value=None)],
        "cut_edge_not_prefix",
    ),
    (
        "inbox_before_request",
        SERIAL_TEAM,
        [_put("edges", 0, "request_inbox", value=[5, 1])],
        "cut_edge_impossible",
    ),
    (
        "first_request_after_origin",
        SERIAL_TEAM,
        [_put("edges", 0, "request", value=[6, 1])],
        "cut_first_request_not_zero",
    ),
    (
        "booking_before_last_return",
        SERIAL_TEAM,
        [_put("submit", value=[80, 46342])],
        "cut_submit_before_replies",
    ),
    (
        "horizon_before_booking",
        SERIAL_TEAM,
        [_put("through", value=[80, 46342])],
        "cut_horizon_before_event",
    ),
    (
        "horizon_missing",
        SERIAL_TEAM,
        [_put("through", value=None)],
        "cut_missing_horizon",
    ),
    (
        "serial_requests_overlap",
        SERIAL_TEAM,
        [_put("edges", 1, "request", value=[40, 11500])],
        "cut_serial_overlap",
    ),
    (
        "gather_differs",
        SERIAL_TEAM,
        [_put("edges", 2, "reply", value=[79, 36063])],
        "cut_gather_differs",
    ),
    (
        "booking_time_differs",
        SERIAL_TEAM,
        [_put("submit", value=[81, 46343]), _put("through", value=[81, 46343])],
        "cut_submit_differs",
    ),
    (
        "point_shape",
        SERIAL_TEAM,
        [_put("edges", 0, "request", value=[6])],
        "cut_point_shape",
    ),
    (
        "point_bool",
        SERIAL_TEAM,
        [_put("edges", 0, "request", value=[True, 0])],
        "cut_point_range",
    ),
    (
        "point_before_actions",
        SERIAL_TEAM,
        [_put("edges", 0, "request", value=[2, 0])],
        "cut_point_range",
    ),
    ("extra_field", SERIAL_TEAM, [_put("note", value="x")], "cut_fields"),
    (
        "schema",
        SERIAL_TEAM,
        [_put("schema", value="seatlab.fanin-mailbox-cut/v0")],
        "cut_schema",
    ),
    (
        "snapshot_missed_reply",
        CONTROL_TEAM,
        [_put("edges", 2, "reply_inbox", value=[20, 1700])],
        "cut_snapshot_missed_reply",
    ),
    (
        "event_reused_across_stages",
        CONTROL_TEAM,
        [_put("edges", 1, "request", value=[11, 222])],
        "cut_event_reused",
    ),
    (
        "one_snapshot_two_clocks",
        CONTROL_TEAM,
        [_put("edges", 2, "reply_inbox", value=[18, 1659])],
        "cut_event_reused",
    ),
    (
        "snapshot_clock_backwards",
        CONTROL_TEAM,
        [_put("edges", 0, "reply_inbox", value=[13, 1300])],
        "cut_clock_backwards",
    ),
]


@pytest.mark.parametrize(
    ("team", "changes", "category"),
    [
        pytest.param(team, changes, category, id=name)
        for name, team, changes, category in CUT_REFUSALS
    ],
)
def test_panel_refuses_cuts_that_contradict_order_or_release(
    team: tuple[int, str, str], changes: list[Change], category: str
) -> None:
    release, cuts = _official()
    cut = _cut_of(release, cuts, team)
    accepted = mailbox_panel.summarize(release, [copy.deepcopy(cut)])
    assert (
        next(t for t in accepted["teams"] if _key(t) == team)["coverage"] == "recorded"
    )
    for change in changes:
        change(cut)
    _refused(
        category,
        lambda: mailbox_panel.summarize(release, [cut]),
        mailbox_panel.Rejected,
    )


def test_panel_refuses_duplicate_and_unbounded_cut_lists() -> None:
    release, cuts = _official()
    cut = _cut_of(release, cuts, SERIAL_TEAM)
    _refused(
        "cut_duplicate",
        lambda: mailbox_panel.summarize(release, [cut, copy.deepcopy(cut)]),
        mailbox_panel.Rejected,
    )
    _refused(
        "cuts_bound",
        lambda: mailbox_panel.summarize(release, [cut] * (mailbox_panel.MAX_CUTS + 1)),
        mailbox_panel.Rejected,
    )
    wrong_type: Any = tuple(cuts)
    _refused(
        "cuts_bound",
        lambda: mailbox_panel.summarize(release, wrong_type),
        mailbox_panel.Rejected,
    )
