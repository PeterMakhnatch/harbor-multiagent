from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from examples.seatlab.market_handoff import decisions, native_receipt, native_stage

SCHEMA = "seatlab.market-native-panel/v1"
SCORE_SCHEMA = "seatlab.market-native-panel-score/v1"
ARMS, ROLES = native_stage.ARMS, native_stage.ROLES
CELLS = tuple((world, arm) for world in range(decisions.WORLDS) for arm in ARMS)
NAMES = {cell: native_stage.trial_name(*cell, "replay") for cell in CELLS}
PUBLISHED = (NAMES[(0, "own")], NAMES[(1, "donor")])
PINS = {
    "native.json": "2cc8d55fb7411d1aff174c7c869fe1de0ad72bfc2ca53a2cdd19f89f8acc1b10",
    "result.json": "3b81b9bd4662f18b198da4e527ec40534e22b4763b2a72ba3b31a5ba91d5b333",
    "sales-first.jsonl": "67256254b2c480d6c5d48f41863dbf8792ea94abf17f78042858745333af2219",
    "finance-first.jsonl": "e4fafc537dea3c295f1f830d32660e0a91f6c64bda30d4e1ccc25c05d78da1ce",
    "inputs.json": "dc21c50eb7dd000718b9c90f5ca7cdeae1fa8279170e3b41d61d0a3185907f45",
}
REGISTRATION = {
    "document": "examples/seatlab/market-handoff.md",
    "commit": "837deba731da3cb5a1560308c0296212a584a534",
    "mode": "replay",
    "cells": len(CELLS),
    "published": list(PUBLISHED),
    "campaign": len(CELLS) - len(PUBLISHED),
    "pins": PINS,
}
CELL_KEYS = {"trial_name", "world", "arm", "origin", "status", "detail", "receipt"}
RECEIPT_KEYS = {
    "schema",
    "trial_id",
    "trial_name",
    "kind",
    "world",
    "arm",
    "task_name",
    "prefix_fingerprint",
    "sha256",
    "choices",
    "seats",
    "grade",
    "multiple",
}
DELTA_TOLERANCE = decisions.WORLDS * sys.float_info.epsilon
Record = dict[str, Any]


def pinned(data: Path) -> Path:
    root = data.expanduser().resolve(strict=True)
    for name, expected in PINS.items():
        if native_stage.digest(native_receipt.read_bytes(root / name)) != expected:
            decisions.fail(f"{name}: differs from the registered pin")
    return root


def published(root: Path) -> dict[str, Record]:
    _, sample = native_receipt.load(root / "native.json")
    rows = [
        native_receipt.record(row, "native.json trial")
        for row in sample.get("trials", [])
    ]
    found = {
        row["trial_name"]: row
        for row in rows
        if row.get("trial_name") in PUBLISHED and row.get("kind") == "replay"
    }
    if sorted(found) != sorted(PUBLISHED) or len(rows) != len(
        {r.get("trial_id") for r in rows}
    ):
        decisions.fail("native.json: expected exactly the 2 published replay receipts")
    return found


def stage_conflicts(root: Path, data: Path, world: int, arm: str) -> list[str]:
    name = NAMES[(world, arm)]
    picks = native_stage.replay_picks(
        data, world, arm, data / "sales-first.jsonl", data / "finance-first.jsonl"
    )
    _, config = native_receipt.load(root / "config.json")
    task = root / "task"
    files = {
        str(path.relative_to(task)) for path in task.rglob("*") if not path.is_dir()
    }
    expected = {
        name: native_stage.template_bytes(name) for name in native_stage.TASK_FILES
    }
    expected[native_stage.SCENARIO] = native_stage.canonical(
        native_stage.scenario(decisions.load_cells(data), world, arm)
    )
    expected[native_stage.PAYOFFS] = native_receipt.read_bytes(
        data / f"target-{world:02d}.payoffs.json"
    )
    checks = {
        "config": config == native_stage.trial_config(root, name, "replay", picks),
        "task_files": not task.is_symlink()
        and files == expected.keys()
        and not any(path.is_symlink() for path in task.rglob("*")),
    }
    checks["task_bytes"] = checks["task_files"] and all(
        (task / file).read_bytes() == data_bytes
        for file, data_bytes in expected.items()
    )
    return [check for check, ok in checks.items() if not ok]


