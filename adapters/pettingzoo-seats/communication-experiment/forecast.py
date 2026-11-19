"""Preregistered forecast and check for the seeds 101..112 held-out cohort.

python forecast.py predict --out NEW.json
python forecast.py check ROOT --forecast FORM --expect-sha256 64HEX \
    --reference REAL.json --out NEW.json

``predict`` runs the independent official engine and the written policies of the
sibling ``reference.py`` on 12 seeds x 5 teams, and writes a compact strict
forecast: the seed goal, each team's returns and, for each of the 4 contrasts
with ``plain-plain``, the first changed listener action, the first changed
physical step and the number of differing listener actions. The bytes are
sorted and canonical: no UUID, no wall time, no host path.

``check`` refuses a forecast whose SHA-256 differs from ``--expect-sha256``.
Otherwise it compares the forecast with native evidence and reports the
verdict; a disagreement is a result, not an error. The native contrasts and
returns come from the REAL.json ``by_seed[].native`` and ``cells[].native``
(the reference report interface). Each is used only when ROOT holds the same
complete, unique native trial (same UUID). The native target is the first
speaker symbol in the ROOT world log of ``plain-plain``. Missing evidence is
``match: null`` and is never counted as false. Nothing is rerun.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

_NAME = "pz_communication_reference"
if _NAME in sys.modules:
    ref = sys.modules[_NAME]
else:
    _SPEC = importlib.util.spec_from_file_location(
        _NAME, Path(__file__).resolve().parent / "reference.py"
    )
    assert _SPEC and _SPEC.loader
    ref = importlib.util.module_from_spec(_SPEC)
    sys.modules[_NAME] = ref
    _SPEC.loader.exec_module(ref)

SCHEMA = "seatlab.pz-communication-forecast/v1"
CHECK_SCHEMA = "seatlab.pz-communication-forecast-check/v1"
SEEDS = tuple(range(101, 113))
TEAM_NAMES = tuple(team for team, _s, _l in ref.TEAMS)
BASE = ref.BASELINE
OTHERS = tuple(t for t in TEAM_NAMES if t != BASE)
CROSS = ("plain-rotate", "rotate-plain")
CELLS = tuple(
    (f"s{seed}-{team}", seed, team, speaker, listener)
    for seed in SEEDS
    for team, speaker, listener in ref.TEAMS
)
TOL = ref.TOLERANCE
ROLES = ("p0", "p1")
_MISSING = object()


class ForecastError(Exception):
    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.token = token


def dumps(doc: object) -> str:
    raw = json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return raw + "\n"


# ---------------------------------------------------------------- predict


def _ret(value: object) -> float:
    number = ref._number(value)
    if number is None:
        raise ref.RefError("reward_value")
    return number


def predict(factory: Callable[[], Any]) -> dict[str, Any]:
    """Simulate every cell independently; native data is never read."""
    rows = []
    for seed in SEEDS:
        traces = {
            team: ref.simulate(factory, seed, speaker, listener)
            for team, speaker, listener in ref.TEAMS
        }
        base = traces[BASE]
        colour = base["steps"][0]["views"]["p0"]["observation"]
        goal = ref._unique_argmax(ref._vector(colour, ref.SPEAKER_DIM))
        contrasts = {}
        for team in OTHERS:
            got = ref.contrast(base, traces[team], team)
            phys = got["first_changed_listener_physical"]
            contrasts[team] = {
                "first_action": got["first_changed_listener_action"],
                "first_physical_step": None if phys is None else phys["step"],
                "differing_actions": got["listener_actions"]["differing"],
                "compared_steps": got["steps"]["compared"],
            }
        rows.append(
            {
                "seed": seed,
                "goal": goal,
                "returns": {
                    t: {r: _ret(traces[t]["returns"][r]) for r in ROLES}
                    for t in TEAM_NAMES
                },
                "contrasts": contrasts,
            }
        )
    return {
        "schema": SCHEMA,
        "game": ref.GAME,
        "cycles": ref.KWARGS["max_cycles"],
        "tolerance": TOL,
        "baseline": BASE,
        "teams": list(TEAM_NAMES),
        "seeds": rows,
    }


def _keys(obj: object, keys: tuple[str, ...]) -> dict:
    if type(obj) is not dict or set(obj) != set(keys):
        raise ForecastError("bad_keys")
    return obj


def _int(value: object, low: int = 0) -> int:
    if type(value) is not int or value < low:
        raise ForecastError("bad_integer")
    return value


def _shape(doc: object) -> None:
    top = ("schema", "game", "cycles", "tolerance", "baseline", "teams", "seeds")
    _keys(doc, top)
    fixed = {
        "schema": SCHEMA,
        "game": ref.GAME,
        "cycles": ref.KWARGS["max_cycles"],
        "tolerance": TOL,
        "baseline": BASE,
        "teams": list(TEAM_NAMES),
    }
    if any(doc[k] != v or type(doc[k]) is not type(v) for k, v in fixed.items()):
        raise ForecastError("bad_header")
    if type(doc["seeds"]) is not list or len(doc["seeds"]) != len(SEEDS):
        raise ForecastError("bad_seeds")
    for seed, row in zip(SEEDS, doc["seeds"]):
        _keys(row, ("seed", "goal", "returns", "contrasts"))
        if _int(row["seed"]) != seed or _int(row["goal"]) > 2:
            raise ForecastError("bad_seed_row")
        for team, rets in _keys(row["returns"], TEAM_NAMES).items():
            for value in _keys(rets, ROLES).values():
                if ref._number(value) is None:
                    raise ForecastError("bad_return")
        keys = ("first_action", "first_physical_step", "differing_actions")
        keys += ("compared_steps",)
        for con in _keys(row["contrasts"], OTHERS).values():
            _keys(con, keys)
            if con["first_action"] is not None:
                act = _keys(con["first_action"], ("step", "baseline", "team"))
                _int(act["step"], 1), _int(act["baseline"]), _int(act["team"])
            if con["first_physical_step"] is not None:
                _int(con["first_physical_step"], 1)
            if _int(con["differing_actions"]) > _int(con["compared_steps"]):
                raise ForecastError("bad_count")


def parse_forecast(raw: bytes) -> dict[str, Any]:
    """Strict: exact keys and types, and the bytes must be canonical."""
    try:
        doc = json.loads(raw.decode("ascii"))
        _shape(doc)
        if dumps(doc).encode("ascii") != raw:
            raise ForecastError("not_canonical")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ForecastError("not_json") from exc
    return doc


# ---------------------------------------------------------------- native


def _plan(root: Path) -> tuple[str, dict[str, str]]:
    return ref._plan(root, CELLS)


def _first_symbol(doc: dict[str, Any] | None) -> int | None:
    """Speaker symbol accepted in world step 1 of a native log."""
    if doc is None or not doc["steps"] or doc["steps"][0]["step"] != 1:
        return None
    item = doc["steps"][0]["decisions"].get("p0")
    action = item["action"] if item else None
    return action if type(action) is int and 0 <= action <= 2 else None


def load_native(root: Path, names: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Each registered cell from ROOT; duplicate UUIDs are invalid, not used."""
    out: dict[str, dict[str, Any]] = {}
    owners: dict[str, list[str]] = {}
    for cid, seed, _team, _speaker, _listener in CELLS:
        row = ref._native(root, cid, names.get(cid))
        doc = row.pop("_doc")
        if doc is not None and doc.get("case_sha256") != ref.case_sha256(seed):
            row.update(status="invalid", reason="case_mismatch")
            doc = None
        out[cid] = {"native": row, "doc": doc}
        if row["native_trial_id"]:
            owners.setdefault(row["native_trial_id"], []).append(cid)
    for ids in owners.values():
        if len(ids) > 1:
            for cid in ids:
                out[cid]["doc"] = None
                out[cid]["native"].update(status="invalid", reason="duplicate_uuid")
    for entry in out.values():
        entry["first_symbol"] = _first_symbol(entry["doc"])
    return out


