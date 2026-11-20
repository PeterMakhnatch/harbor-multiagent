import importlib
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from runpy import run_path
from stat import S_IMODE
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4


import pytest

from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import TrialConfig

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    pairs = importlib.import_module("examples.seatlab.visibility.native_pairs")


def test_grid_has_one_cell_per_arm_and_balanced_pair_order(tmp_path: Path) -> None:
    grid = pairs.grid()
    assert len({cell["cell_id"] for cell in grid}) == len(grid) == 24
    for index, seed in enumerate((3101, 3103, 3109, 3119)):
        for repetition in range(3):
            offset = 2 * (3 * index + repetition)
            block = grid[offset : offset + 2]
            assert [(row["seed"], row["repetition"]) for row in block] == [
                (seed, repetition),
                (seed, repetition),
            ]
            arms = (
                ["fresh", "silent"]
                if (index + repetition) % 2 == 0
                else ["silent", "fresh"]
            )
            assert [row["condition"] for row in block] == arms
            for cell in block:
                config = pairs._trial_config(tmp_path, cell)
                assert config["environment"]["type"] == "docker"
                assert config["agent_timeout_multiplier"] == 2.0
                assert config["verifier"] == {"disable": False}
                assert [p["seat"] for p in config["participants"]] == [
                    "inventory",
                    "dispatch",
                ]
                assert all(
                    p["import_path"]
                    == "examples.seatlab.visibility.native_pairs:DirectFlashSeat"
                    and p["model_name"] == "deepseek/deepseek-flash"
                    and p["kwargs"] == {"run_id": cell["cell_id"]}
                    for p in config["participants"]
                )


def test_host_pythonpath_uses_this_checkout_source() -> None:
    env = pairs._host_env({})
    root = Path(__file__).resolve().parents[3]
    assert env["PYTHONPATH"].split(os.pathsep)[-1] == str(root / "src")
    assert (root / "src").is_dir()


def _environment(role: str) -> SeatEnvironment:
    seat = Mock(spec=SeatEnvironment)
    seat.service = role
    return cast(SeatEnvironment, seat)


async def _offline_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cell: Any
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    """Exercise our adapter against the original World, with no service or model."""
    world_code = run_path(str(pairs.run_panel.TASK_DIR / "environment/seatworld.py"))
    settings = {
        "schema": "seatlab.scenario/v1",
        "seed": cell["seed"],
        "condition": cell["condition"],
        "episodes": 4,
    }
    world = world_code["World"](settings)
    with pytest.raises(world_code["NotReady"]):
        world.view("dispatch")
    prompts: dict[str, str] = {}
    acknowledgments: dict[str, dict[str, str]] = {}
    records: dict[str, Any] = {}

    def answer(prompt: str) -> str:
        view = json.loads(prompt.split("\n", maxsplit=1)[1])
        role = view["role"]
        assert role not in prompts
        prompts[role] = prompt
        assert view.keys() == {"role", "cases"}
        # Deliberately use reports that do not match the live revision 2 views.
        # The delivery path must relay these commitments, not the ground truth.
        if role == "inventory":
            rows = [
                {
                    "id": case["id"],
                    "reports": [
                        {
                            "revision": 2,
                            "available": [
                                bay
                                for bay in "DCBA"
                                if bay not in case["reports"][1]["available"]
                            ],
                        },
                        case["reports"][0],
                    ],
                }
                for case in reversed(view["cases"])
            ]
        else:
            rows = [{"id": case["id"], "bay": "A"} for case in view["cases"]]
        return json.dumps({"rows": rows})

    async def request(
        environment: SeatEnvironment,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        view_sha256: str | None = None,
    ) -> tuple[int, str]:
        role = environment.service
        if path == "/view":
            return 200, json.dumps(world.view(role))
        assert path == "/submit" and role not in acknowledgments
        assert view_sha256 is not None
        assert view_sha256 == pairs.view_hash(world.view(role))
        acknowledgments[role] = {"ack_sha256": view_sha256}
        return 200, json.dumps(world.submit(role, payload))

    monkeypatch.setattr(pairs.deepseek, "MODEL", "deepseek-flash")
    monkeypatch.setattr(
        pairs.deepseek,
        "turn",
        Mock(
            side_effect=lambda prompt, *, run_id, role: pairs.deepseek.SeatTurn(
                answer(prompt), "deepseek-flash", 12, 2, 4
            )
        ),
    )
    monkeypatch.setattr(pairs.seat_agent, "_world_request", request)
    for role in ("inventory", "dispatch"):
        seat = pairs.DirectFlashSeat(
            tmp_path, model_name=pairs.DECLARED_MODEL, run_id="s3101-r0-fresh"
        )
        context = AgentContext()
        await seat.run(
            "instruction contains a condition label",
            _environment(role),
            context,
        )
        records[role] = {
            "seat": role,
            "under_test": True,
            "exception_info": None,
            "agent_execution": {
                "started_at": "2026-11-20T00:00:00Z",
                "finished_at": "2026-11-20T00:00:01Z",
            },
            "agent_info": seat.to_agent_info().model_dump(mode="json"),
            "agent_result": context.model_dump(mode="json"),
        }
        assert (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
        ) == (12, 2, 4)
        assert context.cost_usd is None
        assert context.metadata is not None
        assert context.metadata["model_turns"] == 1
        assert context.metadata["stage"] == "complete"
        assert context.metadata["prompt_sha256"] == pairs._prompt_hash(prompts[role])
        assert context.metadata["view_sha256"] == acknowledgments[role]["ack_sha256"]
    world_record = world.evidence()
    world_record["observation_receipts"] = acknowledgments
    return prompts, world_record, records


