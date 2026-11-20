"""Export and replay one recorded native Dispatch Handoff pair offline.

Give a listener only the selected user prompt and, if supported, the separate
system instruction. The packet itself includes hidden evidence and past choices.
The recorded prompt hash covers user text, not the system text or HTTP body.
Export checks the private record. Offline verification checks consistency;
an external SHA-256 can also pin the exact published packet bytes. Neither
check is a signature or independent proof of a provider call.
"""

import argparse
import json
import sys
from copy import deepcopy
from functools import cache
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any

from ..capsule import _reject_constant, _unique_object, encode_view, view_hash
from ..evidence import _score
from ..export_capsules import reconstructed_views
from . import pair_report

_SCHEMA = "seatlab.visibility-dispatch-packet/v1"
_DEFAULT_PAIR = (3101, 1)
_ARMS = ("fresh", "silent")
_MAX_PACKET_BYTES = 16_384
_MAX_CHOICES_CHARS = 8192  # The original native seat's response limit.

# Fixed source commitments for the blind instructions, not native records.
_INSTRUCTIONS_SHA256 = (
    "3ecd1760a8d19ecf5357ae8905ad17fc2e8ee2ca31ec3346538e77b51d4eaa1f"
)
# The system message is source-defined, not part of the native prompt hash.
_SYSTEM_SHA256 = "bf536051049f6a4272229d090710ff82497da3b3b1a3a02405b35e60508ba8b5"


class _InvalidPacket(ValueError):
    """Safe diagnostics: never contains paths or text from a source file."""


def _object(value: object, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != set(keys):
        raise _InvalidPacket("unexpected packet fields")
    return value


def _load_json(value: str | bytes) -> Any:
    return json.loads(
        value, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )


def _text_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048 or not value.isascii():
        raise _InvalidPacket("invalid request text")
    return sha256(value.encode("ascii")).hexdigest()


def _check_pair(seed: object, repetition: object) -> None:
    if (
        type(seed) is not int
        or seed not in pair_report._SEEDS
        or type(repetition) is not int
        or repetition not in pair_report._REPETITIONS
    ):
        raise _InvalidPacket("expected a seed and repetition in the native grid")


@cache
def _world_type() -> Any:
    return run_path(str(pair_report._WORLD_PATH))["World"]


def _replay(seed: int, arm: str, inventory: object, dispatch: object) -> dict[str, Any]:
    world = _world_type()(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": pair_report._EPISODES,
            "condition": arm,
        }
    )
    # Apply stored inventory choices. Do not generate replacements from truth.
    world.submit("inventory", inventory)
    world.submit("dispatch", dispatch)
    return world.evidence()


def _decisions(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        role: {
            "rows": [{"id": case["id"], field: case[source]} for case in world["cases"]]
        }
        for role, field, source in (
            ("inventory", "reports", "reports"),
            ("dispatch", "bay", "chosen"),
        )
    }


def _grade(world: dict[str, Any]) -> dict[str, Any]:
    score = _score(world)
    return {**score, "reward": score["optimal"] / score["episodes"]}


