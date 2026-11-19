"""Read-only cohort analysis of exported Cramped Room SeatLab studies.

Requires the separately installed stdlib-only ``harbor-seatlab`` package
(``seatlab``) for strict JSON decoding, episode validation, and alignment.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from seatlab.alignment import align
from seatlab.episodes import Episode, read_episodes
from seatlab.tape import TapeError, decode

REPORT_SCHEMA = "seatlab.kitchen-analysis/v1"
INDEX_SCHEMA = "seatlab.kitchen-study/v1"
HORIZON = 38
ROLES = ("chef0", "chef1")
CELLS = ("A-TT", "A-WT", "A-TW", "B-TW", "B-WT", "B-TT")
COMPARISONS = (
    ("A-TT", "A-WT"),
    ("A-TT", "A-TW"),
    ("B-TT", "B-WT"),
    ("B-TT", "B-TW"),
    ("A-TT", "B-TT"),
)
ACTIONS = frozenset({"north", "south", "east", "west", "stay", "interact"})


def _uuid(val: object, where: str) -> str:
    try:
        if (
            isinstance(val, str)
            and len(val) == 36
            and UUID(val).int
            and str(UUID(val)) == val
        ):
            return val
    except ValueError:
        pass
    raise ValueError(f"{where}: expected a canonical UUID")


def _mapping(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _rows(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return None
    return [cast(dict[str, object], item) for item in value]


def _sha256(val: object, where: str) -> str:
    if (
        isinstance(val, str)
        and len(val) == 64
        and all(c in "0123456789abcdef" for c in val)
    ):
        return val
    raise ValueError(f"{where}: expected a lowercase SHA-256 digest")


def _load_episodes(path: Path, cell: dict[str, Any], label: str) -> dict[str, Episode]:
    eps = list(read_episodes(path))
    by_role = {ep["role"]: ep for ep in eps}
    if len(eps) != len(ROLES) or set(by_role) != set(ROLES):
        raise ValueError(f"{label}.{cell['id']}: expected chef0 and chef1 episodes")
    for role, ep in by_role.items():
        if (
            ep["id"] != f"kitchen:{cell['native_trial_id']}:{role}"
            or ep["native_trial_id"] != cell["native_trial_id"]
            or ep["case_sha256"] != cell["case_sha256"]
            or ep["episode_sha256"] != cell["episode_sha256"][role]
            or ep["closed"] is not True
            or len(ep["frames"]) != HORIZON
        ):
            raise ValueError(
                f"{label}.{cell['id']}.{role}: episode metadata, closed flag, or length mismatch"
            )
        for tick, frame in enumerate(ep["frames"]):
            view, obs = frame["view"], frame["observed"]
            state = _mapping(view.get("state"))
            tick_value = view.get("tick")
            timestep = state.get("timestep") if state is not None else None
            action = obs["action"] if obs is not None else None
            if (
                frame["basis"] != "bound"
                or obs is None
                or action not in ACTIONS
                or set(view) != {"tick", "state"}
                or type(tick_value) is not int
                or tick_value != tick
                or state is None
                or type(timestep) is not int
                or timestep != tick
            ):
                raise ValueError(
                    f"{label}.{cell['id']}.{role} tick {tick}: invalid bound kitchen frame"
                )
    if any(
        a["view_sha256"] != b["view_sha256"]
        for a, b in zip(
            by_role["chef0"]["frames"], by_role["chef1"]["frames"], strict=True
        )
    ):
        raise ValueError(
            f"{label}.{cell['id']}: chef0 and chef1 views differ within trial"
        )
    return by_role


def _load_cohort(index_path: Path, label: str) -> dict[str, Any]:
    doc = _mapping(decode(index_path.read_bytes()))
    if doc is None or doc.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"{label}: invalid study index schema")
    study_id = _uuid(doc.get("study_id"), f"{label}.study_id")
    capsule_sha = _sha256(doc.get("capsule_sha256"), f"{label}.capsule_sha256")
    raw_cells, raw_comps = _rows(doc.get("cells")), _rows(doc.get("comparisons"))
    if raw_cells is None or [row.get("id") for row in raw_cells] != list(CELLS):
        raise ValueError(f"{label}: expected 6 registered cells in protocol order")
    if raw_comps is None or len(raw_comps) != len(COMPARISONS):
        raise ValueError(f"{label}: expected 5 registered comparisons")

    export_dir, cells, seen_uuids = index_path.parent, {}, {study_id}
    for row in raw_cells:
        cid, status = row["id"], row.get("status")
        tid = (
            None
            if status == "missing" and row.get("native_trial_id") is None
            else _uuid(row.get("native_trial_id"), f"{label}.{cid}")
        )
        if tid is not None:
            if tid in seen_uuids:
                raise ValueError(f"{label}.{cid}: duplicate UUID within cohort")
            seen_uuids.add(tid)
        if status == "missing":
            if not isinstance(row.get("reason"), str) or not row["reason"]:
                raise ValueError(f"{label}.{cid}: missing cell requires a reason")
            cells[cid] = {
                "id": cid,
                "status": "missing",
                "reason": row["reason"],
                "native_trial_id": tid,
            }
        elif status == "measured":
            reward, deliv, hashes = (
                row.get("reward"),
                row.get("deliveries_by_seat"),
                row.get("episode_sha256"),
            )
            if (
                type(reward) not in (int, float)
                or reward not in (0, 1)
                or not isinstance(deliv, dict)
                or set(deliv) != set(ROLES)
                or any(
                    type(v) is not int or not 0 <= v <= HORIZON for v in deliv.values()
                )
                or type(row.get("episodes")) is not int
                or row["episodes"] != len(ROLES)
                or type(row.get("turns_per_role")) is not int
                or row["turns_per_role"] != HORIZON
                or not isinstance(hashes, dict)
                or set(hashes) != set(ROLES)
                or row.get("episode_file") != f"{cid}.jsonl"
            ):
                raise ValueError(f"{label}.{cid}: invalid measured cell metadata")
            reward_num = cast(int | float, reward)
            deliveries = cast(dict[str, Any], deliv)
            episode_hashes = cast(dict[str, Any], hashes)
            rec: dict[str, Any] = {
                "id": cid,
                "status": "measured",
                "native_trial_id": tid,
                "reward": int(reward_num),
                "case_sha256": _sha256(
                    row.get("case_sha256"), f"{label}.{cid}.case_sha256"
                ),
                "deliveries_by_seat": {r: deliveries[r] for r in ROLES},
                "episode_file": f"{cid}.jsonl",
                "episode_sha256": {
                    r: _sha256(episode_hashes[r], f"{label}.{cid}.episode_sha256.{r}")
                    for r in ROLES
                },
            }
            rec["episodes"] = _load_episodes(
                export_dir / rec["episode_file"], rec, label
            )
            cells[cid] = rec
        else:
            raise ValueError(f"{label}.{cid}: invalid cell status")
    measured = sum(c["status"] == "measured" for c in cells.values())
    if (
        type(doc.get("measured_trials")) is not int
        or doc["measured_trials"] != measured
    ):
        raise ValueError(f"{label}: measured_trials does not match cells")

    comparisons = []
    for (src, tgt), entry in zip(COMPARISONS, raw_comps, strict=True):
        cid, sc, tc = f"{src}-vs-{tgt}", cells[src], cells[tgt]
        if (
            not isinstance(entry, dict)
            or entry.get("id") != cid
            or entry.get("source") != src
            or entry.get("target") != tgt
            or entry.get("source_native_trial_id") != sc["native_trial_id"]
            or entry.get("target_native_trial_id") != tc["native_trial_id"]
        ):
            raise ValueError(f"{label}.{cid}: comparison registration mismatch")
        cases, aligned = (
            {c["case_sha256"] for c in (sc, tc) if c["status"] == "measured"},
            None,
        )
        if len(cases) == 1:
            if entry.get("manifest") != f"{cid}.json":
                raise ValueError(f"{label}.{cid}: expected manifest {cid}.json")
            if sc["status"] == "measured" and tc["status"] == "measured":
                aligned = align(
                    export_dir / sc["episode_file"],
                    export_dir / tc["episode_file"],
                    export_dir / f"{cid}.json",
                )
                counts = _mapping(aligned.get("counts"))
                if (
                    counts is None
                    or counts.get("pairs") != len(ROLES)
                    or any(
                        counts.get(k)
                        for k in (
                            "unbound",
                            "missing_observation",
                            "length_changed",
                            "open_prefix",
                        )
                    )
                ):
                    raise ValueError(
                        f"{label}.{cid}: SeatLab alignment found invalid pairs"
                    )
            else:
                doc_m = decode((export_dir / f"{cid}.json").read_bytes())
                if (
                    not isinstance(doc_m, dict)
                    or doc_m.get("schema") != "seatlab.episode-pairs/v1"
                ):
                    raise ValueError(f"{label}.{cid}: invalid pair manifest")
        elif entry.get("manifest") is not None:
            raise ValueError(
                f"{label}.{cid}: unexpected manifest without shared trusted case"
            )
        comparisons.append(
            {
                "id": cid,
                "source": src,
                "target": tgt,
                "reason": entry.get("reason"),
                "aligned": aligned,
            }
        )
    return {
        "study_id": study_id,
        "capsule_sha256": capsule_sha,
        "measured_trials": measured,
        "cells": cells,
        "comparisons": comparisons,
        "uuids": seen_uuids,
    }


def _role_stats(
    src_ep: Episode, tgt_ep: Episode, pair: dict[str, Any], rel: str, script: str
) -> dict[str, Any]:
    first_in = first_act_eq = first_base = None
    eq_in = eq_act = post_ticks = post_eq = base_eq = 0
    for s, t in zip(src_ep["frames"], tgt_ep["frames"], strict=True):
        s_obs, t_obs = s["observed"], t["observed"]
        tick_value = s["view"].get("tick")
        if s_obs is None or t_obs is None or type(tick_value) is not int:
            raise ValueError("measured frame is missing a bound tick or action")
        sa, ta = s_obs["action"], t_obs["action"]
        if not isinstance(sa, str) or not isinstance(ta, str):
            raise ValueError("measured frame action is not a kitchen word")
        tick, turn = tick_value, s["turn"]
        in_eq, act_eq = s["view_sha256"] == t["view_sha256"], sa == ta
        eq_in, eq_act = eq_in + in_eq, eq_act + act_eq
        if not in_eq and first_in is None:
            first_in = {
                "world_tick": tick,
                "episode_turn": turn,
                "source_action": sa,
                "target_action": ta,
            }
        if in_eq and not act_eq and first_act_eq is None:
            first_act_eq = {
                "world_tick": tick,
                "episode_turn": turn,
                "source_action": sa,
                "target_action": ta,
            }
        if first_in is not None:
            post_ticks, post_eq = post_ticks + 1, post_eq + act_eq
        ba = "stay" if script == "wait" else sa
        base_eq += sa == ba
        if sa != ba and first_base is None:
            first_base = {
                "world_tick": tick,
                "episode_turn": turn,
                "source_action": sa,
                "baseline_action": ba,
            }
    raw_bt = pair.get("boundary_turn")
    if raw_bt is None:
        bt = None
    elif type(raw_bt) is int:
        bt = cast(int, raw_bt)
    else:
        raise ValueError("alignment boundary_turn must be an integer or null")
    return {
        "role_relation": rel,
        "first_boundary": {
            "status": pair.get("status"),
            "matched_prefix_turns": pair.get("matched_prefix"),
            "world_tick": None if bt is None else bt - 1,
            "episode_turn": bt,
        },
        "first_unequal_input": first_in,
        "first_unequal_action_at_equal_input": first_act_eq,
        "equal_input_ticks": eq_in,
        "direct_native_equal_actions": eq_act,
        "post_divergence_ticks": post_ticks,
        "post_divergence_equal_actions": post_eq,
        "two_script_baseline": {
            "baseline_policy": script,
            "same_actions_over_38_ticks": base_eq,
            "first_difference": first_base,
        },
    }


def _summarize_cohort(cohort: dict[str, Any]) -> dict[str, Any]:
    cells = cohort["cells"]
    by_arm = {
        arm: {
            "registered": len(g),
            "measured": len(m),
            "missing": len(g) - len(m),
            "reward_1": sum(c["reward"] == 1 for c in m) if m else None,
            "reward_0": sum(c["reward"] == 0 for c in m) if m else None,
            "total_deliveries": sum(sum(c["deliveries_by_seat"].values()) for c in m)
            if m
            else None,
        }
        for arm in ("TT", "WT", "TW")
        for g in ([cells[cid] for cid in CELLS if cid.endswith(f"-{arm}")],)
        for m in ([c for c in g if c["status"] == "measured"],)
    }
    comps_out = []
    for comp in cohort["comparisons"]:
        sc, tc, aligned = cells[comp["source"]], cells[comp["target"]], comp["aligned"]
        row: dict[str, Any] = {
            "id": comp["id"],
            "kind": "within_block_intervention"
            if comp["source"][0] == comp["target"][0]
            else "between_block_repeatability",
            "source": comp["source"],
            "target": comp["target"],
            "source_native_trial_id": sc["native_trial_id"],
            "target_native_trial_id": tc["native_trial_id"],
            "status": "measured" if aligned is not None else "missing",
            "team_outcomes": {
                s: {
                    "status": c["status"],
                    "reward": c.get("reward"),
                    "deliveries_by_seat": c.get("deliveries_by_seat"),
                }
                for s, c in (("source", sc), ("target", tc))
            },
        }
        if aligned is None:
            row["reason"], row["roles"] = comp["reason"] or "missing_cell", None
        else:
            pairs, sp, tp = (
                {p["role"]: p for p in aligned["pairs"]},
                comp["source"][2:],
                comp["target"][2:],
            )
            row["case_sha256"] = sc["case_sha256"]
            row["roles"] = {
                role: _role_stats(
                    sc["episodes"][role],
                    tc["episodes"][role],
                    pairs[role],
                    "control_repeat"
                    if sp == tp
                    else ("changed_actor" if sp[i] != tp[i] else "unchanged_actor"),
                    "wait" if tp[i] == "W" else "tape",
                )
                for i, role in enumerate(ROLES)
            }
        comps_out.append(row)
    missing = [c for c in cells.values() if c["status"] == "missing"]
    return {
        "study_id": cohort["study_id"],
        "capsule_sha256": cohort["capsule_sha256"],
        "registered_trials": len(CELLS),
        "measured_trials": cohort["measured_trials"],
        "missing_trials": len(missing),
        "missing_reasons": dict(sorted(Counter(c["reason"] for c in missing).items())),
        "team_outcomes_by_arm": by_arm,
        "comparisons": comps_out,
        "cells": [
            {
                "id": cid,
                "status": cells[cid]["status"],
                "reason": cells[cid].get("reason"),
                "native_trial_id": cells[cid]["native_trial_id"],
                "reward": cells[cid].get("reward"),
                "deliveries_by_seat": cells[cid].get("deliveries_by_seat"),
                "total_deliveries": None
                if cells[cid]["status"] == "missing"
                else sum(cells[cid]["deliveries_by_seat"].values()),
            }
            for cid in CELLS
        ],
    }


def analyze(original_index: Path, control_index: Path) -> dict[str, Any]:
    original, control = (
        _load_cohort(original_index, "original"),
        _load_cohort(control_index, "control"),
    )
    if original["capsule_sha256"] != control["capsule_sha256"]:
        raise ValueError("original and control capsule_sha256 do not match")
    reused = sorted(original["uuids"] & control["uuids"])
    if reused:
        raise ValueError(f"UUID reused across studies: {reused[0]}")
    for name, cohort in (("original", original), ("control", control)):
        if (
            len(
                {
                    c["case_sha256"]
                    for c in cohort["cells"].values()
                    if c["status"] == "measured"
                }
            )
            > 1
        ):
            raise ValueError(f"{name}: measured cells do not share one case_sha256")
    trial_uuids = (original["uuids"] - {original["study_id"]}) | (
        control["uuids"] - {control["study_id"]}
    )
    return {
        "schema": REPORT_SCHEMA,
        "tick_indexing": {
            "world_tick_range": [0, HORIZON - 1],
            "episode_turn_range": [1, HORIZON],
            "mapping": "world_tick = episode_turn - 1",
        },
        "validation": {
            "same_frozen_capsule_sha256": True,
            "distinct_study_ids": True,
            "distinct_uuids_across_studies": True,
            "total_distinct_trial_uuids": len(trial_uuids),
            "original_denominator_slots": len(CELLS),
            "control_denominator_slots": len(CELLS),
            "all_measured_frames_bound": True,
        },
        "original_cohort": _summarize_cohort(original),
        "control_cohort": _summarize_cohort(control),
        "boundaries": {
            "missing_is_not_zero": "Missing trials stay unmeasured (null reward/deliveries) in their own 6-slot denominator, never zero.",
            "overlapping_teams_not_independent": "Comparisons share A-TT and B-TT controls and pair two seats from one trial; do not treat them as independent.",
            "no_causal_post_divergence_claim": "Post-divergence equal-action counts are descriptive only and do not show causal effects or which view fields a policy read.",
            "two_script_baseline_scope": "Two-script baseline compares source tape actions to wait/tape on source ticks only; it never forecasts partner inputs or team reward.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        text = (
            json.dumps(
                analyze(args.original, args.control),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        )
        if args.out is not None:
            with args.out.open("x", encoding="ascii") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
    except (OSError, ValueError, TapeError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
