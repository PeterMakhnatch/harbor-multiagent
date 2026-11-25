import json
import shutil
import tempfile
from copy import deepcopy
from importlib import import_module
from pathlib import Path

import pytest

from examples.native_study import record_bytes
from examples.seatlab.exhibit_author_prepare import TURNS, prepare
from examples.seatlab.exhibit_authored import INSTRUCTION, WORLD, at
from examples.seatlab.exhibit_team_receipt import inspect_archive
from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp
from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TrialLock
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import AgentInfo, SeatResult, TimingInfo
from tests.unit.examples.test_exhibit_relay_capture import (
    END,
    START,
    write_native_archive,
)

ROLES = ("loader", "registrar", "depot")
ORDER = ("loader", "registrar", "depot", "loader")
PHASES = ("request", "registrar", "depot", "dispatch")


def replay(case, mode, turns, correct):
    session = import_module(f"{WORLD}.engine").Session(case, mode)
    digest_actions = {
        role: {
            "role": role,
            "phase": "waiting",
            "accepted_actions": [],
            "accepted_requests": [],
        }
        for role in ROLES
    }
    for i, role in enumerate(ORDER[:turns]):
        metadata = digest_actions[role]
        view = json.loads(json.dumps(session.view(role), sort_keys=True))
        instruction = (INSTRUCTION.parent / f"{role}.md").read_text()
        before = metadata["accepted_actions"]
        wire = encode_request(
            render_turn(instruction, view, deepcopy(before[-1]) if before else None)
        )
        if i == 0:
            action = {
                "slips": sorted((b["id"] for b in view["briefs"]), reverse=True),
                "op": "request",
            }
        elif i == 1:
            brief = next(b for b in view["briefs"] if b["weight"] == 4)
            cards = [at(view["cards"], item, brief["epoch"]) for item in brief["items"]]
            action = {
                "notes": [
                    {
                        "keys": sorted((c["value"] for c in cards), reverse=True),
                        "weight": 9,
                        "epoch": brief["epoch"],
                    }
                ],
                "ids": [cards[0]["id"]],
                "op": "file",
            }
        elif i == 2:
            assert len(view["bundles"]) == 1
            assert view["bundles"][0]["weight"] == (9 if mode == "authored" else 0)
            action = {"ids": [], "op": "file"}
        else:
            order = view["order"]
            expected = {
                item: at(
                    case["depot"],
                    at(case["registrar"], item, order["epoch"])["value"],
                    order["epoch"],
                )["value"]
                for item in order["items"]
            }
            if correct:
                action = {"routes": expected, "op": "commit"}
            else:
                first = order["items"][0]
                action = {
                    "op": "commit",
                    "routes": {
                        **expected,
                        first: next(b for b in view["bays"] if b != expected[first]),
                    },
                }
        metadata["accepted_requests"].append(request_stamp(wire))
        metadata["accepted_actions"].append(action)
        session.apply(role, action)
        metadata["phase"] = "dispatch" if i == 0 else "finished"
    return session.snapshot(), {
        k: v if v["accepted_actions"] else None for k, v in digest_actions.items()
    }


def materialize(plan, receipt, mode, turns, correct=False):
    row = receipt["arms"][mode]
    config = TrialConfig.model_validate_json((plan / row["config"]).read_bytes())
    lock = TrialLock.model_validate_json((plan / row["lock"]).read_bytes())
    case = import_module(f"{WORLD}.cases").make_case(
        receipt["seed"], "coupling", layout=receipt["layout"]
    )
    cut, metadata = replay(case, mode, turns, correct)
    observation = import_module(f"{WORLD}.diagnostics").observation(cut)
    assert observation["reward"] == (int(correct) if turns == 4 else None)
    paths = TrialPaths(config.trials_dir / config.trial_name)
    paths.verifier_dir.mkdir(parents=True)
    (paths.verifier_dir / "observation.json").write_bytes(record_bytes(observation))
    cut_path = paths.verifier_dir / "private-exhibit-cut.json"
    cut_path.write_bytes(record_bytes(cut))
    cut_path.chmod(0o600)
    if turns == 4:
        paths.reward_json_path.write_bytes(record_bytes({"reward": int(correct)}))
    seats = [
        SeatResult(
            seat=role,
            under_test=True,
            agent_info=AgentInfo(
                name="seatlab-exhibit-relay-owner", version="1.0.0", model_info=None
            ),
            agent_result=AgentContext(metadata=metadata[role]),
            agent_execution=TimingInfo(started_at=START, finished_at=END),
            execution_disposition="returned"
            if metadata[role] and metadata[role]["phase"] == "finished"
            else "cancelled",
        )
        for role in ROLES
    ]
    write_native_archive(config, lock, seats, observation, paths, START, END)
    return paths, metadata


@pytest.mark.parametrize(
    ("mode", "turns", "correct"),
    [
        ("authored", 4, True),
        ("author-masked", 4, False),
        ("authored", 3, False),
        ("author-masked", 2, False),
        ("authored", 1, False),
        ("author-masked", 0, False),
    ],
)
def test_three_owner_archive_preserves_missingness_and_physical_outcome(
    mode, turns, correct
):
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="exreg-") as directory:
        root = Path(directory).resolve()
        plan = root / "inputs"
        receipt = prepare(
            plan, 7, "a", {turn: root / str(i) for i, turn in enumerate(TURNS)}
        )
        paths, metadata = materialize(plan, receipt, mode, turns, correct)
        relocated = root / "relocated"
        shutil.copytree(paths.config_path.parent, relocated)
        report = inspect_archive(plan, mode, relocated)
        assert (
            report["evidence"] == "native_archive_consistent"
            and not report["provider_authenticated"]
        )
        assert [row["phase"] for row in report["accepted_turns"]] == list(
            PHASES[:turns]
        )
        assert report["depot_request_matched"] == (turns >= 3)
        assert report["native_reward"] == (int(correct) if turns == 4 else None)
        assert report["C"] == report["P"] == (0.0 if turns >= 3 else None)
        if turns >= 2:
            assert (
                report["prescribed_handoff"]["submitted"][
                    "inaccurate_positive_weight_count"
                ]
                == 1
            )
        else:
            assert report["prescribed_handoff"] is None
        if turns == 4:
            assert (
                report["accepted_turns"][-1]["request"]
                == metadata["loader"]["accepted_requests"][1]
            )
            invalid = next(arm for arm in ("authored", "author-masked") if arm != mode)
            with pytest.raises((ValueError, TypeError)):
                inspect_archive(plan, invalid, relocated)
            archived = relocated / paths.result_path.name
            broken = json.loads(archived.read_bytes())
            registrar = next(
                seat for seat in broken["seat_results"] if seat["seat"] == "registrar"
            )
            registrar["agent_result"]["metadata"]["accepted_requests"][0]["bytes"] += 1
            archived.write_text(json.dumps(broken))
            with pytest.raises((ValueError, TypeError)):
                inspect_archive(plan, mode, relocated)