def verify_packet(value: object) -> dict[str, Any]:
    """Validate a packet against the public world and recorded commitments."""
    packet = _object(
        value, "schema", "seed", "repetition", "system", "world", "world_sha256", "arms"
    )
    seed = packet["seed"]
    _check_pair(seed, packet["repetition"])
    if packet["schema"] != _SCHEMA:
        raise _InvalidPacket("unknown packet schema")
    if _text_digest(packet["system"]) != _SYSTEM_SHA256:
        raise _InvalidPacket("system instruction does not match the transport source")
    latent = pair_report._latent_cases(seed)
    if (
        encode_view(packet["world"]) != encode_view(latent)
        or view_hash(latent) != packet["world_sha256"]
    ):
        raise _InvalidPacket("world does not match the recorded public seed")
    arms = _object(packet["arms"], *_ARMS)
    for arm in _ARMS:
        data = _object(
            arms[arm],
            "request",
            "request_sha256",
            "inventory",
            "inventory_sha256",
            "dispatch",
            "dispatch_sha256",
            "score",
        )
        digest = _text_digest(data["request"])
        if digest != data["request_sha256"]:
            raise _InvalidPacket(
                f"{arm}: request does not match the recorded prompt hash"
            )
        world = _replay(seed, arm, data["inventory"], data["dispatch"])
        decisions = _decisions(world)
        for role in ("inventory", "dispatch"):
            if (
                encode_view(data[role]) != encode_view(decisions[role])
                or view_hash(decisions[role]["rows"]) != data[f"{role}_sha256"]
            ):
                raise _InvalidPacket(f"{arm}: {role} decisions differ from the record")
        cases = reconstructed_views(world)["dispatch"]["cases"]
        blind_view = encode_view({"role": "dispatch", "cases": cases}).decode("ascii")
        instructions, separator, body = data["request"].partition("\n")
        if _text_digest(instructions + separator) != _INSTRUCTIONS_SHA256:
            raise _InvalidPacket(f"{arm}: request instructions are not allowlisted")
        if body != blind_view:
            raise _InvalidPacket(
                f"{arm}: request does not match the committed delivery"
            )
        if encode_view(data["score"]) != encode_view(_grade(world)):
            raise _InvalidPacket(f"{arm}: score does not match the stock scorer")
    return packet