# ---------------------------------------------------------------- compare


def _get(obj: object, *path: str) -> Any:
    for key in path:
        if type(obj) is not dict or key not in obj:
            return _MISSING
        obj = obj[key]
    return obj


def _same(left: object, right: object) -> bool:
    """Strict equality: 1, 1.0 and True differ."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _cmp(expected: object, actual: object) -> dict[str, Any]:
    if actual is _MISSING:
        return {"expected": expected, "actual": None, "match": None}
    return {"expected": expected, "actual": actual, "match": _same(expected, actual)}


def _tally(items: list[dict[str, Any]]) -> dict[str, int]:
    flags = [item["match"] for item in items]
    return {
        "denominator": len(flags),
        "true": sum(f is True for f in flags),
        "false": sum(f is False for f in flags),
        "missing": sum(f is None for f in flags),
    }


def _index(reference: object) -> tuple[dict, dict]:
    """Reference report -> cells by id, native contrasts by seed and team."""

    def put(table: dict, key: object, value: object) -> None:
        table[key] = None if key in table else value  # a duplicate is unusable

    cells: dict[str, Any] = {}
    seeds: dict[int, dict[str, Any]] = {}
    for cell in (
        _get(reference, "cells") if type(_get(reference, "cells")) is list else []
    ):
        if type(cell) is dict and isinstance(cell.get("id"), str):
            put(cells, cell["id"], cell)
    for entry in (
        _get(reference, "by_seed") if type(_get(reference, "by_seed")) is list else []
    ):
        if type(entry) is dict and type(entry.get("seed")) is int:
            teams: dict[str, Any] = {}
            for item in (
                entry.get("native") if type(entry.get("native")) is list else []
            ):
                if type(item) is dict and isinstance(item.get("team"), str):
                    put(teams, item["team"], item)
            put(seeds, entry["seed"], teams)
    return cells, {k: v or {} for k, v in seeds.items()}


def _returns(cell: object) -> dict[str, float] | None:
    got = {r: ref._number(_get(cell, "native", "returns", r)) for r in ROLES}
    return None if any(v is None for v in got.values()) else got  # type: ignore[return-value]


def _ret_cmp(expected: dict[str, float], got: dict[str, float] | None) -> dict:
    if got is None:
        return {"expected": expected, "actual": None, "match": None}
    match = all(abs(expected[r] - got[r]) <= TOL for r in ROLES)
    return {"expected": expected, "actual": got, "match": match}


def _contrast_cmp(expected: dict[str, Any], item: object) -> dict[str, Any]:
    act: dict[str, Any] = dict.fromkeys(
        ("first_action", "first_physical_step", "differing_actions"), _MISSING
    )
    if _get(item, "measured") is True:
        first = _get(item, "first_changed_listener_action")
        if first is not _MISSING and first is not None:
            first = {k: _get(first, k) for k in ("step", "baseline", "team")}
            if any(v is _MISSING for v in first.values()):
                first = _MISSING
        phys = _get(item, "first_changed_listener_physical")
        if phys is not _MISSING and phys is not None:
            phys = _get(phys, "step")
        diff = _get(item, "listener_actions", "differing")
        act = {
            "first_action": first,
            "first_physical_step": phys,
            "differing_actions": diff if type(diff) is int else _MISSING,
        }
    return {k: _cmp(expected[k], act[k]) for k in act}


def check(root: Path, raw: bytes, expect: str, reference: object) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != expect:
        raise ForecastError("digest_mismatch")
    plan, names = _plan(root)
    forecast = parse_forecast(raw)
    natives = load_native(root, names)
    rep_cells, rep_seeds = _index(reference)

    def usable(cid: str) -> bool:
        mine = natives[cid]["native"]
        theirs = _get(rep_cells.get(cid), "native")
        return (
            mine["status"] == "complete"
            and mine["native_trial_id"] is not None
            and _get(theirs, "status") == "complete"
            and _get(theirs, "native_trial_id") == mine["native_trial_id"]
            and _get(theirs, "returns") is not _MISSING
            and _same(theirs["returns"], mine["returns"])
        )

    rows, items, ok = [], {}, {}
    for seed, exp in zip(SEEDS, forecast["seeds"]):
        for team in TEAM_NAMES:
            ok[seed, team] = usable(f"s{seed}-{team}")
        symbol = natives[f"s{seed}-{BASE}"]["first_symbol"] if ok[seed, BASE] else None
        returns = {
            t: _ret_cmp(
                exp["returns"][t],
                _returns(rep_cells.get(f"s{seed}-{t}")) if ok[seed, t] else None,
            )
            for t in TEAM_NAMES
        }
        contrasts = {}
        for team in OTHERS:
            item = None
            if ok[seed, BASE] and ok[seed, team]:
                reported = rep_seeds.get(seed, {}).get(team)
                actual = ref.contrast(
                    ref.native_trace(natives[f"s{seed}-{BASE}"]["doc"]),
                    ref.native_trace(natives[f"s{seed}-{team}"]["doc"]),
                    team,
                )
                # A UUID identifies a trial, not the contents of a derived summary.
                # Read the native records and require agreement on every field used below.
                keys = (
                    "first_changed_listener_action",
                    "first_changed_listener_physical",
                    "listener_actions",
                    "listener_physical",
                    "measured",
                )
                if isinstance(reported, dict) and all(
                    k in reported and _same(reported[k], actual[k]) for k in keys
                ):
                    item = actual
            items[seed, team] = item
            contrasts[team] = _contrast_cmp(exp["contrasts"][team], item)
        rows.append(
            {
                "seed": seed,
                "goal": _cmp(exp["goal"], _MISSING if symbol is None else symbol),
                "returns": returns,
                "contrasts": contrasts,
            }
        )

    def flag(seed: int, team: str, *path: str) -> bool | None:
        value = _get(items.get((seed, team)), *path)
        return value if type(value) is bool else None

    def p1(seed: int, team: str) -> float | None:
        if not ok[seed, team]:
            return None
        return (_returns(rep_cells.get(f"s{seed}-{team}")) or {}).get("p1")

    summary = {
        "goal": _tally([r["goal"] for r in rows]),
        "returns": {t: _tally([r["returns"][t] for r in rows]) for t in TEAM_NAMES},
        "contrasts": {
            t: {
                k: _tally([r["contrasts"][t][k] for r in rows])
                for k in rows[0]["contrasts"][t]
            }
            for t in OTHERS
        },
    }
    tallies = [summary["goal"], *summary["returns"].values()]
    tallies += [v for per in summary["contrasts"].values() for v in per.values()]
    verdict = "agreed"
    if any(t["missing"] for t in tallies):
        verdict = "incomplete"
    if any(t["false"] for t in tallies):
        verdict = "disagreed"

    groups = {
        k: {"seeds": 0, "measured": 0, "equal": 0}
        for k in ("target_zero", "target_other", "target_unknown")
    }
    blind = []
    for seed in SEEDS:
        sym = natives[f"s{seed}-{BASE}"]["first_symbol"] if ok[seed, BASE] else None
        key = (
            "target_unknown"
            if sym is None
            else "target_zero"
            if sym == 0
            else "target_other"
        )
        equal = flag(seed, "plain-blind", "listener_actions", "matched")
        group = groups[key]
        group["seeds"] += 1
        if equal is not None:
            group["measured"] += 1
            group["equal"] += equal
        blind.append({"seed": seed, "target": sym, "blind_equal": equal})
    for group in groups.values():
        group["missing"] = group["seeds"] - group["measured"]

    crossed: dict[str, Any] = {}
    for team in CROSS:
        count = {"better": 0, "tie": 0, "worse": 0, "missing": 0}
        for seed in SEEDS:
            base, other = p1(seed, BASE), p1(seed, team)
            if base is None or other is None:
                count["missing"] += 1
            else:
                gap = other - base
                count["better" if gap > TOL else "worse" if gap < -TOL else "tie"] += 1
        crossed[team] = {"denominator": len(SEEDS), **count}
    crossed["total"] = {
        k: sum(crossed[t][k] for t in CROSS)
        for k in ("denominator", "better", "tie", "worse", "missing")
    }

    rotation = {"actions_preserved": [], "physical_preserved": [], "symbol_changed": []}
    for seed in SEEDS:
        rotation["actions_preserved"].append(
            {"match": flag(seed, "rotate-rotate", "listener_actions", "matched")}
        )
        rotation["physical_preserved"].append(
            {"match": flag(seed, "rotate-rotate", "listener_physical", "matched")}
        )
        left = natives[f"s{seed}-{BASE}"]["first_symbol"]
        right = natives[f"s{seed}-rotate-rotate"]["first_symbol"]
        both = ok[seed, BASE] and ok[seed, "rotate-rotate"]
        rotation["symbol_changed"].append(
            {"match": left != right if both and None not in (left, right) else None}
        )
    rotation = {k: _tally(v) for k, v in rotation.items()}

    cells = []
    for cid, seed, team, _speaker, _listener in CELLS:
        mine = natives[cid]["native"]
        cells.append(
            {
                "id": cid,
                "seed": seed,
                "team": team,
                "native_trial_id": mine["native_trial_id"],
                "status": mine["status"],
                "reason": mine["reason"],
                "usable": ok[seed, team],
                "first_symbol": natives[cid]["first_symbol"],
            }
        )
    return {
        "schema": CHECK_SCHEMA,
        "game": ref.GAME,
        "forecast_sha256": expect,
        "plan": plan,
        "registered": {
            "cells": len(CELLS),
            "seeds": list(SEEDS),
            "teams": list(TEAM_NAMES),
            "comparisons": len(SEEDS) * len(OTHERS),
            "tolerance": TOL,
        },
        "verdict": verdict,
        "summary": summary,
        "primary_blind": {"groups": groups, "seeds": blind},
        "crossed": crossed,
        "shared_rotation": rotation,
        "cells": cells,
        "seeds": rows,
    }


# ---------------------------------------------------------------- output


def write_new(out: Path, data: str) -> None:
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(data)
    except Exception:
        out.unlink(missing_ok=True)
        raise


def _sha(text: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise argparse.ArgumentTypeError("need 64 lowercase hex digits")
    return text


def main(
    argv: list[str] | None = None, factory: Callable[[], Any] | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    pre = sub.add_parser("predict")
    pre.add_argument("--out", required=True, type=Path, metavar="NEW.json")
    chk = sub.add_parser("check")
    chk.add_argument("root", type=Path, metavar="ROOT")
    chk.add_argument("--forecast", required=True, type=Path, metavar="FORM")
    chk.add_argument("--expect-sha256", required=True, type=_sha, metavar="64HEX")
    chk.add_argument("--reference", required=True, type=Path, metavar="REAL.json")
    chk.add_argument("--out", required=True, type=Path, metavar="NEW.json")
    args = parser.parse_args(argv)
    if os.path.lexists(args.out) or not args.out.parent.is_dir():
        print("--out exists or its parent is missing", file=sys.stderr)
        return 2
    if args.mode == "predict":
        if factory is None:
            pins = {name: ref._version(name) for name in ref.PINS}
            if pins != ref.PINS:
                print("pinned mpe2/pettingzoo versions not installed", file=sys.stderr)
                return 2
            try:
                factory = ref.official_factory()
            except Exception:  # noqa: BLE001
                print("engine unavailable", file=sys.stderr)
                return 1
        try:
            data = dumps(predict(factory))
        except ref.RefError as exc:
            print(f"forecast failed: {exc.token}", file=sys.stderr)
            return 1
        write_new(args.out, data)
        print(hashlib.sha256(data.encode("ascii")).hexdigest())
        return 0
    try:
        raw = args.forecast.read_bytes()
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        report = check(args.root, raw, args.expect_sha256, reference)
    except ForecastError as exc:
        print(f"refused: {exc.token}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("refused: unreadable forecast or reference", file=sys.stderr)
        return 2
    data = dumps(report)
    needles = {str(args.root.resolve()), str(args.root)}
    if not ref._public(report) or any(n in data for n in needles if os.sep in n):
        print("refused: report would publish a host path", file=sys.stderr)
        return 2
    write_new(args.out, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
