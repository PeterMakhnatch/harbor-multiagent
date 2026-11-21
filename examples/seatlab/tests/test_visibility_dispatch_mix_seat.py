import json
import sys
from pathlib import Path
from runpy import run_path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_ROOT = Path(__file__).resolve().parents[3]
with patch.object(
    sys, "path", [str(_ROOT / "examples/seatlab"), str(_ROOT), *sys.path]
):
    from examples.seatlab import claude_replay, seat_agent
    from examples.seatlab.visibility import dispatch_mix_plan as plan
    from examples.seatlab.visibility import dispatch_mix_seat as mix
    from examples.seatlab.visibility import native_pairs as pairs


def _cell(pairing: str) -> plan.Cell:
    return next(
        cell
        for cell in plan.grid()
        if cell["seed"] == 4001
        and cell["repetition"] == 0
        and cell["condition"] == "reordered"
        and cell["pairing"] == pairing
    )


def _environment(role: str) -> SeatEnvironment:
    seat = Mock(spec=SeatEnvironment)
    seat.service = role
    return cast(SeatEnvironment, seat)


def _answer(user: str) -> dict[str, list[dict[str, Any]]]:
    body = json.loads(user.split("\n", 1)[1])
    if body["role"] == "inventory":
        return {"rows": list(reversed(body["cases"]))}
    return {"rows": [{"id": case["id"], "bay": "A"} for case in body["cases"]]}


