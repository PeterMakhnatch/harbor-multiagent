import argparse
import json
import sys
from functools import cache
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import TextIOWrapper
from pathlib import Path
from types import ModuleType
from typing import Any, Never, override

from . import dispatch_payload
from .capsule import view_hash

SYSTEM_PROMPT = "Answer only with JSON."
_ROOT = Path(__file__).resolve().parent
_TASK = _ROOT.parent / "tasks/dispatch-handoff"
_MANIFEST_SHA256 = "5e2759b8f56c52b056c1e33702cc7a321d6d3204826623cb3aeda08ff2fd79ab"
_RECEIPT_SHA256 = "86eaf01d4a5306ed9961bd1d39c5a4e8feff113fdec6be795e23a6c28f217c40"
_WORLDS = tuple(range(6801, 6809))
_ARMS = ("shown", "masked")
_EPISODES = 6
_REPLY_LIMIT = 64 * 1024


class _CaseError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid arguments. Use --help.\n")


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise _CaseError(message)


def _pinned_json(name: str, pin: str, limit: int) -> dict[str, Any]:
    with (_ROOT / "results" / name).open("rb") as stream:
        data = stream.read(limit + 1)
    _require(
        len(data) <= limit and sha256(data).hexdigest() == pin,
        "Public source digest mismatch.",
    )
    return json.loads(data)


@cache
def _task_module(path: str) -> ModuleType:
    spec = spec_from_file_location(f"_seatlab_dispatch_{Path(path).stem}", _TASK / path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load the stock Dispatch task.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _world_class() -> Any:
    return _task_module("environment/seatworld.py").World


def _sources() -> dict[int, dict[str, Any]]:
    manifest = _pinned_json("dispatch-peer-sources.json", _MANIFEST_SHA256, 16 * 1024)
    receipt = _pinned_json("live-confirmation.json", _RECEIPT_SHA256, 256 * 1024)
    _require(
        manifest["schema"] == "seatlab.public-dispatch-peer-sources/v1"
        and manifest["source_receipt_sha256"] == _RECEIPT_SHA256
        and receipt["schema"] == "seatlab.confirmation-native/v1",
        "Public source schema mismatch.",
    )
    rows = manifest["worlds"]
    named = [row for row in receipt["studies"]["notices"] if row["arm"] == "named"]
    roster = {row["world"]: row for row in named}
    _require(
        tuple(row["world"] for row in rows) == _WORLDS
        and len(named) == len(_WORLDS)
        and roster.keys() == set(_WORLDS),
        "Public source roster mismatch.",
    )
    for source in rows:
        row = roster[source["world"]]
        source_uuid = source["source_uuid"]
        trial_ref = sha256(
            f"seatlab-confirmation:v1:{source_uuid}".encode()
        ).hexdigest()
        _require(
            row["complete"] is True
            and row["behavior_confirmed"] is True
            and row["result_sha256"] == source["source_result_sha256"]
            and row["trial_ref"] == trial_ref,
            "Public source roster mismatch.",
        )
    return {row["world"]: row for row in rows}


def _prepared_world(world_class: Any, source: dict[str, Any]) -> Any:
    seed = source["world"]
    world = world_class(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": _EPISODES,
            "condition": "reordered",
        }
    )
    rows = world.view("inventory")["cases"]
    _require(
        view_hash(rows) == source["inventory_rows_sha256"], "Inventory digest mismatch."
    )
    world.submit("inventory", {"rows": rows})
    return world


def _case(world_class: Any, source: dict[str, Any]) -> tuple[Any, tuple[str, str]]:
    world = _prepared_world(world_class, source)
    prompts = dispatch_payload.project(
        world.view("dispatch")["cases"], source["world"], source["opened"]
    )
    for arm, prompt in zip(_ARMS, prompts, strict=True):
        _require(
            sha256(prompt.encode("utf-8")).hexdigest()
            == source[f"{arm}_prompt_sha256"],
            "User prompt digest mismatch.",
        )
    return world, prompts


def _score_reply(world: Any, seed: int, data: bytes) -> dict[str, int]:
    _require(len(data) <= _REPLY_LIMIT, "Receiver reply exceeds 64 KiB.")
    try:
        choices = dispatch_payload.decode_choices(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise _CaseError("Invalid first receiver reply.") from None

    rows = [
        {"id": episode, "bay": choices[alias]}
        for alias, episode in dispatch_payload.aliases(seed).items()
    ]
    world.submit("dispatch", {"rows": rows})
    result = _task_module("tests/score.py").score(world.evidence())
    return {
        "episodes": result["episodes"],
        "optimal": result["optimal"],
        "feasible": result["feasible"],
    }


def _score_stdin(world: Any, seed: int) -> dict[str, int | str]:
    data = sys.stdin.buffer.read(_REPLY_LIMIT + 1)
    return {"mode": "receiver_replay", "world": seed, **_score_reply(world, seed, data)}


def main() -> int:
    parser = _Parser(
        prog="python3.12 -BS -m examples.seatlab.dispatch_peer_case",
        description="Check, print, or score a public Dispatch receiver replay.",
        epilog=f"Receiver system instruction (separate from the user prompt): {SYSTEM_PROMPT}",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser(
        "check", help="verify all 8 worlds and 16 user prompts", allow_abbrev=False
    )
    prompt = commands.add_parser(
        "prompt", help="print one exact user prompt", allow_abbrev=False
    )
    score = commands.add_parser(
        "score", help="score one first receiver reply from stdin", allow_abbrev=False
    )
    for command in (prompt, score):
        command.add_argument("--world", type=int, choices=_WORLDS, required=True)
    prompt.add_argument("--arm", choices=_ARMS, required=True)
    args = parser.parse_args()

    try:
        sources = _sources()
        world_class = _world_class()
        if args.operation == "check":
            for source in sources.values():
                _case(world_class, source)
            print(
                json.dumps(
                    {
                        "worlds": len(sources),
                        "inventory_hashes_verified": len(sources),
                        "prompt_hashes_verified": len(sources) * len(_ARMS),
                    }
                )
            )
        else:
            world, prompts = _case(world_class, sources[args.world])
            if args.operation == "prompt":
                if isinstance(sys.stdout, TextIOWrapper):
                    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
                sys.stdout.write(prompts[_ARMS.index(args.arm)])
            else:
                print(json.dumps(_score_stdin(world, args.world)))
    except _CaseError as error:
        print(error, file=sys.stderr)
        return 1
    except (ImportError, KeyError, OSError, TypeError, ValueError):
        print("Cannot process the public Dispatch replay.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
