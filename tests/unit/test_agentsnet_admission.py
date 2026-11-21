import asyncio
import importlib.util
import json
import random
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from enum import Enum, auto
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import httpx
import pytest

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.models.agent.context import AgentContext

SEEDS = (
    103,
    137,
    149,
    157,
    173,
    191,
    223,
    241,
    257,
    283,
    307,
    331,
    353,
    379,
    401,
    421,
    449,
    467,
    491,
    521,
    541,
    563,
    587,
    607,
    631,
    653,
    677,
    701,
    727,
    751,
    773,
    797,
)
SEATS = 4
MAX_PROBES = 8
OUTPUT_LIMIT = 512
RESERVE = 10_444_800
NANODOLLARS_PER_USD = 1_000_000_000


@pytest.fixture
def task_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "examples/tasks/agentsnet-coloring/agentsnet_agent.py"
    )
    spec = importlib.util.spec_from_file_location("_agentsnet_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.setitem(
        sys.modules, "agentsnet_protocol", ModuleType("agentsnet_protocol")
    )
    spec.loader.exec_module(module)
    return module


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 23, 0, 59, tzinfo=UTC)

    def now(self, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return self.current.replace(tzinfo=None)
        return self.current.astimezone(tz)

    def tick(self, seconds: int = 1) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass(frozen=True)
class _Reply:
    miss: int
    hit: int
    output: int
    text: str | None = "reply"
    reason: str = "stop"
    partial: bool = False

    @property
    def usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.miss + self.hit,
            "prompt_cache_hit_tokens": self.hit,
            "prompt_cache_miss_tokens": self.miss,
            "completion_tokens": self.output,
        }

    @property
    def peak_cost(self) -> int:
        return 300 * self.miss + 6 * self.hit + 1200 * self.output

    def body(self) -> bytes:
        return json.dumps(
            {
                "model": "deepseek-flash",
                "choices": [
                    {"message": {"content": self.text}, "finish_reason": self.reason}
                ],
                "usage": self.usage,
            }
        ).encode()


class _Terminal(Enum):
    KNOWN = auto()
    UNKNOWN = auto()


class _Failure(Enum):
    QUERY_REJECTED = auto()


@dataclass(eq=False)
class _Probe:
    seat: int
    index: int
    reply: _Reply
    balance_ready: asyncio.Event = field(default_factory=asyncio.Event)
    body_ready: asyncio.Event = field(default_factory=asyncio.Event)
    amount: str | None = None
    started: datetime | None = None
    terminal: _Terminal | None = None
    task: asyncio.Task[str | _Failure] = field(init=False)

    @property
    def position(self) -> tuple[int, int]:
        return self.index // 2 + 1, self.index % 2 + 1

    @property
    def actual_cost(self) -> int:
        assert self.started is not None
        assert self.started.weekday() == 0 and self.started.hour in (0, 1)
        return self.reply.peak_cost // (2 if self.started.hour == 0 else 1)


class _ReplyStream(httpx.AsyncByteStream):
    def __init__(self, probe: _Probe, active: set[_Probe]) -> None:
        self.probe = probe
        self.active = active

    async def __aiter__(self) -> AsyncIterator[bytes]:
        await self.probe.body_ready.wait()
        yield self.probe.reply.body()
        if self.probe.reply.partial:
            raise httpx.ReadError("injected missing end of body")

    async def aclose(self) -> None:
        self.active.remove(self.probe)


