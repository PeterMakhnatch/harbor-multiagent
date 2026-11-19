"""Freeze a complete Hanabi forecast or prepare independent stock Harbor trials."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from fractions import Fraction
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

PAIRINGS = ("LL", "LR", "RL", "RR")
HERE = Path(__file__).resolve().parent


class Prediction(TypedDict):
    deck_id: int
    pairing: str
    deck_sha256: str
    history_sha256: str
    decisions: int
    scores: list[int]


def digest(value: object) -> str:
    data = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")


def prediction(deck_id: int, pairing: str) -> Prediction:
    from task.environment.engine import Game
    from task.environment.player.policy import choose

    game = Game(deck_id)
    history = []
    while game.player is not None:
        player = game.player
        view = game.view()
        action = choose(view, pairing[player])
        history.append([game.turn, f"p{player}", digest(view), action])
        game.apply(action)
    return {
        "deck_id": deck_id,
        "pairing": pairing,
        "deck_sha256": game.deck_sha256,
        "history_sha256": digest(history),
        "decisions": len(history),
        "scores": game.scores(),
    }


def summarize(cells: list[Prediction]) -> dict[str, object]:
    by_deck: dict[int, dict[str, int]] = {}
    for cell in cells:
        scores = cell["scores"]
        if (
            len(scores) != 2
            or scores[0] != scores[1]
            or type(scores[0]) is not int
            or not 0 <= scores[0] <= 25
        ):
            raise ValueError("Hanabi requires one shared integer score in [0,25]")
        deck = by_deck.setdefault(cell["deck_id"], {})
        if cell["pairing"] in deck:
            raise ValueError("duplicate deck/pairing cell")
        deck[cell["pairing"]] = scores[0]
    if not by_deck or any(d.keys() != set(PAIRINGS) for d in by_deck.values()):
        raise ValueError("the paired summary requires complete four-pairing decks")
    gaps = [
        Fraction(d["LL"] + d["RR"] - d["LR"] - d["RL"], 2) for d in by_deck.values()
    ]
    return {
        "decks": len(by_deck),
        "pairings": {
            p: {
                "mean_score": sum(d[p] for d in by_deck.values()) / len(by_deck),
                "zero_scores": sum(d[p] == 0 for d in by_deck.values()),
            }
            for p in PAIRINGS
        },
        "self_minus_cross_mean": float(sum(gaps) / len(gaps)),
        "positive_decks": sum(g > 0 for g in gaps),
        "tied_decks": sum(g == 0 for g in gaps),
        "negative_decks": sum(g < 0 for g in gaps),
    }


def prepare(out: Path, decks: range) -> None:
    import yaml

    out = out.resolve()
    out.mkdir()
    jobs = out / "configs"
    jobs.mkdir()
    stamp = uuid4().hex[:12]
    cells = []
    for deck in decks:
        for p in PAIRINGS:
            name = f"han-{stamp}-d{deck:02}-{p.lower()}"
            task = out / "tasks" / name
            shutil.copytree(
                HERE / "task",
                task,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            env = task / "environment"
            (env / "config.json").write_text(
                json.dumps({"deck_id": deck}) + "\n", encoding="utf-8"
            )
            compose_file = env / "docker-compose.yaml"
            compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
            for i, side in enumerate(("p0", "p1")):
                service = compose["services"][side]
                service["environment"] = {
                    **service.get("environment", {}),
                    "CONVENTION": p[i],
                }
            compose_file.write_text(
                yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
            )
            write_new(
                jobs / f"d{deck:02}-{p}.json",
                {
                    "schema_version": 1,
                    "task": {"path": str(task)},
                    "trials_dir": str(out / "trials"),
                    "trial_name": name,
                    "participants": [
                        {"seat": s, "name": "oracle"} for s in ("p0", "p1")
                    ],
                    "environment": {
                        "type": "docker",
                        "force_build": True,
                        "delete": True,
                    },
                },
            )
            cells.append({"deck_id": deck, "pairing": p, "trial_name": name})
    write_new(
        out / "plan.json",
        {"schema": "seatlab.hanabi-plan/v1", "deck_ids": list(decks), "cells": cells},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    offline = sub.add_parser(
        "simulate", help="predict all four pairings on each registered physical deck"
    )
    native = sub.add_parser(
        "prepare", help="prepare separate tasks and stock trial configurations"
    )
    for cmd, limit in ((offline, 64), (native, 8)):
        cmd.add_argument(
            "out",
            type=Path,
            help="new file for predictions; new directory for preparation",
        )
        cmd.add_argument(
            "--decks",
            type=int,
            default=limit,
            help=f"number of decks starting at 0 (default {limit})",
        )
    args = parser.parse_args()
    if not 1 <= args.decks <= 64:
        parser.error("--decks must be between 1 and 64")
    decks = range(args.decks)
    if args.command == "prepare":
        prepare(args.out, decks)
        print(
            f"Prepared {len(decks) * len(PAIRINGS)} independent trials; see {args.out}/plan.json"
        )
    else:
        if args.out.exists():
            parser.error("prediction output already exists")
        cells = [prediction(deck, p) for deck in decks for p in PAIRINGS]
        summary = summarize(cells)
        write_new(
            args.out,
            {
                "schema": "seatlab.hanabi-predictions/v1",
                "cells": cells,
                "summary": summary,
            },
        )
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