async def test_original_delivery_only_changes_the_dispatch_bulletins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fresh, silent = pairs.grid()[:2]
    fresh_prompts, fresh_world, _ = await _offline_world(monkeypatch, tmp_path, fresh)
    silent_prompts, silent_world, _ = await _offline_world(
        monkeypatch, tmp_path, silent
    )
    assert fresh_prompts["inventory"] == silent_prompts["inventory"]
    fresh_rules, fresh_body = fresh_prompts["dispatch"].split("\n", maxsplit=1)
    silent_rules, silent_body = silent_prompts["dispatch"].split("\n", maxsplit=1)
    assert fresh_rules == silent_rules
    fresh_view, silent_view = json.loads(fresh_body), json.loads(silent_body)
    for visible, hidden, actual, other in zip(
        fresh_view["cases"],
        silent_view["cases"],
        fresh_world["cases"],
        silent_world["cases"],
        strict=True,
    ):
        assert visible.pop("messages") == [actual["reports"][1]]
        assert hidden.pop("messages") == []
        assert visible == hidden
        assert actual["reports"] == other["reports"]
        assert actual["reports"][1]["available"] != actual["after"]


@pytest.mark.parametrize(
    "stage",
    [
        "direct_unavailable",
        "provider_response_invalid",
        "invalid_model_decisions",
        "submission_unacknowledged",
    ],
)
async def test_actor_rejects_one_bad_answer_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str
) -> None:
    world = run_path(str(pairs.run_panel.TASK_DIR / "environment/seatworld.py"))[
        "World"
    ](
        {
            "schema": "seatlab.scenario/v1",
            "seed": 3101,
            "condition": "silent",
            "episodes": 4,
        }
    )
    world_view = world.view("inventory")
    view = AsyncMock(return_value=world_view)
    submit = AsyncMock(return_value=(409, '{"accepted":false}'))
    monkeypatch.setattr(pairs.deepseek, "MODEL", "deepseek-flash")
    rows = world_view["cases"] if stage == "submission_unacknowledged" else []
    invoke = Mock(
        return_value=pairs.deepseek.SeatTurn(
            json.dumps({"rows": rows}), "deepseek-flash", 12, 2, 4
        )
    )
    if stage == "direct_unavailable":
        invoke.side_effect = pairs.deepseek.SeatUnavailable("private diagnostic")
    elif stage == "provider_response_invalid":
        invoke.side_effect = pairs.deepseek.SeatUnavailable(
            stage, settled_usage=(12, 2, 4)
        )
    monkeypatch.setattr(pairs.seat_agent, "_view", view)
    monkeypatch.setattr(pairs.seat_agent, "_world_request", submit)
    monkeypatch.setattr(pairs.deepseek, "turn", invoke)
    actor = pairs.DirectFlashSeat(tmp_path, run_id="s3101-r0-silent")
    actor.context_id = uuid4()
    context = AgentContext()
    with pytest.raises(pairs.seat_agent._Unmeasured) as failure:
        await actor.run("", _environment("inventory"), context)
    assert "private diagnostic" not in str(failure.value)
    with pytest.raises(pairs.seat_agent._Unmeasured, match="already started"):
        await actor.run("", _environment("inventory"), context)
    invoke.assert_called_once()
    if stage == "submission_unacknowledged":
        submit.assert_awaited_once()
    else:
        submit.assert_not_awaited()
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == ((None, None, None) if stage == "direct_unavailable" else (12, 2, 4))
    assert context.cost_usd is None
    assert context.metadata is not None
    assert context.metadata["model_turns"] == 1
    assert context.metadata["stage"] == stage
    expected_served = (
        "deepseek-flash"
        if stage in {"invalid_model_decisions", "submission_unacknowledged"}
        else None
    )
    assert context.metadata["served_model"] == expected_served
    assert context.metadata["view_sha256"] is None