def campaign_cell(campaign: Path, data: Path, world: int, arm: str) -> Record:
    name = NAMES[(world, arm)]
    path = campaign / name
    cell: Record = {
        "trial_name": name,
        "world": world,
        "arm": arm,
        "origin": "campaign",
        "status": "missing",
        "detail": "not_staged",
        "receipt": None,
    }
    if not path.exists() and not path.is_symlink():
        return cell
    if path.is_symlink() or not path.is_dir():
        decisions.fail(f"{name}: stage root is not a real directory")
    root = path.resolve(strict=True)
    bad = stage_conflicts(root, data, world, arm)
    if bad:
        decisions.fail(f"{name}: conflicting stage {', '.join(bad)}")
    result = root / "trials" / name / "result.json"
    if not result.is_file():
        cell["detail"] = "no_result" if (root / "trials").exists() else "not_run"
        return cell
    try:
        row = native_receipt.receipt(root)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        detail = (
            f"{exc.strerror or type(exc).__name__}: {Path(exc.filename).name}"
            if isinstance(exc, OSError) and exc.filename
            else str(exc)
        )
        return cell | {"status": "failed", "detail": detail}
    if (row["kind"], row["trial_name"], row["world"], row["arm"]) != (
        "replay",
        name,
        world,
        arm,
    ):
        decisions.fail(f"{name}: receipt is not this registered replay cell")
    return cell | {"status": "complete", "detail": None, "receipt": row}


def collect(data: Path, campaign: Path) -> Record:
    root, campaign = pinned(data), campaign.expanduser().resolve(strict=True)
    staged = set(NAMES.values()) - set(PUBLISHED)
    stray = sorted(
        entry.name
        for entry in campaign.iterdir()
        if native_receipt.TRIAL.fullmatch(entry.name) and entry.name not in staged
    )
    if stray:
        decisions.fail(f"campaign holds unregistered or published trial roots: {stray}")
    reused = published(root)
    cells = [
        {
            "trial_name": NAMES[cell],
            "world": cell[0],
            "arm": cell[1],
            "origin": "published",
            "status": "complete",
            "detail": None,
            "receipt": reused[NAMES[cell]],
        }
        if NAMES[cell] in reused
        else campaign_cell(campaign, root, *cell)
        for cell in CELLS
    ]
    done = [cell["receipt"] for cell in cells if cell["receipt"] is not None]
    for field in ("trial_id", "result"):
        values = [
            row["trial_id"] if field == "trial_id" else row["sha256"]["result"]
            for row in done
        ]
        if len(set(values)) != len(values):
            decisions.fail(f"conflicting stages share a native {field}")
    return {"schema": SCHEMA, "registration": REGISTRATION, "cells": cells}