class TestMixedSeat(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logs_dir = Path(self.enterContext(TemporaryDirectory()))
        self.world_request = self.enterContext(
            patch.object(seat_agent, "_world_request")
        )
        self.cli = self.enterContext(
            patch.object(claude_replay, "_invoke", side_effect=self._claude)
        )
        self.enterContext(
            patch.object(claude_replay, "_managed_configuration", return_value=False)
        )
        self.api = self.enterContext(
            patch.object(pairs.deepseek, "_request", side_effect=self._deepseek)
        )
        self.ledger = Mock()
        self.enterContext(
            patch.object(pairs.deepseek, "_budget", return_value=self.ledger)
        )
        self.inputs: dict[tuple[str, str], tuple[str, str]] = {}
        self.auth = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "max",
        }
        self.claude_model = "claude-sonnet-5-5"
        self.claude_session_ok = True
        self.direct_model = "DeepSeek-V4.1-Flash-1017"
        self.balance = "2.00"

    def _capture(self, model: str, system: str, user: str) -> str:
        role = json.loads(user.split("\n", 1)[1])["role"]
        self.assertNotIn((model, role), self.inputs)
        self.inputs[model, role] = (system, user)
        return json.dumps(_answer(user))

    def _claude(
        self, args: list[str], body: bytes | None, timeout: int
    ) -> CompletedProcess[bytes]:
        if args == ["auth", "status", "--json"]:
            self.assertIsNone(body)
            response = self.auth
        else:
            assert body is not None
            session = args[args.index("--session-id") + 1]
            system = args[args.index("--system-prompt") + 1]
            self.assertEqual(args[args.index("--max-turns") + 1], "1")
            response = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": session if self.claude_session_ok else "wrong-session",
                "num_turns": 1,
                "permission_denials": [],
                "duration_ms": 100,
                "duration_api_ms": 90,
                "total_cost_usd": 0.012,
                "modelUsage": {
                    self.claude_model: {
                        "inputTokens": 10,
                        "cacheReadInputTokens": 2,
                        "cacheCreationInputTokens": 3,
                        "outputTokens": 7,
                    }
                },
                "result": self._capture(plan.CLAUDE, system, body.decode()),
            }
        return CompletedProcess(args, 0, json.dumps(response).encode(), b"")

    def _deepseek(self, path: str, body: bytes | None, timeout: int) -> dict[str, Any]:
        if path == "/user/balance":
            self.assertIsNone(body)
            return {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": self.balance,
                        "granted_balance": "0.00",
                        "topped_up_balance": self.balance,
                    }
                ],
            }
        self.assertEqual(path, "/chat/completions")
        assert body is not None
        system, user = json.loads(body)["messages"]
        self.assertEqual((system["role"], user["role"]), ("system", "user"))
        text = self._capture(plan.DIRECT, system["content"], user["content"])
        return {
            "model": self.direct_model,
            "usage": {
                "prompt_cache_hit_tokens": 5,
                "prompt_cache_miss_tokens": 7,
                "completion_tokens": 8,
                "prompt_tokens": 12,
                "total_tokens": 20,
            },
            "choices": [{"message": {"content": text}}],
        }

    def _world(
        self, cell: plan.Cell, *, episodes: int = 6, condition: str | None = None
    ) -> Any:
        path = pairs.run_panel.TASK_DIR / "environment/seatworld.py"
        world = run_path(str(path))["World"](
            {
                "schema": "seatlab.scenario/v1",
                "seed": cell["seed"],
                "episodes": episodes,
                "condition": condition or cell["condition"],
            }
        )

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
            self.assertEqual(path, "/submit")
            self.assertEqual(view_sha256, pairs.view_hash(world.view(role)))
            _, user = self.inputs[plan.model_for(cell, role), role]
            self.assertEqual(payload, _answer(user))
            return 200, json.dumps(world.submit(role, payload))

        self.world_request.side_effect = request
        return world

    async def test_both_rosters_commit_the_same_scoped_input_and_native_usage(
        self,
    ) -> None:
        worlds = []
        for pairing in ("cd", "dc"):
            cell = _cell(pairing)
            world = self._world(cell)
            for role in plan.ROLES:
                model = plan.model_for(cell, role)
                agent = mix.MixedSeat(self.logs_dir, model, run_id=cell["cell_id"])
                agent_info = agent.to_agent_info()
                self.assertEqual(agent_info.name, "seatlab-mixed-model")
                self.assertEqual(agent_info.version, "1.0.0")
                assert agent_info.model_info is not None
                self.assertEqual(
                    (agent_info.model_info.provider, agent_info.model_info.name),
                    tuple(model.split("/", 1)),
                )
                context = AgentContext()
                view = world.view(role)
                await agent.run("private task instruction", _environment(role), context)
                system, user = self.inputs[model, role]
                self.assertEqual(system, "Answer only with JSON.")
                self.assertEqual(user, pairs._prompt(role, view["cases"]))
                self.assertEqual(
                    json.loads(user.split("\n", 1)[1]).keys(), {"role", "cases"}
                )
                self.assertNotIn("private task instruction", user)
                metadata = context.metadata
                assert metadata is not None
                self.assertEqual(metadata["schema"], pairs._SEAT_SCHEMA)
                self.assertEqual(metadata["run_id"], cell["cell_id"])
                self.assertEqual(metadata["role"], role)
                self.assertEqual(metadata["requested_model"], model.split("/", 1)[1])
                served = (
                    self.claude_model if model == plan.CLAUDE else self.direct_model
                )
                self.assertEqual(metadata["served_model"], served)
                self.assertEqual(
                    (metadata["stage"], metadata["model_turns"]), ("complete", 1)
                )
                self.assertEqual(metadata["view_sha256"], pairs.view_hash(view))
                self.assertEqual(metadata["prompt_sha256"], pairs._prompt_hash(user))
                self.assertEqual(
                    metadata["decision_sha256"],
                    pairs._decision_hash(role, _answer(user)["rows"]),
                )
                usage = (
                    context.n_input_tokens,
                    context.n_cache_tokens,
                    context.n_output_tokens,
                )
                if model == plan.CLAUDE:
                    self.assertEqual(usage, (15, 2, 7))
                    self.assertEqual(metadata["telemetry"]["estimated_cost_usd"], 0.012)
                else:
                    self.assertEqual(usage, (12, 5, 8))
                    self.assertIsNone(context.cost_usd)
                    self.assertNotIn("telemetry", metadata)
            worlds.append(world.evidence())
        for role in plan.ROLES:
            self.assertEqual(
                self.inputs[plan.CLAUDE, role], self.inputs[plan.DIRECT, role]
            )
        self.assertEqual(worlds[0], worlds[1])
        self.assertEqual(self.cli.call_count, 4)
        self.assertEqual(self.api.call_count, 4)

    async def test_wrong_role_or_roster_never_gets_a_view_or_starts_a_turn(
        self,
    ) -> None:
        for role, model, stage in (
            ("world", plan.CLAUDE, "invalid_seat"),
            ("inventory", plan.DIRECT, "invalid_roster"),
            ("dispatch", plan.CLAUDE, "invalid_roster"),
        ):
            with self.subTest(role=role, model=model):
                agent = mix.MixedSeat(
                    self.logs_dir, model, run_id=_cell("cd")["cell_id"]
                )
                context = AgentContext()
                with self.assertRaisesRegex(seat_agent._Unmeasured, stage):
                    await agent.run("", _environment(role), context)
                assert context.metadata is not None
                self.assertEqual(context.metadata["model_turns"], 0)
        self.world_request.assert_not_awaited()
        self.cli.assert_not_called()
        self.api.assert_not_called()

    async def test_wrong_case_count_or_condition_never_starts_a_turn(self) -> None:
        cell = _cell("cd")
        for override in ({"episodes": 5}, {"condition": "fresh"}):
            with self.subTest(override=override):
                self._world(cell, **override)
                self.world_request.reset_mock()
                env = _environment("inventory")
                actor = mix.MixedSeat(
                    self.logs_dir, plan.CLAUDE, run_id=cell["cell_id"]
                )
                with self.assertRaisesRegex(seat_agent._Unmeasured, "invalid_view"):
                    await actor.run("", env, AgentContext())
                self.world_request.assert_awaited_once_with(env, "/view")
        self.cli.assert_not_called()
        self.api.assert_not_called()

    async def test_account_or_identity_failure_never_submits_or_uses_fallback(
        self,
    ) -> None:
        for model, override, stage, call_count in (
            (
                plan.CLAUDE,
                {"auth": {**self.auth, "subscriptionType": "pro"}},
                "route_unverified",
                1,
            ),
            (plan.CLAUDE, {"claude_model": "unregistered"}, "model_unattested", 2),
            (plan.CLAUDE, {"claude_session_ok": False}, "session_unattested", 2),
            (plan.DIRECT, {"balance": "1.00"}, "balance_below_minimum", 1),
            (
                plan.DIRECT,
                {"direct_model": "unregistered"},
                "served_model_unverified",
                2,
            ),
        ):
            with self.subTest(stage=stage), patch.multiple(self, **override):
                self.world_request.reset_mock()
                self.cli.reset_mock()
                self.api.reset_mock()
                self.inputs.clear()
                cell = _cell("cd" if model == plan.CLAUDE else "dc")
                self._world(cell)
                env = _environment("inventory")
                context = AgentContext()
                actor = mix.MixedSeat(self.logs_dir, model, run_id=cell["cell_id"])
                with self.assertRaisesRegex(seat_agent._Unmeasured, f"{stage}$"):
                    await actor.run("", env, context)
                with self.assertRaisesRegex(seat_agent._Unmeasured, "already started"):
                    await actor.run("", env, context)
                self.world_request.assert_awaited_once_with(env, "/view")
                self.assertEqual(
                    (self.cli.call_count, self.api.call_count),
                    (call_count, 0) if model == plan.CLAUDE else (0, call_count),
                )
                assert context.metadata is not None
                self.assertIsNone(context.metadata["view_sha256"])
                self.assertIsNone(context.metadata["decision_sha256"])
                self.assertIsNone(context.metadata["served_model"])

    def test_constructor_rejects_unknown_cells_routes_and_overrides(self) -> None:
        valid = {"run_id": _cell("cd")["cell_id"], "model_name": plan.CLAUDE}
        for change in (
            {"run_id": "s3101-r0-fresh"},
            {"model_name": "claude-sonnet-5-5"},
            {"model_name": None},
            {"extra_env": {"ANTHROPIC_API_KEY": "dummy"}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                mix.MixedSeat(self.logs_dir, **{**valid, **change})
        with self.assertRaises(ValueError):
            mix.MixedSeat(self.logs_dir, **valid, base_url="invalid")