@pytest.mark.parametrize(
    "change",
    [
        None,
        "oracle",
        "missing_turn",
        "missing_ack",
        "wrong_prompt",
        "wrong_submission",
        "wrong_seed",
        "wrong_reward",
    ],
)
async def test_inspection_requires_real_provenance_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str | None,
) -> None:
    cell = pairs.grid()[0]
    _, world, records = await _offline_world(monkeypatch, tmp_path, cell)
    config = pairs._trial_config(tmp_path.resolve(), cell)
    trial = Path(config["trials_dir"]) / config["trial_name"]
    artifact = trial / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True)
    score = pairs.evidence._score(world)
    reward = score["optimal"] / 4
    native = {
        "id": str(uuid4()),
        "task_name": "harbor/dispatch-handoff",
        "trial_name": config["trial_name"],
        "trial_uri": trial.as_uri(),
        "task_id": {"path": config["task"]["path"]},
        "finished_at": "2026-11-20T00:00:01Z",
        "exception_info": None,
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": reward}},
        "seat_results": list(records.values()),
        "config": TrialConfig.model_validate(config).model_dump(mode="json"),
    }
    if change == "oracle":
        records["inventory"]["agent_info"]["name"] = "oracle"
    elif change == "missing_turn":
        records["inventory"]["agent_result"]["metadata"]["model_turns"] = 0
    elif change == "missing_ack":
        del world["observation_receipts"]["inventory"]
    elif change == "wrong_prompt":
        metadata = records["dispatch"]["agent_result"]["metadata"]
        metadata["prompt_sha256"] = metadata["view_sha256"]
    elif change == "wrong_submission":
        records["inventory"]["agent_result"]["metadata"]["decision_sha256"] = "0" * 64
    elif change == "wrong_seed":
        world["settings"]["seed"] = 9999
    elif change == "wrong_reward":
        native["verifier_result"]["rewards"]["reward"] = 0.5 if reward != 0.5 else 0.0
    artifact.write_text(json.dumps(world))
    (trial / "result.json").write_text(json.dumps(native))
    row: dict[str, Any] = {"outcome_valid": False, "native_returncode": 0, "seats": {}}
    if change is not None:
        with pytest.raises(ValueError):
            pairs._inspect(trial, config, cell, row)
        assert row["outcome_valid"] is False
    else:
        pairs._inspect(trial, config, cell, row)
        assert row["trial_id"] == native["id"]
        assert row["native_score"] == reward
        assert row["outcome_valid"] is True
        assert all(action["choice"] == "A" for action in row["actions"])
        assert set(row["seats"]) == {"inventory", "dispatch"}