class _Simulation:
    def __init__(self, module: ModuleType, path: Path, lease: int, seed: int) -> None:
        self.module = module
        self.path = path
        self.lease = lease
        self.seed = seed
        self.rng = random.Random(seed)
        self.clock = _Clock()
        self.current: ContextVar[_Probe] = ContextVar("current_probe")
        self.probes: list[_Probe] = []
        self.admitted: list[_Probe] = []
        self.started: dict[_Probe, tuple[int, int]] = {}
        self.settled: set[_Probe] = set()
        self.balance_calls: set[_Probe] = set()
        self.balances: set[_Probe] = set()
        self.post_calls: set[_Probe] = set()
        self.posts: set[_Probe] = set()

    def observe_transitions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        model_type = self.module._SeatModel
        original_reserve = model_type._reserve
        original_settle = model_type._settle

        @wraps(original_reserve)
        def reserve(
            model: Any,
            message_bytes: int,
            reserve: int,
            round_number: int,
            attempt: int,
        ) -> tuple[datetime, dict[str, Any]]:
            result = original_reserve(
                model, message_bytes, reserve, round_number, attempt
            )
            started, call = result
            probe = self.current.get()
            assert self.admission.lock.locked() and self.models[probe.seat] is model
            assert probe not in self.started and probe.started == started
            assert (round_number, attempt) == probe.position
            assert reserve == RESERVE
            key = (call["trial_call"], call["seat_call"])
            assert key[0] == len(self.started) + 1
            assert key[1] == 1 + sum(p.seat == probe.seat for p in self.started)
            self.started[probe] = key
            self.check_accounting()
            return result

        @wraps(original_settle)
        def settle(model: Any, call: dict[str, Any], reserve: int, usage: Any) -> None:
            probe = self.current.get()
            assert self.admission.lock.locked() and self.models[probe.seat] is model
            assert probe not in self.settled and probe.terminal is not None
            assert self.started[probe] == (call["trial_call"], call["seat_call"])
            assert reserve == RESERVE
            assert (usage is None) == (probe.terminal is _Terminal.UNKNOWN)
            original_settle(model, call, reserve, usage)
            self.settled.add(probe)
            self.check_accounting()

        monkeypatch.setattr(model_type, "_reserve", reserve)
        monkeypatch.setattr(model_type, "_settle", settle)

    async def run(self, scenario: Callable[["_Simulation"], Awaitable[None]]) -> None:
        self.admission = self.module._trial_admission(UUID(int=self.seed), self.lease)
        async with httpx.AsyncClient(
            base_url="https://simulated.invalid",
            transport=httpx.MockTransport(self.handle),
        ) as client:
            self.models = []
            for seat in range(SEATS):
                logs = self.path / f"seat-{seat}"
                logs.mkdir(parents=True)
                admission = self.module._trial_admission(
                    UUID(int=self.seed), self.lease
                )
                assert admission is self.admission
                self.models.append(
                    self.module._SeatModel(
                        client, "fake-key", admission, logs, OUTPUT_LIMIT
                    )
                )
            async with asyncio.TaskGroup() as group:
                self.group = group
                self.check()
                await scenario(self)
                assert all(probe.task.done() for probe in self.probes)
                assert not self.posts and not self.balances
        await self.checkpoint()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        probe = self.current.get()
        assert not self.admission.lock.locked()
        match request.method, request.url.path:
            case "GET", "/user/balance":
                assert probe not in self.balance_calls
                self.balance_calls.add(probe)
                self.balances.add(probe)
                try:
                    await probe.balance_ready.wait()
                finally:
                    self.balances.remove(probe)
                return httpx.Response(
                    200,
                    json={
                        "is_available": True,
                        "balance_infos": [
                            {"currency": "USD", "total_balance": probe.amount}
                        ],
                    },
                )
            case "POST", "/chat/completions":
                assert probe in self.balance_calls and probe.balance_ready.is_set()
                assert probe in self.started and probe not in self.post_calls
                assert len(self.posts) < SEATS
                assert all(active.seat != probe.seat for active in self.posts)
                pending = self.admission.outstanding_reserve_nanodollars
                assert pending >= (len(self.posts) + 1) * RESERVE
                assert pending + self.admission.budgeted_cost_nanodollars <= self.lease
                assert (
                    self.models[probe.seat].account.outstanding_reserve_nanodollars
                    == RESERVE
                )
                self.post_calls.add(probe)
                self.posts.add(probe)
                return httpx.Response(200, stream=_ReplyStream(probe, self.posts))
            case _:
                raise AssertionError(f"Unexpected fake transport request: {request}")

    def launch(self, seat: int, reply: _Reply) -> _Probe:
        assert 0 <= seat < SEATS
        assert len(self.probes) < MAX_PROBES
        assert sum(not probe.task.done() for probe in self.probes) < SEATS
        self.clock.tick()
        probe = _Probe(seat, len(self.probes), reply)
        self.probes.append(probe)
        probe.task = self.group.create_task(self.query(probe))
        return probe

    async def query(self, probe: _Probe) -> str | _Failure:
        token = self.current.set(probe)
        try:
            return await self.models[probe.seat].query("simulate", *probe.position)
        except NonZeroAgentExitCodeError:
            return _Failure.QUERY_REJECTED
        finally:
            self.current.reset(token)

    async def checkpoint(self) -> None:
        loop = asyncio.get_running_loop()
        for _ in range(4):
            turn = loop.create_future()
            loop.call_soon(turn.set_result, None)
            await turn
        self.check()

    async def balance(
        self, probe: _Probe, *, allow: bool, amount: str = "1.50"
    ) -> None:
        assert probe in self.balances
        assert not probe.balance_ready.is_set()
        self.clock.tick()
        probe.amount = amount
        if allow:
            assert probe.started is None
            probe.started = self.clock.current
            self.admitted.append(probe)
        probe.balance_ready.set()
        await self.checkpoint()
        if not allow:
            self.assert_refused(probe)

    def assert_refused(self, probe: _Probe) -> None:
        assert probe.task.done() and probe.task.result() is _Failure.QUERY_REJECTED
        assert probe not in self.post_calls

    async def reject(self, probe: _Probe) -> None:
        await self.checkpoint()
        if probe in self.balances:
            await self.balance(probe, allow=False)
        self.assert_refused(probe)

    async def finish(self, probe: _Probe) -> None:
        assert probe in self.posts and probe.terminal is None
        self.clock.tick()
        probe.terminal = _Terminal.UNKNOWN if probe.reply.partial else _Terminal.KNOWN
        probe.body_ready.set()
        await self.checkpoint()
        assert probe.task.done()
        if probe.reply.partial or probe.reply.text is None:
            assert probe.task.result() is _Failure.QUERY_REJECTED
        else:
            assert probe.task.result() == probe.reply.text

    async def cancel(self, *probes: _Probe) -> None:
        assert 1 <= len(probes) <= SEATS
        self.clock.tick()
        for probe in probes:
            assert probe in self.balances or probe in self.posts
            if probe in self.posts:
                assert probe.terminal is None
                probe.terminal = _Terminal.UNKNOWN
            probe.task.cancel()
        await self.checkpoint()
        assert all(probe.task.cancelled() for probe in probes)
        assert not self.admission.lock.locked()

    def check(self) -> None:
        assert not self.admission.lock.locked()
        assert set(self.started) == set(self.admitted)
        assert self.settled == {p for p in self.admitted if p.terminal is not None}
        assert self.post_calls == set(self.admitted)
        assert self.posts == {p for p in self.admitted if p.terminal is None}
        assert self.balances <= {p for p in self.probes if not p.task.done()}
        assert len(self.posts) <= SEATS
        assert len({p.seat for p in self.posts}) == len(self.posts)
        self.check_accounting()
        for seat in range(SEATS):
            calls = [(i, p) for i, p in enumerate(self.admitted, 1) if p.seat == seat]
            assert all(self.started[p][0] == i for i, p in calls)
            self.check_trace(seat, calls)

    def check_accounting(self) -> None:
        live = set(self.started) - self.settled
        assert self.admission.live_calls == len(live) <= SEATS
        assert len({p.seat for p in live}) == len(live)
        known = {p for p in self.settled if p.terminal is _Terminal.KNOWN}
        settled = sum(p.reply.peak_cost for p in known)
        pending = RESERVE * (len(self.started) - len(known))
        assert self.admission.started_calls == len(self.started)
        assert self.admission.budgeted_cost_nanodollars == settled
        assert self.admission.outstanding_reserve_nanodollars == pending
        assert 0 <= settled + pending <= self.lease
        for seat in range(SEATS):
            self.check_account(seat, [p for p in self.started if p.seat == seat])

    def check_account(self, seat: int, probes: list[_Probe]) -> None:
        account = self.models[seat].account
        known = [
            p for p in probes if p in self.settled and p.terminal is _Terminal.KNOWN
        ]
        active = sum(p not in self.settled for p in probes)
        unknown = len(probes) - len(known) - active
        assert active + unknown <= 1
        assert account.started == len(probes)
        assert account.observed == len(known)
        expected_peak = sum(p.reply.peak_cost for p in known)
        expected_cost = sum(p.actual_cost for p in known)
        assert account.budgeted_cost_nanodollars == expected_peak
        assert account.cost_nanodollars == expected_cost
        assert account.outstanding_reserve_nanodollars == (active + unknown) * RESERVE
        context = AgentContext()
        account.publish(context)
        assert context.metadata is not None
        assert context.metadata["started_calls"] == len(probes)
        assert context.metadata["observed_usage_calls"] == len(known)
        assert context.metadata["observed_model_cost_nanodollars"] == expected_cost
        assert context.metadata["budgeted_model_cost_nanodollars"] == expected_peak
        if not active:
            assert context.metadata["unknown_usage_calls"] == unknown
            assert context.metadata["unknown_usage_reserve_nanodollars"] == (
                unknown * RESERVE
            )
        native_usage = (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
            context.cost_usd,
        )
        if not known:
            assert native_usage == (None, None, None, None)
            assert context.model_usage is None
            return
        expected_usage = (
            sum(p.reply.miss + p.reply.hit for p in known),
            sum(p.reply.hit for p in known),
            sum(p.reply.output for p in known),
            expected_cost / NANODOLLARS_PER_USD,
        )
        assert native_usage == expected_usage
        assert context.model_usage is not None
        assert set(context.model_usage) == {"deepseek-flash"}
        usage = context.model_usage["deepseek-flash"]
        assert (
            usage.n_input_tokens,
            usage.n_cache_tokens,
            usage.n_output_tokens,
            usage.cost_usd,
        ) == expected_usage

    def check_trace(self, seat: int, calls: list[tuple[int, _Probe]]) -> None:
        path = self.path / f"seat-{seat}" / "deepseek.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        assert len(lines) <= 2 * MAX_PROBES
        starts = {}
        finishes = {}
        for line in lines:
            record = json.loads(line)
            if record["event"] not in ("call_started", "call_finished"):
                continue
            target = starts if record["event"] == "call_started" else finishes
            key = (record["trial_call"], record["seat_call"])
            assert key not in target
            target[key] = record
        expected = {(trial, i): p for i, (trial, p) in enumerate(calls, 1)}
        assert starts.keys() == expected.keys()
        assert finishes.keys() == {
            key for key, p in expected.items() if p.terminal is not None
        }
        for key, probe in expected.items():
            start = starts[key]
            assert (start["round"], start["attempt"]) == probe.position
            assert start["reserve_usd"] == RESERVE / NANODOLLARS_PER_USD
            if probe.terminal is None:
                continue
            finish = finishes[key]
            assert (finish["round"], finish["attempt"]) == probe.position
            if probe.terminal is _Terminal.KNOWN:
                assert finish["usage"] == probe.reply.usage
                assert finish["cost_usd"] == probe.actual_cost / NANODOLLARS_PER_USD
                assert (
                    finish["budgeted_model_cost_nanodollars"] == probe.reply.peak_cost
                )
                assert finish["unknown_usage_reserve_usd"] == 0
            else:
                assert finish["cost_usd"] is None
                assert finish["budgeted_model_cost_nanodollars"] is None
                assert (
                    finish["unknown_usage_reserve_usd"] == RESERVE / NANODOLLARS_PER_USD
                )
                if probe.reply.partial:
                    assert (
                        json.loads(finish["response_body"])["usage"]
                        == probe.reply.usage
                    )

    def reply(
        self,
        *,
        max_miss: int = 32_000,
        output: int | None = None,
        text: str | None = "reply",
        reason: str = "stop",
        partial: bool = False,
    ) -> _Reply:
        return _Reply(
            miss=self.rng.randint(1, max_miss),
            hit=self.rng.randint(1, 768),
            output=self.rng.randint(1, OUTPUT_LIMIT) if output is None else output,
            text=text,
            reason=reason,
            partial=partial,
        )

    async def batch(self, *replies: _Reply) -> list[_Probe]:
        assert len(replies) == SEATS
        seats = self.rng.sample(range(SEATS), SEATS)
        probes = [
            self.launch(seat, reply) for seat, reply in zip(seats, replies, strict=True)
        ]
        await self.checkpoint()
        assert self.balances == set(probes)
        return probes

    async def open_four(self, probes: list[_Probe]) -> None:
        assert len(probes) == SEATS
        for index, probe in enumerate(probes):
            if index == 2:
                self.clock.tick(60)
            await self.balance(probe, allow=True)
        assert len(self.posts) == SEATS