def request_digest(stage: str, roles: tuple[str, str], plan: decisions.Plan) -> str:
    prefix = f"{decisions.NAMESPACE}/order/{stage}"
    ranked = sorted(
        range(decisions.WORLDS), key=lambda world: decisions.sha(f"{prefix}/{world}")
    )
    lines = [
        decisions.canonical(plan[(world, role)]) + "\n"
        for world in range(decisions.WORLDS)
        for role in (roles[::-1] if world in ranked[decisions.WORLDS // 2 :] else roles)
        if (world, role) in plan
    ]
    return decisions.sha("".join(lines))


def frozen_inputs(
    root: Path, plans: dict[str, tuple[tuple[str, str], decisions.Plan]]
) -> None:
    _, inputs = native_receipt.load(root / "inputs.json")
    frozen = {
        name: native_stage.digest(native_receipt.read_bytes(root / name))
        for name in inputs["prefix_file_sha256"]
    }
    stages = {
        stage: {
            "first_replies_sha256": PINS[f"{stage}-first.jsonl"],
            "requests": len(plan),
            "requests_sha256": request_digest(stage, roles, plan),
        }
        for stage, (roles, plan) in plans.items()
    }
    if (
        frozen != inputs["prefix_file_sha256"]
        or inputs["source"] != decisions.PINNED_SOURCE
        or len(frozen) != len(decisions.ARMS) * decisions.WORLDS
        or not same(inputs["stages"], stages)
    ):
        decisions.fail(
            "inputs.json: baseline freeze differs from the pinned direct inputs"
        )


def originals(root: Path) -> tuple[Record, dict[tuple[int, str], Record]]:
    sales, finance = root / "sales-first.jsonl", root / "finance-first.jsonl"
    archived = native_receipt.record(
        decisions.decode(decisions.read_text(root / "result.json")), "result.json"
    )
    if decisions.canonical(
        decisions.score(root, sales, finance)
    ) != decisions.canonical(archived):
        decisions.fail(
            "result.json: archived grades differ from the pinned direct replies"
        )
    cells, advice, _ = decisions.advise(root, sales)
    sales_plan, finance_plan = (
        decisions.sales_plan(cells),
        decisions.finance_plan(cells, advice),
    )
    frozen_inputs(
        root,
        {
            "sales": (decisions.ARMS, sales_plan),
            "finance": (decisions.GROUPS, finance_plan),
        },
    )
    decision, _ = decisions.outcomes(
        finance_plan, decisions.read_replies(finance, finance_plan, sales_plan)
    )
    task_name = native_receipt.task_name(native_stage.TEMPLATE)
    expected: dict[tuple[int, str], Record] = {}
    for world, arm in CELLS:
        source, group = native_stage.SALES_SOURCE[arm], archived["groups"][world]
        sales_pick, finance_pick = advice[(world, source)], decision[(world, arm)]
        if sales_pick is None or finance_pick is None:
            decisions.fail(
                f"{NAMES[(world, arm)]}: original direct decision is missing"
            )
        picks = {"sales": sales_pick, "finance": finance_pick}
        scene = native_stage.scenario(cells, world, arm)
        payoff = native_receipt.read_bytes(root / f"target-{world:02d}.payoffs.json")
        option = decisions.payoffs(root, world, cells[("target", world)])[
            finance_pick - 1
        ]
        prompts = {
            "sales": sales_plan[(world, source)],
            "finance": finance_plan[(world, arm)],
        }
        seats = {}
        for role in ROLES:
            view = {
                "role": role,
                "completed_days": scene["completed_days"][role],
                "observation": scene[role],
            } | ({"advice": picks["sales"]} if role == "finance" else {})
            seats[role] = {
                "source": "replay",
                "served_model": None,
                "pick": picks[role],
                "view_sha256": native_stage.digest(native_stage.canonical(view)),
                "prompt_sha256": prompts[role]["prompt_sha256"],
            }
        expected[(world, arm)] = {
            "schema": native_receipt.SCHEMA,
            "trial_name": NAMES[(world, arm)],
            "kind": "replay",
            "world": world,
            "arm": arm,
            "task_name": task_name,
            "prefix_fingerprint": group["prefix_fingerprint"],
            "choices": picks,
            "seats": seats,
            "grade": group["world_score"][arm],
            "multiple": group["multiple"][arm],
            "payoffs": native_stage.digest(payoff),
            "option": option,
            "archived_pick": group["decision"][arm],
            "target": cells[("target", world)],
        }
    return archived, expected


def same(left: object, right: object) -> bool:
    return decisions.canonical(left) == decisions.canonical(right)


def uuid_ok(value: object) -> bool:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        return False
    return parsed is not None and str(parsed) == value and parsed.int != 0


def row_mismatches(value: object, want: Record) -> list[str]:
    if not isinstance(value, dict) or value.keys() != RECEIPT_KEYS:
        return ["receipt_keys"]
    row = native_receipt.record(value, "bundle receipt")
    bad = [
        key
        for key in RECEIPT_KEYS - {"trial_id", "sha256", "seats"}
        if not same(row[key], want[key])
    ]
    bad += [
        f"seats.{role}"
        for role in ROLES
        if not isinstance(row["seats"], dict)
        or not same(row["seats"].get(role), want["seats"][role])
    ]
    hashes = row["sha256"]
    if not (
        isinstance(hashes, dict)
        and hashes.keys() == {"config", "result", "evidence", "payoffs"}
        and all(native_receipt.hex64(value) for value in hashes.values())
        and hashes["payoffs"] == want["payoffs"]
    ):
        bad.append("sha256")
    checks = {
        "trial_id": uuid_ok(row["trial_id"]),
        "archived_pick": want["choices"]["finance"] == want["archived_pick"],
        "target_prefix": want["target"]["prefix_fingerprint"]
        == want["prefix_fingerprint"],
        "stock_grade": same(want["option"]["world_score"], want["grade"])
        and same(want["option"]["multiple"], want["multiple"]),
        "reward_valid": native_receipt.number_ok(row["grade"])
        and 0 <= row["grade"] <= 1,
    }
    return bad + [check for check, ok in checks.items() if not ok]


def bundle_cells(root: Path, value: object) -> list[Record]:
    bundle = native_receipt.record(value, "bundle")
    if bundle.keys() != {"schema", "registration", "cells"} or (
        bundle["schema"] != SCHEMA or not same(bundle["registration"], REGISTRATION)
    ):
        decisions.fail("bundle: unexpected schema or registration")
    listed, reused = bundle["cells"], published(root)
    if not isinstance(listed, list) or len(listed) != len(CELLS):
        decisions.fail("bundle: expected exactly the 24 registered cells")
    cells: list[Record] = []
    for item, (world, arm) in zip(listed, CELLS, strict=True):
        name = NAMES[(world, arm)]
        cell = native_receipt.record(item, f"bundle cell {name}")
        origin = "published" if name in PUBLISHED else "campaign"
        if not (
            cell.keys() == CELL_KEYS
            and same(
                [cell["trial_name"], cell["world"], cell["arm"], cell["origin"]],
                [name, world, arm, origin],
            )
            and cell["status"] in ("complete", "missing", "failed")
            and (cell["status"] == "complete") == (cell["receipt"] is not None)
            and (cell["status"] == "complete") == isinstance(cell["receipt"], dict)
            and (cell["status"] == "complete") == (cell["detail"] is None)
            and (cell["detail"] is None or isinstance(cell["detail"], str))
        ):
            decisions.fail(f"bundle: malformed cell {name}")
        if origin == "published" and not same(cell["receipt"], reused[name]):
            decisions.fail(f"bundle: {name} differs from the published native receipt")
        cells.append(cell)
    return cells


def paired(rows: dict[tuple[int, str], Record], archived: Record) -> Record:
    worlds, deltas = [], []
    for world in range(decisions.WORLDS):
        own, donor = rows[(world, "own")]["grade"], rows[(world, "donor")]["grade"]
        deltas.append(own - donor)
        worlds.append(
            {
                "world": world,
                "own": own,
                "donor": donor,
                "delta": deltas[-1],
                "outcome": "win" if own > donor else "loss" if own < donor else "tie",
                "archived_delta_exact": same(
                    deltas[-1], archived["groups"][world]["delta"]
                ),
            }
        )
    wins, losses = sum(d > 0 for d in deltas), sum(d < 0 for d in deltas)
    gain = decisions.mean(deltas)
    own_mean = decisions.mean(
        [rows[(w, "own")]["grade"] for w in range(decisions.WORLDS)]
    )
    donor_mean = decisions.mean(
        [rows[(w, "donor")]["grade"] for w in range(decisions.WORLDS)]
    )
    primary, descriptive = archived["primary"], archived["descriptive"]
    assert gain is not None
    checks = {
        "complete": primary["complete"] == decisions.WORLDS,
        "wins_losses_ties": [wins, losses, len(deltas) - wins - losses]
        == [primary["wins"], primary["losses"], primary["ties"]],
        "world_deltas_exact": all(row["archived_delta_exact"] for row in worlds),
        "own_mean_exact": same(own_mean, descriptive["own_mean"]),
        "donor_mean_exact": same(donor_mean, descriptive["donor_mean"]),
        "mean_delta_within_float_tolerance": math.isclose(
            gain, primary["mean_delta"], rel_tol=0.0, abs_tol=DELTA_TOLERANCE
        ),
    }
    return {
        "status": "complete",
        "passed": all(checks.values()),
        "worlds": worlds,
        "primary": {
            "complete": len(deltas),
            "required": decisions.WORLDS,
            "mean_delta": gain,
            "wins": wins,
            "losses": losses,
            "ties": len(deltas) - wins - losses,
        },
        "descriptive": {"own_mean": own_mean, "donor_mean": donor_mean},
        "archived": {
            "primary": {
                key: primary[key] for key in ("mean_delta", "wins", "losses", "ties")
            },
            "descriptive": {
                key: descriptive[key] for key in ("own_mean", "donor_mean")
            },
            "checks": checks,
            "mean_delta_bitwise_equal": same(gain, primary["mean_delta"]),
            "mean_delta_tolerance": {
                "applies_to": "primary.mean_delta",
                "rel_tol": 0.0,
                "abs_tol": DELTA_TOLERANCE,
                "basis": "binary64 means of 12 deltas in [-1,1]; bound 12 * float epsilon",
            },
        },
    }


def score(data: Path, bundle_path: Path) -> Record:
    root = pinned(data)
    raw = native_receipt.read_bytes(bundle_path)
    bundle = decisions.decode(raw.decode("utf-8"))
    if raw != native_stage.canonical(bundle) + b"\n":
        decisions.fail("bundle: expected one canonical JSON document")
    cells = bundle_cells(root, bundle)
    archived, expected = originals(root)
    missing = [
        {"trial_name": c["trial_name"], "detail": c["detail"]}
        for c in cells
        if c["status"] == "missing"
    ]
    failed = [
        {"trial_name": c["trial_name"], "detail": c["detail"]}
        for c in cells
        if c["status"] == "failed"
    ]
    rows = {
        (c["world"], c["arm"]): c["receipt"] for c in cells if c["status"] == "complete"
    }
    bad = {key: row_mismatches(row, expected[key]) for key, row in rows.items()}
    for field in ("trial_id", "result"):
        seen: dict[str, int] = {}
        values = {
            key: decisions.canonical(
                row.get("trial_id")
                if field == "trial_id"
                else (row.get("sha256") or {}).get("result")
            )
            for key, row in rows.items()
            if "receipt_keys" not in bad[key]
            and (field == "trial_id" or isinstance(row["sha256"], dict))
        }
        for value in values.values():
            seen[value] = seen.get(value, 0) + 1
        for key, value in values.items():
            if seen[value] > 1:
                bad[key].append(f"unique_{field}")
    reward_fields = {
        "receipt_keys",
        "grade",
        "multiple",
        "archived_pick",
        "stock_grade",
        "reward_valid",
        "target_prefix",
        "prefix_fingerprint",
    }
    report: Record = {
        "schema": SCORE_SCHEMA,
        "registration": REGISTRATION,
        "bundle_sha256": native_stage.digest(raw),
        "cells": {
            "registered": len(CELLS),
            "published": len(PUBLISHED),
            "campaign": len(CELLS) - len(PUBLISHED),
            "complete": len(rows),
            "missing": len(missing),
            "failed": len(failed),
        },
        "agreement": {
            "seat_bindings": {
                "required": len(CELLS) * len(ROLES),
                "agree": sum(
                    not {"receipt_keys", f"seats.{role}"} & set(fields)
                    for fields in bad.values()
                    for role in ROLES
                ),
            },
            "rewards": {
                "required": len(CELLS),
                "agree": sum(
                    not reward_fields & set(fields) for fields in bad.values()
                ),
            },
        },
        "missing": missing,
        "failed": failed,
        "mismatches": [
            {"trial_name": NAMES[key], "fields": sorted(fields)}
            for key, fields in bad.items()
            if fields
        ],
    }
    if missing or failed or report["mismatches"]:
        return report | {"status": "incomplete" if missing or failed else "mismatch"}
    return report | paired(rows, archived)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.market_handoff.native_panel"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command, second in (("collect", "campaign"), ("score", "bundle")):
        sub = commands.add_parser(command)
        sub.add_argument("data", type=Path)
        sub.add_argument(second, type=Path)
    args = parser.parse_args(argv)
    try:
        report = (
            collect(args.data, args.campaign)
            if args.command == "collect"
            else score(args.data, args.bundle)
        )
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        message = (
            f"{exc.strerror or type(exc).__name__}: {Path(exc.filename).name}"
            if isinstance(exc, OSError) and exc.filename
            else str(exc)
        )
        sys.stderr.write(f"error: {message}\n")
        return 2
    sys.stdout.write(native_stage.canonical(report).decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
