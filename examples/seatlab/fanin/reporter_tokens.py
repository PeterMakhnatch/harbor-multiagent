import sys
from pathlib import Path
from typing import Any, cast

from ..wait_peer.receipt import canonical
from . import certified_panel as cp
from . import panel, reporter_usage
from .panel import Rejected, require

SCHEMA = "seatlab.certified-reporters-tokens/v1"
USAGE = "usage: python3.12 -BS -m examples.seatlab.fanin.reporter_tokens ROWS"
FLAG = "--owner-counters"
SUMS = ("issued_all", "issued_stop", "issued_shared", "all_tokens", "stop_tokens")
SUMS += ("absent_in_stop_all_tokens", "absent_in_all_stop_tokens", "matched_all")
SUMS += ("matched_stop", "shared_request_sha256_equal", "shared_reply_sha256_equal")
DIGEST_BASIS = (
    "count of shared seats with equal published digest across arms; each digest is "
    "SHA-256 of raw owner record bytes; request bytes must be the canonical prompt "
    "envelope (so equal prompt text); reply bytes are compared as recorded"
)
Row = dict[str, Any]
Issued = dict[str, tuple[int, str, str | None]]


def _issued(team: Row) -> Issued:
    wire, prov = team["external"], team["provenance"]
    require(prov["fully_accounted"] is True, "unaccounted_reporter")
    issued: Issued = {}
    for seat, owned in zip(wire["seats"][1:], prov["seats"], strict=True):
        assert seat["seat"] == owned["seat"]
        if seat["action"] == "report":
            require(owned["status"] == "bound", "issued_without_receipt")
            digests = (seat["request_sha256"], seat["reply_sha256"])
            issued[seat["seat"]] = (owned["client_tokens"], *digests)
    tokens = sum(entry[0] for entry in issued.values())
    require(tokens == prov["attested_tokens"], "attested_tokens")
    return issued


def _layout(team: Row) -> tuple[object, ...]:
    wire = team["external"]
    calendar = wire["source"]["calendar_sha256"]
    require(calendar is not None, "calendar_unverified")
    seats = tuple((s["seat"], s["position"], s["role"]) for s in wire["seats"])
    return calendar, seats


def _deltas(row: Row) -> Row:
    shared = row["matched_stop"] - row["matched_all"]
    total = row["stop_tokens"] - row["all_tokens"]
    assert row["all_tokens"] == row["matched_all"] + row["absent_in_stop_all_tokens"]
    assert row["stop_tokens"] == row["matched_stop"] + row["absent_in_all_stop_tokens"]
    assert total == (
        shared + row["absent_in_all_stop_tokens"] - row["absent_in_stop_all_tokens"]
    )
    return row | {"shared_seat_delta": shared, "total_stop_minus_all": total}


def _world(world: int, teams: dict[tuple[int, str], Row]) -> Row:
    all_team, stop_team = teams[(world, "all")], teams[(world, "stop")]
    require(_layout(all_team) == _layout(stop_team), "roles_unmatched_in_world")
    a, s = _issued(all_team), _issued(stop_team)
    shared = a.keys() & s.keys()
    row = {
        "world": world,
        "issued_all": len(a),
        "issued_stop": len(s),
        "issued_shared": len(shared),
        "all_tokens": sum(entry[0] for entry in a.values()),
        "stop_tokens": sum(entry[0] for entry in s.values()),
        "absent_in_stop_all_tokens": sum(a[k][0] for k in a.keys() - shared),
        "absent_in_all_stop_tokens": sum(s[k][0] for k in s.keys() - shared),
        "matched_all": sum(a[k][0] for k in shared),
        "matched_stop": sum(s[k][0] for k in shared),
        "shared_request_sha256_equal": sum(a[k][1] == s[k][1] for k in shared),
        "shared_reply_sha256_equal": sum(a[k][2] == s[k][2] for k in shared),
    }
    return _deltas(row)


def _leave_one_out(worlds: list[Row], total: int) -> Row:
    remaining = {str(w["world"]): total - w["total_stop_minus_all"] for w in worlds}
    assert len(remaining) == len(cp.WORLDS)
    return {
        "total_stop_minus_all_without_world": remaining,
        "positive": sum(v > 0 for v in remaining.values()),
        "negative": sum(v < 0 for v in remaining.values()),
        "zero": sum(v == 0 for v in remaining.values()),
    }


def decompose(envelope: object, supplement: object) -> Row:
    report = cp.summarize(envelope)
    require(not report["missing_cells"], "unpaired_rows")
    require(report["accounted_cells"] == report["required_cells"], "insufficient_rows")
    require(all(a["tokens_complete"] for a in report["arms"].values()), "unaccounted")
    public_teams = cast(Row, envelope)["teams"]
    teams: dict[tuple[int, str], Row] = {
        (t["external"]["world"], t["external"]["arm"]): t for t in public_teams
    }
    require(len(teams) == len(cp.WORLDS) * len(cp.ARMS), "unpaired_rows")
    worlds = [_world(world, teams) for world in cp.WORLDS]
    totals = _deltas({key: sum(w[key] for w in worlds) for key in SUMS})
    for arm in cp.ARMS:
        assert totals[f"{arm}_tokens"] == report["arms"][arm]["attested_tokens"]
    counters = None
    if supplement is not None:
        counters = reporter_usage.partition(public_teams, supplement)
    return {
        "panel_verdict": report["verdict"],
        "unit": "attested reporter client_tokens; booker turns not attested, excluded",
        "join": "world + physical seat; calendar, position and role equal across arms",
        "worlds": worlds,
        "totals": totals,
        "leave_one_world_out": _leave_one_out(worlds, totals["total_stop_minus_all"]),
        "digest_basis": DIGEST_BASIS,
        "owner_counters": counters,
        "claims": {"causal": False, "price": False, "provider_cost": False},
    }


def _emit(decomposition: Row | None, refusal: str | None) -> int:
    assert (decomposition is None) != (refusal is None)
    row = {"schema": SCHEMA, "decomposition": decomposition, "refusal": refusal}
    sys.stdout.buffer.write(canonical(row) + b"\n")
    return 0 if refusal is None else 2


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not (len(args) == 1 or len(args) == 3 and args[1] == FLAG):
        print(f"{USAGE} [{FLAG} PATH]", file=sys.stderr)
        return 2
    try:
        extra = panel.load(Path(args[2])) if len(args) == 3 else None
        if len(args) == 3:
            require(extra is not None, "supplement_shape")
        return _emit(decompose(panel.load(Path(args[0])), extra), None)
    except Rejected as rejected:
        return _emit(None, str(rejected))
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        return _emit(None, f"unreadable_input {type(error).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