async def _clean(sim: _Simulation) -> None:
    zero, length, invalid, normal = await sim.batch(
        _Reply(0, 0, 0),
        sim.reply(output=OUTPUT_LIMIT, reason="length"),
        sim.reply(text=None),
        sim.reply(),
    )
    first = [zero, length, invalid]
    sim.rng.shuffle(first)
    await sim.open_four([*first, normal])
    await sim.finish(normal)
    followup = sim.launch(normal.seat, sim.reply())
    await sim.checkpoint()
    await sim.balance(followup, allow=True)
    remaining = [zero, length, invalid, followup]
    sim.rng.shuffle(remaining)
    for probe in remaining:
        await sim.finish(probe)


async def _stop_with_partial_usage(sim: _Simulation) -> None:
    partial, *known = await sim.batch(
        sim.reply(partial=True), sim.reply(), sim.reply(), sim.reply()
    )
    order = [partial, *known]
    sim.rng.shuffle(order)
    await sim.open_four(order)
    sim.rng.shuffle(known)
    freed = known.pop()
    await sim.finish(freed)
    racing = sim.launch(freed.seat, sim.reply())
    await sim.checkpoint()
    assert racing in sim.balances
    await sim.finish(partial)
    assert sim.admission.stopped is not None
    await sim.balance(racing, allow=False)
    await sim.reject(sim.launch(freed.seat, sim.reply()))
    for probe in known:
        await sim.finish(probe)