def test_cli_reserves_only_one_private_first_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path.resolve() / "private"
    harbor = tmp_path / "harbor"
    cell = pairs.grid()[0]
    preflight = Mock(return_value=(harbor, {"HARBOR_TELEMETRY": "0"}))
    launch = Mock(side_effect=OSError("private diagnostic"))
    monkeypatch.setattr(pairs, "_preflight", preflight)
    monkeypatch.setattr(pairs, "_launch", launch)
    args = [
        "run",
        "--out",
        str(root),
        "--harbor",
        str(harbor),
        "--cell",
        cell["cell_id"],
    ]
    assert pairs.main(args) == 1
    with pytest.raises(SystemExit) as retry:
        pairs.main(args)
    assert retry.value.code == 1
    preflight.assert_called_once_with(harbor)
    directory = root / "cells" / cell["cell_id"]
    launch.assert_called_once_with(directory, harbor, {"HARBOR_TELEMETRY": "0"})
    assert list((root / "cells").iterdir()) == [directory]
    assert all(
        S_IMODE(path.stat().st_mode) == 0o700
        for path in (root, directory.parent, directory)
    )
    attempt = pairs.evidence._read_json(directory / "attempt.json")
    scenario = pairs.evidence._read_json(
        directory / "dispatch-handoff" / pairs.run_panel.SCENARIO
    )
    receipt = pairs.evidence._read_json(directory / "receipt.json")
    assert attempt["reserved_model_turns"] == 2
    assert scenario == {
        "schema": "seatlab.scenario/v1",
        "seed": 3101,
        "condition": "fresh",
        "episodes": 4,
    }
    assert receipt["outcome_valid"] is False
    assert receipt["error"] == "native_launch_failed"
    assert receipt["seats"] == {"inventory": None, "dispatch": None}


@pytest.mark.parametrize("bindable", [True, False])
def test_native_host_keeps_paid_values_out_of_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bindable: bool
) -> None:
    secret = "dummy-not-a-credential"
    ledger = tmp_path / "private-ledger"
    balance = Decimal("2.00")
    if bindable:
        ledger.mkdir(mode=0o700)
        pairs.spend.initialize(ledger, balance, Decimal("1.00"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    monkeypatch.setenv("DOCKER_HOST", "unix:///unused.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)

    entry = tmp_path / "fake-harbor"
    marker = tmp_path / "seen.json"
    paid_names = ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")
    child_probe = f"import os; assert set({paid_names!r}).isdisjoint(os.environ)"
    entry.write_text(
        f"#!{pairs._host_python()}\n"
        "import json, os, subprocess, sys\n"
        "from decimal import Decimal\n"
        "from pathlib import Path\n"
        "if sys.argv[1:3] == ['trial', 'start']:\n"
        f"    Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"assert set({paid_names!r}).isdisjoint(os.environ)\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print({pairs.run_panel.NATIVE_VERSION!r})\n"
        "elif sys.argv[1:] == ['trial', 'schema']:\n"
        "    print(json.dumps({'properties': {'participants': {}}}))\n"
        "else:\n"
        "    from examples.seatlab.visibility import deepseek, spend\n"
        f"    assert deepseek._key() == {secret!r}\n"
        "    spend.reserve('native-probe', 'inventory', balance_usd=Decimal('2.00'))\n"
        f"    subprocess.run([sys.executable, '-c', {child_probe!r}], check=True)\n"
    )
    entry.chmod(0o700)

    harbor, env = pairs._preflight(entry)
    assert set(paid_names).isdisjoint(env)
    returncode = pairs._launch(tmp_path, harbor, env)
    log = (tmp_path / "native.log").read_text()
    assert secret not in log
    assert str(ledger) not in log
    if not bindable:
        assert returncode == 1
        assert "Native host spend binding failed" in log
        assert not marker.exists()
        return
    assert returncode == 0, log
    assert json.loads(marker.read_text()) == [
        "trial",
        "start",
        "-c",
        str(tmp_path / "config.json"),
    ]
    assert pairs.spend.report(ledger)["unsettled_calls"] == 1


def test_child_startup_trace_imports_native_symbols() -> None:
    env = {"PYTHONPATH": pairs._host_env({})["PYTHONPATH"]}
    completed = subprocess.run(
        [
            pairs._host_python(),
            "-c",
            "import examples.seatlab.visibility.native_pairs as runner\n"
            "assert runner.team_report._within_trial\n"
            "assert runner.DirectFlashSeat.name() == 'seatlab-direct-flash'\n"
            "print('startup-ok')\n",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "startup-ok"


def test_collect_leaves_missing_result_unresolved(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "private"
    cell = pairs.grid()[1]
    directory = root / "cells" / cell["cell_id"]
    directory.mkdir(parents=True)
    directory.chmod(0o700)
    root.chmod(0o700)
    directory.parent.chmod(0o700)
    receipt = pairs.collect_cell(root, cell)
    assert receipt["recovery"] == "unresolved"
    assert receipt["outcome_valid"] is False
    assert not (directory / "receipt.json").exists()