def export_packet(
    study: Path, seed: int = _DEFAULT_PAIR[0], repetition: int = _DEFAULT_PAIR[1]
) -> dict[str, Any]:
    """Read both original attempts without running a seat or changing the study."""
    _check_pair(seed, repetition)
    # Only export needs the existing native Harbor environment. All receiver
    # commands work with a checkout and Python alone.
    try:
        from . import native_pairs
    except ModuleNotFoundError:
        raise _InvalidPacket(
            "export requires the native Harbor Python environment with "
            "PYTHONPATH=examples/seatlab:examples:."
        ) from None

    root = study.resolve(strict=True)
    cells = root / "cells"
    if not cells.is_dir() or cells.is_symlink():
        raise _InvalidPacket("study has no native cells directory")
    registered = {
        cell["condition"]: cell
        for cell in native_pairs.grid()
        if (cell["seed"], cell["repetition"]) == (seed, repetition)
    }
    pair = {arm: pair_report._Cell(seed, repetition, arm) for arm in _ARMS}
    results = {
        cell: pair_report._inspect(cells / cell.cell_id, cell) for cell in pair.values()
    }
    pair_report._reject_reused_trials(results)
    arms = {}
    latent = pair_report._latent_cases(seed)
    fingerprint = view_hash(latent)
    for arm, cell in pair.items():
        found = results[cell]
        if found.evidence is None:
            raise _InvalidPacket(f"{arm}: missing or invalid recorded first attempt")
        try:
            config = native_pairs._trial_config(root, registered[arm])
            directory = cells / cell.cell_id
            receipt = pair_report._read_sealed(directory / "receipt.json")
            if receipt["trial_name"] != config["trial_name"]:
                raise ValueError("sealed reservation does not name the native trial")
            native_pairs.team_report._check_config(
                pair_report._read_sealed(directory / "config.json"), config
            )
            trial = directory / "trials" / config["trial_name"]
            native: dict[str, Any] = {"native_returncode": 0, "seats": {}}
            native_pairs._inspect(trial, config, registered[arm], native)
            if (
                native["trial_id"] != found.trial_id
                or native["native_score"] != found.evidence.score
                or native["fingerprint"] != found.evidence.fingerprint
                or any(
                    native["seats"][role]["invocation"][key] != recorded
                    for role, hashes in found.evidence.hashes.items()
                    for key, recorded in hashes.items()
                )
            ):
                raise ValueError("native result and sealed receipt differ")
            world = native_pairs.team_report._read_object(
                trial / "artifacts/logs/world/handoff.json"
            )
            grade = _grade(world)
            footprint = [
                {key: case[key] for key in latent[0]} for case in world["cases"]
            ]
            if (
                world["settings"]["seed"] != seed
                or world["settings"]["condition"] != arm
                or view_hash(footprint) != fingerprint
                or grade["reward"] != found.evidence.score
            ):
                raise ValueError("native dossier and sealed receipt differ")
            request = native_pairs._prompt(
                "dispatch", reconstructed_views(world)["dispatch"]["cases"]
            )
            digest = _text_digest(request)
            if digest != found.evidence.hashes["dispatch"]["prompt_sha256"]:
                raise ValueError("reconstructed request does not match attestation")
            arms[arm] = {
                "request": request,
                "request_sha256": digest,
                "inventory_sha256": found.evidence.hashes["inventory"][
                    "decision_sha256"
                ],
                "dispatch_sha256": found.evidence.hashes["dispatch"]["decision_sha256"],
                **_decisions(world),
                "score": grade,
            }
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            raise _InvalidPacket(
                f"{arm}: native evidence cannot verify the exact attested request and decisions"
            ) from None
    return verify_packet(
        {
            "schema": _SCHEMA,
            "seed": seed,
            "repetition": repetition,
            "system": native_pairs.deepseek._SYSTEM,
            "world": deepcopy(latent),
            "world_sha256": fingerprint,
            "arms": arms,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export one pair from native evidence")
    export.add_argument("study", type=Path)
    export.add_argument(
        "--pair",
        nargs=2,
        type=int,
        metavar=("SEED", "REP"),
        default=_DEFAULT_PAIR,
        help="choose one registered pair (default: 3101 1)",
    )
    verify = commands.add_parser("verify", help="check public packet integrity")
    prompt = commands.add_parser("prompt", help="write exact blind user text only")
    score = commands.add_parser("score", help="score new choices with the stock scorer")
    for command in (prompt, score):
        command.add_argument("packet", type=Path)
        command.add_argument("arm", choices=_ARMS)
    for command in (verify, prompt, score):
        command.add_argument(
            "--expect-sha256",
            help="require the exact packet file SHA-256 from a trusted source",
        )
    verify.add_argument("packet", type=Path)
    score.add_argument(
        "choices_json", metavar="CHOICES_JSON", help="inline JSON object"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            # Canonical bytes without a newline, so sha256sum applies directly.
            sys.stdout.buffer.write(encode_view(export_packet(args.study, *args.pair)))
            return 0
        else:
            with args.packet.open("rb") as source:
                raw = source.read(_MAX_PACKET_BYTES + 1)
            if len(raw) > _MAX_PACKET_BYTES:
                raise _InvalidPacket("packet exceeds the size limit")
            digest = sha256(raw).hexdigest()
            if args.expect_sha256 is not None and args.expect_sha256 != digest:
                raise _InvalidPacket(
                    "packet SHA-256 does not match the external digest"
                )
            packet = verify_packet(_load_json(raw))
            if args.command == "prompt":
                sys.stdout.buffer.write(
                    packet["arms"][args.arm]["request"].encode("ascii")
                )
                return 0
            if args.command == "score":
                if len(args.choices_json) > _MAX_CHOICES_CHARS:
                    raise _InvalidPacket("choices exceed the size limit")
                output = _grade(
                    _replay(
                        packet["seed"],
                        args.arm,
                        packet["arms"][args.arm]["inventory"],
                        _load_json(args.choices_json),
                    )
                )
            else:
                output = {
                    "integrity": "seeded world, choices and packet commitments agree",
                    "packet_sha256": digest,
                    "external_commitment": (
                        "not supplied" if args.expect_sha256 is None else "matched"
                    ),
                    "authenticity": "not independently verified",
                    "user_request_basis": (
                        "reconstructed; user text matches the packet's reported native digest"
                    ),
                    "system_basis": "fixed transport source; not covered by the prompt hash",
                    "world_basis": "synthetic public seed; not independently captured",
                }
    except _InvalidPacket as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        parser.exit(2, f"{parser.prog}: invalid or unreadable replay input\n")
    print(encode_view(output).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