async def _cancel_all_dispatched(sim: _Simulation) -> None:
    if sim.seed % 4 == 1:
        prior = sim.launch(sim.rng.randrange(SEATS), sim.reply())
        await sim.checkpoint()
        await sim.balance(prior, allow=True)
        await sim.finish(prior)
    probes = await sim.batch(*(sim.reply() for _ in range(SEATS)))
    sim.rng.shuffle(probes)
    await sim.open_four(probes)
    sim.rng.shuffle(probes)
    await sim.cancel(*probes)
    assert sim.admission.stopped is not None


async def _tight_cap(sim: _Simulation) -> None:
    probes = await sim.batch(*(sim.reply(max_miss=4_096) for _ in range(SEATS)))
    first, second, rejected, other = sim.rng.sample(probes, SEATS)
    await sim.balance(first, allow=True)
    await sim.balance(second, allow=True)
    await sim.balance(rejected, allow=False)
    await sim.balance(other, allow=False)
    await sim.finish(second)
    await sim.reject(sim.launch(rejected.seat, sim.reply()))
    await sim.finish(first)
    next_call = sim.launch(rejected.seat, sim.reply())
    await sim.checkpoint()
    await sim.balance(next_call, allow=True)
    await sim.finish(next_call)


async def _cancel_before_reserve(sim: _Simulation) -> None:
    seat = sim.rng.randrange(SEATS)
    canceled = sim.launch(seat, sim.reply())
    await sim.checkpoint()
    assert canceled in sim.balances
    await sim.cancel(canceled)
    assert not sim.post_calls and not sim.balances


