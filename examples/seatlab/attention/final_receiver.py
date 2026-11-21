import argparse
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from .. import evidence
from ..export_capsules import reconstructed_capsules
from ..visibility.clarify_run import _object
from ..visibility.route_run import _MAX_REPLY_BYTES
from . import evidence_join, protocol

_FORMATS = ("nested", "joined")
_COHORT = "evidence-join"


def _load_native_study() -> ModuleType:
    seatlab = str(Path(__file__).resolve().parents[1])
    sys.path.insert(0, seatlab)
    try:
        from . import __main__ as study

        return study
    finally:
        sys.path.remove(seatlab)


_native = _load_native_study()


class Source:
    def __init__(
        self,
        metadata: dict[str, Any],
        prompts: dict[str, str],
        dispatch_cases: list[dict[str, Any]],
        stock_world: dict[str, Any],
        seed: int,
        repetition: int,
    ) -> None:
        self.metadata = metadata
        self._prompts = prompts
        self._dispatch_cases = dispatch_cases
        self._stock_world = stock_world
        self._seed = seed
        self._repetition = repetition

    def prompt(self, format: str) -> str:
        if format not in _FORMATS:
            raise ValueError("Unknown receiver prompt format")
        return self._prompts[format]

    def score(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or len(text) > _MAX_REPLY_BYTES:
            raise ValueError("Expected a bounded first decision reply")
        try:
            if len(text.encode("utf-8")) > _MAX_REPLY_BYTES:
                raise ValueError("Oversized first decision reply")
            parsed = protocol.decide(
                text, self._dispatch_cases, self._seed, self._repetition
            )
        except ValueError:
            raise ValueError("Invalid first decision reply") from None
        bays = {row["id"]: row["bay"] for row in parsed["rows"]}
        cases = self._stock_world["cases"]
        actions = [{"id": case["id"], "bay": bays[case["id"]]} for case in cases]
        grade = evidence._score(
            {
                **self._stock_world,
                "cases": [
                    {**case, "chosen": action["bay"]}
                    for case, action in zip(cases, actions, strict=True)
                ],
            }
        )
        return {
            "actions": actions,
            "optimal": grade["optimal"],
            "feasible": grade["feasible"],
            "episodes": grade["episodes"],
        }


def _enrolled_cell(root: Path, cell_id: str) -> tuple[dict[str, Any], str]:
    teams = _native.teams
    teams._check_path(root, 0o700, directory=True)
    teams._check_path(root / "cells", 0o700, directory=True)
    record = _object(teams._read_sealed(root / "register.json"))
    if record.keys() != {"plan", "spend_directory"}:
        raise ValueError("Invalid native study register")
    plan = record["plan"]
    if plan["schema"] != evidence_join.PLAN_SCHEMA:
        raise ValueError("Expected a native evidence-join study")
    transport = plan["transport"]
    spend = record["spend_directory"]
    if transport == "commands" and spend is None:
        digest = sha256(teams._read_sealed(root / "commands.json")).hexdigest()
    elif (
        transport == "direct"
        and isinstance(spend, str)
        and os.path.isabs(spend)
        and os.path.normpath(spend) == spend
        and "\0" not in spend
    ):
        digest = None
    else:
        raise ValueError("Invalid native study transport registration")
    expected = _native._plan(transport, digest, cohort=_COHORT)
    if teams._bytes(plan) != teams._bytes(expected):
        raise ValueError("Registered native sources or cells changed")
    cells = {cell["cell_id"]: cell for cell in expected["cells"]}
    with os.scandir(root / "cells") as entries:
        if any(
            entry.name not in cells or not entry.is_dir(follow_symlinks=False)
            for entry in entries
        ):
            raise ValueError("Unexpected native trial claim")
    return cells[cell_id], transport


def _load_source(root: Path, cell: dict[str, Any], transport: str) -> Source:
    teams = _native.teams
    trial = teams._trial_dir(root, cast(teams.Cell, cell))
    if trial.resolve() != trial:
        raise ValueError("Expected the registered physical trial path")
    row = _native._observed(root, cell, transport, None)
    if row["state"] != "complete":
        raise ValueError("Expected a complete first-attempt claim")
    outcome = row["outcome"]
    receipt, stock_world = evidence.read_trial_with_world(trial)
    seed = cell["seed"]
    repetition = cell["repetition"]
    if (
        receipt["trial_id"] != outcome["trial_id"]
        or receipt["fingerprint"] != outcome["fingerprint"]
        or receipt["condition"] != cell["condition"]
        or receipt["seed"] != seed
        or receipt["score"]["episodes"] != protocol.EPISODES
    ):
        raise ValueError("Native trial evidence changed during inspection")
    dispatch = reconstructed_capsules(stock_world)["dispatch"]
    if dispatch["basis"] != "bound":
        raise ValueError("Expected the acknowledged dispatch observation")
    cases = cast(dict[str, Any], dispatch["view"])["cases"]
    opened = protocol._open_pair(outcome["opened"])
    nested = protocol.decide_prompt(cases, seed, repetition, opened)
    joined = evidence_join.project(nested, "joined")
    nested_bytes = nested.encode("utf-8")
    if evidence_join.unjoin(joined).encode("utf-8") != nested_bytes:
        raise ValueError("Evidence layout inverse differs from the nested prompt")
    digests = {
        "nested": sha256(nested_bytes).hexdigest(),
        "joined": sha256(joined.encode("utf-8")).hexdigest(),
    }
    source_format = cell["arm"]
    if outcome["turns"][-1]["prompt_sha256"] != digests[source_format]:
        raise ValueError("Reconstructed decision prompt differs from the native turn")
    metadata = {
        "source_cell": cell["cell_id"],
        "world": seed,
        "trial_id_sha256": sha256(receipt["trial_id"].encode("utf-8")).hexdigest(),
        "source_format": source_format,
        "stock_world_sha256": receipt["fingerprint"],
        "prompt_sha256": digests,
    }
    return Source(
        metadata,
        {"nested": nested, "joined": joined},
        cases,
        stock_world,
        seed,
        repetition,
    )


def load(study: Path, cell_id: str) -> Source:
    try:
        root = _native._checked_study(study)
        cell, transport = _enrolled_cell(root, cell_id)
        return _load_source(root, cell, transport)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OverflowError,
        RuntimeError,
    ):
        raise ValueError(
            "Cannot load a verified native evidence-join first attempt"
        ) from None


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in ("check", "prompt", "score"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        sub.add_argument("--study", type=Path, required=True)
        sub.add_argument("--cell", required=True)
        if mode == "prompt":
            sub.add_argument("--format", choices=_FORMATS, required=True)
    args = cli.parse_args(argv)
    try:
        source = load(args.study, args.cell)
        if args.mode == "prompt":
            sys.stdout.buffer.write(source.prompt(args.format).encode("utf-8"))
            return 0
        if args.mode == "score":
            raw = sys.stdin.buffer.read(_MAX_REPLY_BYTES + 1)
            if len(raw) > _MAX_REPLY_BYTES:
                raise ValueError("Oversized first decision reply")
            result = source.score(raw.decode("utf-8"))
        else:
            result = source.metadata
        print(
            json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        )
    except (OSError, ValueError):
        cli.exit(1, "Cannot verify the receiver source or score the first reply.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
