import hashlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
ROLES = ("m1", "m2", "m3", "m4", "m5")

sys.path.insert(0, str(ADAPTER))

from interactive_client import (  # noqa: E402
    feasible_starts,
    run_actor,
    suffix_choice,
    suffix_mask,
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_base = _load_module(
    "calendar_base", ADAPTER / "template" / "environment" / "calendar_world.py"
)
with patch.dict(sys.modules, {"calendar_base": _base}):
    _world_mod = _load_module("interactive_world", ADAPTER / "interactive_world.py")


def _canonical(document) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(document) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _read_canonical(path: Path):
    payload = path.read_bytes()
    document = json.loads(payload)
    assert payload == _canonical(document), path
    return document


def _write_submission(ports: Path, role: str, name: str, document) -> Path:
    path = ports / role / "out" / name
    with path.open("xb") as stream:
        stream.write(_canonical(document))
    return path


def _deliver(path: Path, document) -> None:
    staging = path.with_suffix(".pending")
    staging.write_bytes(_canonical(document))
    staging.rename(path)


def _case_from_free(
    free: dict[str, list[int]],
    *,
    horizon: int,
    slots_per_day: int | None = None,
    duration: int = 1,
    case_id: str = "hybrid/1",
):
    return {
        "case_id": case_id,
        "horizon": horizon,
        "duration": duration,
        "slots_per_day": horizon if slots_per_day is None else slots_per_day,
        "members": {
            role: sorted(set(range(horizon)).difference(free[role])) for role in ROLES
        },
    }


def _run_threaded_exchange(case, mode: str, root: Path, *, timeout: float = 5.0):
    ports = root / "ports"
    ports.mkdir()
    artifact = root / "logs" / "artifacts" / "world.calendar.json"
    world = _world_mod.InteractiveWorld(case, mode, ports, artifact)
    with ThreadPoolExecutor(max_workers=6) as executor:
        broker = executor.submit(world.run, timeout)
        actors = {
            role: executor.submit(run_actor, role, ports / role, timeout=timeout)
            for role in ROLES
        }
        broker.result()
        for role, future in actors.items():
            assert future.result() is None, role
    return ports, _read_canonical(artifact)


def _staggered_case(*, tail: dict[str, list[int]], horizon: int, case_id: str):
    """Build a case whose scalar cursor advances 0->1->2->3->4->5->6->7->8.

    Rounds 1..8 transition sequence (with min(tail["m1"]) == 8):
      r1 b=0 -> (0,1,0,0,0) max=1
      r2 b=1 -> (2,1,1,1,1) max=2
      r3 b=2 -> (2,3,2,2,2) max=3
      r4 b=3 -> (4,3,3,3,3) max=4
      r5 b=4 -> (4,5,4,4,4) max=5
      r6 b=5 -> (6,5,5,5,5) max=6
      r7 b=6 -> (6,7,6,6,6) max=7
      r8 b=7 -> (8,7,7,7,7) max=8 (nonterminal -> round 9 suffix at bound=8)
    """
    assert tail["m1"] and min(tail["m1"]) == 8
    assert all(min(ticks) >= 8 for ticks in tail.values() if ticks)
    prefix = {
        "m1": [0, 2, 4, 6],
        "m2": [1, 3, 5, 7],
        "m3": [0, 1, 2, 3, 4, 5, 6, 7],
        "m4": [0, 1, 2, 3, 4, 5, 6, 7],
        "m5": [0, 1, 2, 3, 4, 5, 6, 7],
    }
    free = {role: sorted(prefix[role] + tail[role]) for role in ROLES}
    return _case_from_free(free, horizon=horizon, case_id=case_id)


def _advance_scalar_rounds(world, ports: Path, views: dict[str, dict], rounds: int):
    previous = None
    for number in range(1, rounds + 1):
        bound = number - 1
        receipt = views["m1"] if previous is None else previous
        _write_submission(
            ports,
            "m1",
            f"query-{number:03d}.json",
            {"round": number, "bound": bound, "view_sha256": _digest(receipt)},
        )
        assert not world.advance()
        query = {"round": number, "bound": bound}
        advancer = "m2" if number % 2 == 1 else "m1"
        replies = {}
        for role in ROLES:
            tick = bound + 1 if role == advancer else bound
            replies[role] = tick
            _write_submission(
                ports,
                role,
                f"reply-{number:03d}.json",
                {
                    "role": role,
                    "round": number,
                    "next": tick,
                    "initial_sha256": _digest(views[role]),
                    "query_sha256": _digest(query),
                },
            )
        assert not world.advance()
        previous = {**query, "replies": replies}
    return previous


def test_suffix_helpers_enforce_fixed_width_lowercase_hex_and_zero_padding():
    # horizon=13, bound=8 -> 5 bits -> 2 hex digits, top 3 bits must be zero.
    assert suffix_mask([], 8, 13) == "00"
    assert suffix_mask([1, 7, 8, 12], 8, 13) == "11"
    # horizon=18, bound=8 -> 10 bits -> 3 hex digits with leading zeros preserved.
    assert suffix_mask([8, 9], 8, 18) == "003"
    assert suffix_mask([17], 8, 18) == "200"

    replies = {
        "m1": "21a",
        "m2": "20a",
        "m3": "20e",
        "m4": "308",
        "m5": "208",
    }
    # Common bits in 0x21a & 0x20a & 0x20e & 0x308 & 0x208 = 0x208 -> bit 3 -> tick 11.
    assert suffix_choice(replies, 8, 18) == 11
    # Remove bit 3 from m3 -> only bit 9 remains -> tick 17.
    assert suffix_choice({**replies, "m3": "204"}, 8, 18) == 17
    # No common bit -> None.
    assert suffix_choice({**replies, "m3": "004"}, 8, 18) is None

    # Invalid starts inputs.
    for bad_starts in ([8, 8], [10, 9], [18], [True], "89"):
        with pytest.raises(ValueError):
            suffix_mask(bad_starts, 8, 18)  # type: ignore[arg-type]

    # Invalid masks: uppercase, short/long width, prefix, non-zero padding bit, non-string.
    for bad_mask in ("00A", "0a", "000a", "0x0a", "400", "fff", 0, None):
        with pytest.raises(ValueError, match="m4:"):
            suffix_choice({**replies, "m4": bad_mask}, 8, 18)

    # Consistent round-8 disclosures: m1<bound (unconstrained), m2/m3 lowest bit is 1 (tick 9),
    # m4/m5 lowest bit is 3 (tick 11).
    previous = {"m1": 7, "m2": 9, "m3": 9, "m4": 11, "m5": 11}
    assert suffix_choice(replies, 8, 18, previous=previous) == 11
    # Corrupting m4 to set a lower start (tick 8) or omit known start 11 fails specifically on m4.
    with pytest.raises(ValueError, match="m4: suffix mask contradicts known start 11"):
        suffix_choice({**replies, "m4": "201"}, 8, 18, previous=previous)
    with pytest.raises(ValueError, match="m4: suffix mask contradicts known start 11"):
        suffix_choice({**replies, "m4": "200"}, 8, 18, previous=previous)
    # Prior exhaustion must have an all-zero suffix mask.
    with pytest.raises(ValueError, match="m4: suffix mask contradicts exhaustion"):
        suffix_choice(replies, 8, 18, previous={**previous, "m4": None})


def test_hybrid8_threaded_early_unanimous_stops_without_suffix(tmp_path):
    case = _case_from_free(
        {
            "m1": [1, 5, 6],
            "m2": [2, 5],
            "m3": [4, 5, 7],
            "m4": [0, 4, 5],
            "m5": [4, 5],
        },
        horizon=12,
        case_id="hybrid/early-hit",
    )
    ports, artifact = _run_threaded_exchange(case, "hybrid8", tmp_path)

    assert artifact["schema"] == "seatlab.calendar-interactive-world/v1"
    assert artifact["mode"] == "hybrid8"
    assert artifact["choice"] == 5
    assert artifact["acknowledged"] is True
    assert len(artifact["rounds"]) == 3
    for row in artifact["rounds"]:
        assert "encoding" not in row
        assert set(row.keys()) == {
            "round",
            "bound",
            "replies",
            "query_sha256",
            "bundle_sha256",
        }
    for role in ROLES:
        assert _read_canonical(ports / role / "input.json")["mode"] == "hybrid8"
        assert _read_canonical(ports / role / "done.json") == {"done": True}
        assert not (ports / role / "query-004.json").exists()


def test_hybrid8_threaded_early_exhaustion_stops_with_null_without_suffix(tmp_path):
    # Transition sequence:
    #   r1 b=0 -> (1, 1, 0, 0, 2) max=2
    #   r2 b=2 -> (3, None, 3, 2, 2) exhausted -> choice=None
    case = _case_from_free(
        {
            "m1": [1, 3],
            "m2": [1],
            "m3": [0, 3, 5],
            "m4": [0, 1, 2, 3, 4],
            "m5": [2, 4],
        },
        horizon=10,
        case_id="hybrid/early-null",
    )
    ports, artifact = _run_threaded_exchange(case, "hybrid8", tmp_path)

    assert artifact["mode"] == "hybrid8"
    assert artifact["choice"] is None
    assert len(artifact["rounds"]) == 2
    assert [row["bound"] for row in artifact["rounds"]] == [0, 2]
    assert artifact["rounds"][1]["replies"] == {
        "m1": 3,
        "m2": None,
        "m3": 3,
        "m4": 2,
        "m5": 2,
    }
    assert all("encoding" not in row for row in artifact["rounds"])
    assert not (ports / "m1" / "query-003.json").exists()


def test_hybrid8_threaded_round_9_suffix_hit_preserves_wire_and_digests(tmp_path):
    # Horizon 21, bound 8 -> 13 suffix bits -> 4 hex digits with leading zeros required.
    case = _staggered_case(
        tail={
            "m1": [8, 12, 19],
            "m2": [9, 12, 14, 19],
            "m3": [8, 10, 19, 20],
            "m4": [11, 13, 19],
            "m5": [8, 19],
        },
        horizon=21,
        case_id="hybrid/late-hit",
    )
    ports, artifact = _run_threaded_exchange(case, "hybrid8", tmp_path)

    assert artifact["schema"] == "seatlab.calendar-interactive-world/v1"
    assert artifact["mode"] == "hybrid8"
    assert artifact["choice"] == 19
    assert artifact["acknowledged"] is True
    assert len(artifact["rounds"]) == 9
    assert [row["bound"] for row in artifact["rounds"]] == list(range(9))

    # Rounds 1..8 have stock scalar wire with no encoding tag.
    for number in range(1, 9):
        row = artifact["rounds"][number - 1]
        assert "encoding" not in row
        query = {"round": number, "bound": row["bound"]}
        bundle = {**query, "replies": row["replies"]}
        assert row["query_sha256"] == _digest(query)
        assert row["bundle_sha256"] == _digest(bundle)
        assert _read_canonical(ports / "m2" / f"query-{number:03d}.json") == query

    # Round 9 has encoding="suffix-hex", bound=8, fixed-width 4-hex masks, and choice=19.
    row9 = artifact["rounds"][8]
    assert row9["round"] == 9
    assert row9["bound"] == 8
    assert row9["encoding"] == "suffix-hex"
    expected_query9 = {"round": 9, "bound": 8, "encoding": "suffix-hex"}
    assert row9["query_sha256"] == _digest(expected_query9)
    expected_masks = {
        "m1": "0811",  # bits 0, 4, 11 (ticks 8, 12, 19)
        "m2": "0852",  # bits 1, 4, 6, 11 (ticks 9, 12, 14, 19)
        "m3": "1805",  # bits 0, 2, 11, 12 (ticks 8, 10, 19, 20)
        "m4": "0828",  # bits 3, 5, 11 (ticks 11, 13, 19)
        "m5": "0801",  # bits 0, 11 (ticks 8, 19)
    }
    assert row9["replies"] == expected_masks
    expected_bundle9 = {**expected_query9, "replies": expected_masks}
    assert row9["bundle_sha256"] == _digest(expected_bundle9)
    assert artifact["choice_view_sha256"] == row9["bundle_sha256"]

    # Check exact submitted files for query-009 and reply-009.
    submitted_q9 = _read_canonical(ports / "m1" / "out" / "query-009.json")
    assert submitted_q9 == {
        "round": 9,
        "bound": 8,
        "encoding": "suffix-hex",
        "view_sha256": artifact["rounds"][7]["bundle_sha256"],
    }
    for role in ROLES:
        assert _read_canonical(ports / role / "query-009.json") == expected_query9
        submitted_r9 = _read_canonical(ports / role / "out" / "reply-009.json")
        assert submitted_r9 == {
            "role": role,
            "round": 9,
            "mask": expected_masks[role],
            "initial_sha256": artifact["initial_view_sha256"][role],
            "query_sha256": row9["query_sha256"],
        }
        assert "next" not in submitted_r9
    assert _read_canonical(ports / "m1" / "round-009.json") == expected_bundle9
    assert not (ports / "m2" / "round-009.json").exists()
    assert not (ports / "m1" / "out" / "query-010.json").exists()


def test_hybrid8_threaded_round_9_suffix_disjoint_returns_null(tmp_path):
    # Every seat has starts >= 8 (so round 8 does not exhaust), but no tick >= 8 is common.
    case = _staggered_case(
        tail={
            "m1": [8, 10, 12],
            "m2": [8, 11, 12],
            "m3": [9, 10, 11],
            "m4": [8, 9, 12],
            "m5": [10, 11, 12],
        },
        horizon=14,
        case_id="hybrid/late-null",
    )
    _, artifact = _run_threaded_exchange(case, "hybrid8", tmp_path)

    assert artifact["mode"] == "hybrid8"
    assert artifact["choice"] is None
    assert len(artifact["rounds"]) == 9
    assert [row["bound"] for row in artifact["rounds"]] == list(range(9))
    assert artifact["rounds"][8]["encoding"] == "suffix-hex"
    assert artifact["rounds"][8]["bound"] == 8
    # 14 - 8 = 6 bits -> 2 lowercase hex digits per seat.
    assert artifact["rounds"][8]["replies"] == {
        "m1": "15",  # bits 0, 2, 4 (ticks 8, 10, 12)
        "m2": "19",  # bits 0, 3, 4 (ticks 8, 11, 12)
        "m3": "0e",  # bits 1, 2, 3 (ticks 9, 10, 11)
        "m4": "13",  # bits 0, 1, 4 (ticks 8, 9, 12)
        "m5": "1c",  # bits 2, 3, 4 (ticks 10, 11, 12)
    }


def test_hybrid8_threaded_exact_round_8_boundary_does_not_open_round_9(tmp_path):
    # Bounds advance 0->1->2->3->4->5->6->7; round 8 at bound 7 is unanimous at 7.
    exact8_hit = _case_from_free(
        {
            "m1": [0, 2, 4, 6, 7],
            "m2": [1, 3, 5, 7],
            "m3": list(range(8)),
            "m4": list(range(8)),
            "m5": list(range(8)),
        },
        horizon=12,
        case_id="hybrid/exact8-hit",
    )
    ports, artifact = _run_threaded_exchange(exact8_hit, "hybrid8", tmp_path)

    assert artifact["choice"] == 7
    assert len(artifact["rounds"]) == 8
    assert all("encoding" not in row for row in artifact["rounds"])
    assert not (ports / "m1" / "query-009.json").exists()


def test_broker_rejects_invalid_hybrid8_queries_masks_and_round_10(tmp_path):
    # Horizon 13; after 8 scalar rounds (bounds 0..7), round 9 at bound 8 has 5 bits -> 2 hex digits.
    case = _case_from_free(
        {role: list(range(13)) for role in ROLES}, horizon=13, case_id="hybrid/grammar"
    )
    ports = tmp_path / "ports"
    ports.mkdir()
    artifact = tmp_path / "logs" / "artifacts" / "world.calendar.json"
    world = _world_mod.InteractiveWorld(case, "hybrid8", ports, artifact)
    views = {role: _read_canonical(ports / role / "input.json") for role in ROLES}

    # Scalar round 1 rejects encoding field.
    bad_scalar = _write_submission(
        ports,
        "m1",
        "query-001.json",
        {
            "round": 1,
            "bound": 0,
            "encoding": "suffix-hex",
            "view_sha256": _digest(views["m1"]),
        },
    )
    with pytest.raises(ValueError, match="query does not acknowledge"):
        world.advance()
    bad_scalar.unlink()

    bundle8 = _advance_scalar_rounds(world, ports, views, 8)

    # Round 9 rejects missing encoding or wrong encoding value.
    for bad_query in (
        {"round": 9, "bound": 8, "view_sha256": _digest(bundle8)},
        {
            "round": 9,
            "bound": 8,
            "encoding": "hex",
            "view_sha256": _digest(bundle8),
        },
    ):
        path = _write_submission(ports, "m1", "query-009.json", bad_query)
        with pytest.raises(ValueError, match="query does not acknowledge"):
            world.advance()
        path.unlink()

    # Valid round 9 suffix query.
    _write_submission(
        ports,
        "m1",
        "query-009.json",
        {
            "round": 9,
            "bound": 8,
            "encoding": "suffix-hex",
            "view_sha256": _digest(bundle8),
        },
    )
    assert not world.advance()
    query9 = {"round": 9, "bound": 8, "encoding": "suffix-hex"}
    assert _read_canonical(ports / "m3" / "query-009.json") == query9

    # Round 9 reply rejects scalar `next` field instead of `mask`.
    bad_next = _write_submission(
        ports,
        "m2",
        "reply-009.json",
        {
            "role": "m2",
            "round": 9,
            "next": 8,
            "initial_sha256": _digest(views["m2"]),
            "query_sha256": _digest(query9),
        },
    )
    with pytest.raises(ValueError, match="reply does not bind"):
        world.advance()
    bad_next.unlink()

    # Round 9 reply rejects uppercase, wrong width, non-hex, non-zero high padding bit (0x20 sets bit 5 >= 5), non-string.
    for bad_mask in ("0A", "a", "00a", "0g", "20", "ff", None, 10):
        path = _write_submission(
            ports,
            "m2",
            "reply-009.json",
            {
                "role": "m2",
                "round": 9,
                "mask": bad_mask,
                "initial_sha256": _digest(views["m2"]),
                "query_sha256": _digest(query9),
            },
        )
        with pytest.raises(ValueError, match="invalid reply mask"):
            world.advance()
        path.unlink()

    # Broker validates grammar only, not private-calendar truth: accept arbitrary valid 5-bit masks.
    valid_masks = {"m1": "1f", "m2": "00", "m3": "0a", "m4": "15", "m5": "01"}
    for role, mask in valid_masks.items():
        _write_submission(
            ports,
            role,
            "reply-009.json",
            {
                "role": role,
                "round": 9,
                "mask": mask,
                "initial_sha256": _digest(views[role]),
                "query_sha256": _digest(query9),
            },
        )
    assert not world.advance()
    bundle9 = _read_canonical(ports / "m1" / "round-009.json")
    assert bundle9 == {**query9, "replies": valid_masks}

    # Round 10 is rejected in hybrid8 even with valid digest and increasing bound.
    q10 = _write_submission(
        ports,
        "m1",
        "query-010.json",
        {"round": 10, "bound": 9, "view_sha256": _digest(bundle9)},
    )
    with pytest.raises(ValueError, match="too many interactive calendar rounds"):
        world.advance()
    q10.unlink()

    # Choice after round 9 completes and writes the hybrid8 artifact.
    _write_submission(
        ports,
        "m1",
        "choice.json",
        {"round": 9, "start": None, "view_sha256": _digest(bundle9)},
    )
    assert world.advance()
    recorded = _read_canonical(artifact)
    assert recorded["mode"] == "hybrid8"
    assert recorded["choice"] is None
    assert len(recorded["rounds"]) == 9


def test_actor_rejects_malformed_suffix_query_and_bundle_in_hybrid8(tmp_path):
    # Seat rejects a round-9 query missing encoding in hybrid8.
    port = tmp_path / "m2"
    (port / "out").mkdir(parents=True)
    view = {
        "role": "m2",
        "mode": "hybrid8",
        "horizon": 16,
        "duration": 1,
        "slots_per_day": 16,
        "busy": [],
    }
    assert feasible_starts(view) == list(range(16))
    _deliver(port / "input.json", view)
    for number in range(1, 9):
        _deliver(port / f"query-{number:03d}.json", {"round": number, "bound": number - 1})
    _deliver(port / "query-009.json", {"round": 9, "bound": 8})
    with pytest.raises(ValueError, match="invalid query: query-009.json"):
        run_actor("m2", port, timeout=2)
    assert not (port / "out" / "reply-009.json").exists()

    # Coordinator rejects a round-9 bundle with tampered coordinator mask.
    # Rounds 1..8 alternate m2 (odd rounds) and m3 (even rounds) advancing bound by +1
    # while m1 (busy=[]) consistently replies bound on every scalar round:
    #   r1 b=0 -> m2=1, others=0 (max=1)
    #   r2 b=1 -> m3=2, others=1 (max=2)
    #   ...
    #   r8 b=7 -> m3=8, others=7 (max=8) -> round 9 at bound=8
    m1_port = tmp_path / "m1"
    (m1_port / "out").mkdir(parents=True)
    m1_view = {**view, "role": "m1"}
    _deliver(m1_port / "input.json", m1_view)
    for number in range(1, 9):
        bound = number - 1
        query = {"round": number, "bound": bound}
        _deliver(m1_port / f"query-{number:03d}.json", query)
        replies = {role: bound for role in ROLES}
        replies["m2" if number % 2 == 1 else "m3"] = bound + 1
        _deliver(m1_port / f"round-{number:03d}.json", {**query, "replies": replies})
    query9 = {"round": 9, "bound": 8, "encoding": "suffix-hex"}
    _deliver(m1_port / "query-009.json", query9)
    # m1's true mask for bound=8, horizon=16, busy=[] is "ff"; tamper it to "fe".
    # m3's mask "ff" has bit 0 set, matching its round-8 disclosure of 8.
    tampered = {role: "ff" for role in ROLES}
    tampered["m1"] = "fe"
    _deliver(m1_port / "round-009.json", {**query9, "replies": tampered})
    with pytest.raises(ValueError, match="changed the coordinator's own reply"):
        run_actor("m1", m1_port, timeout=2)
    assert not (m1_port / "out" / "choice.json").exists()


def test_cursor_mode_remains_unchanged_beyond_round_9(tmp_path):
    # A case requiring 10 scalar rounds works unchanged in cursor mode (no suffix encoding).
    case = _case_from_free(
        {
            "m1": [0, 2, 4, 6, 8, 9],
            "m2": [1, 3, 5, 7, 9],
            "m3": list(range(10)),
            "m4": list(range(10)),
            "m5": list(range(10)),
        },
        horizon=12,
        case_id="cursor/ten-rounds",
    )
    _, artifact = _run_threaded_exchange(case, "cursor", tmp_path)

    assert artifact["mode"] == "cursor"
    assert artifact["choice"] == 9
    assert len(artifact["rounds"]) == 10
    assert all("encoding" not in row for row in artifact["rounds"])
