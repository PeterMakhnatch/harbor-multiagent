"""Check one registered native committee prefix without a model or verifier."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

from . import analysis, driver, prefix_packet, prefix_schedule, schedule

_AGENT_IMPORT = "examples.seatlab.committee.prefix_probe_seat:PrefixProbeSeat"
_AGENT_NAME = "seatlab-prefix-cut-check"
_AGENT_VERSION = "1.0.0"
_SCHEMA = "seatlab.native-prefix-probe/v1"
_RECEIPT_SCHEMA = "seatlab.native-prefix-probe-seat/v1"


def _load_cut(map_index: int) -> prefix_packet.Checkpoint:
    try:
        cut = prefix_packet.load(prefix_schedule.PACKET, prefix_schedule.PACKET_SHA256)[
            map_index
        ]
    except (OSError, ValueError, KeyError, RecursionError):
        raise driver._Rejected("registered_packet_unavailable") from None
    if cut is None:
        raise driver._Rejected("named_source_ineligible")
    return cut


def _run_id(map_index: int) -> str:
    return f"cut-check-m{map_index}"


def _receipt(role: str, cut: prefix_packet.Checkpoint) -> dict[str, Any]:
    return {
        "schema": _RECEIPT_SCHEMA,
        "status": "complete",
        "role": role,
        "map_index": cut.map_index,
        "run_id": _run_id(cut.map_index),
        "cut_status": {
            "message_count": len(cut.board["messages"]),
            "votes_submitted": 0,
            "done": [],
        },
        "packet_sha256": prefix_schedule.PACKET_SHA256,
        "prefix_sha256": cut.prefix_sha256,
        "event_count": len(cut.events),
    }


def _config(directory: Path, task: Path, map_index: int) -> dict[str, Any]:
    # Harbor uses the trial name as the Docker project identity. Two fresh
    # output directories must not share the same project on a local socket.
    return {
        "schema_version": 1,
        "task": {"path": str(task)},
        "trial_name": f"{_run_id(map_index)}-{uuid4().hex}",
        "trials_dir": str(directory / "trials"),
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": True},
        "participants": [
            {
                "seat": role,
                "import_path": _AGENT_IMPORT,
                "kwargs": {"role": role, "map_index": map_index},
            }
            for role in schedule.ROLES
        ],
    }


def _launch(directory: Path, harbor: Path, env: dict[str, str]) -> int:
    child_env = driver._native_env(env)
    if "PATH" in child_env:
        # In particular, do not allow Docker lookup in a task or trial CWD.
        child_env["PATH"] = os.pathsep.join(
            entry
            for entry in child_env["PATH"].split(os.pathsep)
            if Path(entry).is_absolute()
        )
        if not child_env["PATH"]:
            del child_env["PATH"]
    with TemporaryDirectory(prefix=".native-home-", dir=directory) as home:
        child_env["HOME"] = home
        plugins = driver._DOCKER_DESKTOP_PLUGINS
        if plugins.is_dir():
            docker = Path(home) / ".docker"
            docker.mkdir(mode=0o700)
            (docker / "config.json").write_text(
                json.dumps({"cliPluginsExtraDirs": [str(plugins)]}), encoding="utf-8"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(directory / "native.log", flags, 0o600), "wb") as log:
            os.fchmod(log.fileno(), 0o600)
            return subprocess.run(
                [str(harbor), "trial", "start", "-c", str(directory / "config.json")],
                cwd=directory,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=driver._TRIAL_TIMEOUT_SEC,
                umask=0o077,
            ).returncode


def _validate_result(
    trial: Path, config: dict[str, Any], cut: prefix_packet.Checkpoint
) -> int:
    reason = "native_trial_incomplete"
    require = analysis._require
    result = analysis._object(analysis._json(trial, "result.json", reason), reason)
    task = config["task"]
    recorded = analysis._object(result["config"], reason)
    environment = recorded["environment"]
    require(
        UUID(result["id"]).int
        and type(result["schema_version"]) is int
        and result["schema_version"] == config["schema_version"]
        and result["trial_name"] == config["trial_name"]
        and result["trial_uri"] == trial.as_uri()
        and result["task_id"] == task
        and result["task_name"] == driver._TASK_NAME
        and result["task_checksum"] == schedule.TASK_CHECKSUM
        and result["exception_info"] is None
        and result["verifier_result"] is None
        and result["reward_valid"] is False
        and isinstance(result["finished_at"], str)
        and result["finished_at"]
        and recorded["trial_name"] == config["trial_name"]
        and recorded["trials_dir"] == config["trials_dir"]
        and recorded["task"]["path"] == task["path"]
        and all(
            type(environment[k]) is type(v) and environment[k] == v
            for k, v in config["environment"].items()
        )
        and environment.get("import_path") is None
        and recorded["verifier"]["disable"] is True
        and not any(
            recorded.get(k) for k in ("install_only", "source_trial", "user_agent")
        ),
        reason,
    )
    participants = analysis._roles(recorded["participants"], reason)
    seats = analysis._roles(result["seat_results"], reason)
    for expected in config["participants"]:
        role = expected["seat"]
        participant = participants[role]
        seat = seats[role]
        info = seat["agent_info"]
        context = seat["agent_result"]
        finished = seat["agent_execution"]["finished_at"]
        require(
            participant["import_path"] == _AGENT_IMPORT
            and participant["kwargs"] == expected["kwargs"]
            and all(
                participant.get(k) is None
                for k in (
                    "name",
                    "model_name",
                    "base_url",
                    "model_api_key_name",
                    "model_api_format",
                )
            )
            and not participant.get("env")
            and seat["under_test"] is True
            and seat["exception_info"] is None
            and isinstance(finished, str)
            and finished
            and info["name"] == _AGENT_NAME
            and info["version"] == _AGENT_VERSION
            and info["model_info"] is None
            and all(
                context.get(k) is None
                for k in (
                    "n_input_tokens",
                    "n_cache_tokens",
                    "n_output_tokens",
                    "cost_usd",
                    "model_usage",
                )
            )
            and prefix_packet._canonical(context["metadata"])
            == prefix_packet._canonical(_receipt(role, cut)),
            reason,
        )
    return len(seats)


def _accept(
    directory: Path, config: dict[str, Any], cut: prefix_packet.Checkpoint
) -> dict[str, Any]:
    try:
        trial = analysis._path(
            directory, f"trials/{config['trial_name']}", directory=True
        )
        seats = _validate_result(trial, config, cut)
    except (
        analysis._Excluded,
        OSError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise driver._Rejected("native_trial_incomplete") from None

    # Read Harbor's own stock main-service artifact, not any seat's receipt or
    # the live restore helper's transient audit download. No suffix is allowed:
    # the probe does not call /done, issue a final chat read, or submit a vote.
    try:
        events = analysis._events(trial)
        if len(events) != len(cut.events):
            raise ValueError
        normalized = [prefix_packet.normalize_event(event) for event in events]
        native_bytes = prefix_packet._canonical(normalized)
        native_digest = hashlib.sha256(native_bytes).hexdigest()
        if (
            any(event["event_type"] == "vote" for event in normalized)
            or native_digest != cut.prefix_sha256
            or native_bytes != prefix_packet._canonical(cut.events)
        ):
            raise ValueError
    except (
        analysis._Excluded,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise driver._Rejected("native_prefix_mismatch") from None
    return {
        "schema": _SCHEMA,
        "status": "accepted",
        "map_index": cut.map_index,
        "complete_seats": seats,
        "packet_sha256": prefix_schedule.PACKET_SHA256,
        "source_event_count": len(cut.events),
        "native_event_count": len(events),
        "source_prefix_sha256": cut.prefix_sha256,
        "native_prefix_sha256": native_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--map", dest="map_index", type=int, choices=range(6), required=True
    )
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--harbor", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        cut = _load_cut(args.map_index)
        task = driver._task_dir(args.task)
        _, team = driver._helpers()
        directory = team._root(args.out)
        if directory.is_relative_to(task):
            raise driver._Rejected("private_output_invalid")
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            raise driver._Rejected("private_output_already_exists") from None
        team._check_path(directory, 0o700, directory=True)
        harbor, _, env = driver._preflight(args.harbor, task)
        config = _config(directory, task, args.map_index)
        team._seal(directory / "config.json", team._bytes(config))
        (directory / "trials").mkdir(mode=0o700)
        if _launch(directory, harbor, env) != 0:
            raise driver._Rejected("native_nonzero_exit")
        accepted = _accept(directory, config, cut)
    except driver._Rejected as exc:
        print(f"prefix probe: {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("prefix probe: native_launch_timeout", file=sys.stderr)
        return 1
    except (OSError, ValueError, RecursionError):
        print("prefix probe: local_setup_failed", file=sys.stderr)
        return 1
    print(json.dumps(accepted, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