async def _pre_dispatch(sim: _Simulation) -> None:
    seat, other = sim.rng.sample(range(SEATS), 2)
    admitted = sim.launch(seat, sim.reply())
    await sim.checkpoint()
    await sim.balance(admitted, allow=True)
    await sim.reject(sim.launch(seat, sim.reply()))
    await sim.finish(admitted)
    low_balance = sim.launch(other, sim.reply())
    await sim.checkpoint()
    await sim.balance(low_balance, allow=False, amount="1.49")


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    ("scenario", "lease"),
    [
        (_clean, 5 * RESERVE),
        (_stop_with_partial_usage, 5 * RESERVE),
        (_cancel_all_dispatched, 5 * RESERVE),
        (_tight_cap, 2 * RESERVE),
        (_cancel_before_reserve, 4 * RESERVE),
        (_pre_dispatch, 4 * RESERVE),
    ],
    ids=("clean", "partial", "cancel", "tight-cap", "cancel-balance", "pre-dispatch"),
)
async def test_agentsnet_admission_simulation(
    task_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
    scenario: Callable[[_Simulation], Awaitable[None]],
    lease: int,
) -> None:
    simulation = _Simulation(task_module, tmp_path, lease, seed)
    monkeypatch.setattr(task_module, "datetime", simulation.clock)
    simulation.observe_transitions(monkeypatch)
    await simulation.run(scenario)
