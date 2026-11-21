import asyncio

import pytest

from examples.seatlab.briefing import channel


_TRIAL = "seatlab-team-000000000000000000000001"
_OTHER_TRIAL = "seatlab-team-000000000000000000000002"


async def _assert_closed(channel_id: str) -> None:
    for role in ("inventory", "dispatch"):
        with pytest.raises(RuntimeError, match="already closed"):
            async with channel.open_port(channel_id, role):
                pytest.fail("A closed relay admitted another seat")


async def _exchange(
    channel_id: str, brief: str | None, push: tuple[str, str], first_role: str
) -> None:
    entered = asyncio.Event()
    inventory_exited = asyncio.Event()

    async def inventory() -> None:
        if first_role == "dispatch":
            await entered.wait()
        async with channel.open_port(channel_id, "inventory") as port:
            entered.set()
            assert await port.receive("brief") == brief
            await port.send("push", push)
        inventory_exited.set()

    async def dispatch() -> None:
        if first_role == "inventory":
            await entered.wait()
        async with channel.open_port(channel_id, "dispatch") as port:
            entered.set()
            await port.send("brief", brief)
            await inventory_exited.wait()
            assert await port.receive("push") == push

    async with asyncio.TaskGroup() as seats:
        seats.create_task(inventory())
        seats.create_task(dispatch())
    await _assert_closed(channel_id)


async def test_concurrent_trials_keep_brief_and_masked_traffic_separate() -> None:
    async with asyncio.timeout(2), asyncio.TaskGroup() as trials:
        trials.create_task(_exchange(_TRIAL, "ABCDAB", ("Q3", "Q1"), "inventory"))
        trials.create_task(_exchange(_OTHER_TRIAL, None, ("Q2", "Q6"), "dispatch"))


async def test_only_registered_roles_and_one_ordered_exchange_can_send() -> None:
    with pytest.raises(ValueError, match="Invalid relay role"):
        async with channel.open_port(_TRIAL, "observer"):
            pytest.fail("The relay admitted an unsupported role")
    for invalid_id in (_TRIAL + "\n", _TRIAL[:-1] + "A", _TRIAL[:-1]):
        with pytest.raises(ValueError, match="Invalid relay channel ID"):
            async with channel.open_port(invalid_id, "inventory"):
                pytest.fail("The relay admitted an invalid trial ID")

    async with (
        asyncio.timeout(2),
        channel.open_port(_TRIAL, "inventory") as inventory,
        channel.open_port(_TRIAL, "dispatch") as dispatch,
    ):
        with pytest.raises(RuntimeError, match="already joined"):
            async with channel.open_port(_TRIAL, "dispatch"):
                pytest.fail("The relay admitted a second dispatch seat")
        with pytest.raises(RuntimeError, match="role sequence"):
            await inventory.send("brief", "ABCDAB")
        with pytest.raises(RuntimeError, match="role sequence"):
            await inventory.send("push", ("Q1", "Q2"))
        with pytest.raises(RuntimeError, match="role sequence"):
            await dispatch.receive("push")
        with pytest.raises(RuntimeError, match="role sequence"):
            await dispatch.receive("brief")
        for invalid_brief in ("ABCDEA", "abcdaa", "ABCD", ("Q1", "Q2")):
            with pytest.raises(ValueError, match="six A-D letters"):
                await dispatch.send("brief", invalid_brief)

        await dispatch.send("brief", "DDACBB")
        with pytest.raises(RuntimeError, match="role sequence"):
            await dispatch.send("brief", None)
        assert await inventory.receive("brief") == "DDACBB"
        with pytest.raises(RuntimeError, match="role sequence"):
            await inventory.receive("brief")
        for invalid_push in (None, ("Q1", "Q1"), ("Q1", "Q7"), ["Q1", "Q2"]):
            with pytest.raises(ValueError, match="distinct shipment aliases"):
                await inventory.send("push", invalid_push)
        await inventory.send("push", ("Q6", "Q2"))
        with pytest.raises(RuntimeError, match="role sequence"):
            await inventory.send("push", ("Q2", "Q6"))
        assert await dispatch.receive("push") == ("Q6", "Q2")
        with pytest.raises(RuntimeError, match="role sequence"):
            await dispatch.receive("push")
    with pytest.raises(RuntimeError, match="port closed"):
        await inventory.send("push", ("Q1", "Q2"))
    await _assert_closed(_TRIAL)


async def test_cancelled_inventory_releases_waiting_dispatch() -> None:
    received = asyncio.Event()

    async def inventory() -> None:
        async with channel.open_port(_TRIAL, "inventory") as port:
            assert await port.receive("brief") is None
            received.set()
            await asyncio.Event().wait()

    async def dispatch() -> None:
        async with channel.open_port(_TRIAL, "dispatch") as port:
            await port.send("brief", None)
            with pytest.raises(RuntimeError, match="exchange aborted"):
                await port.receive("push")

    async with asyncio.timeout(2), asyncio.TaskGroup() as seats:
        inventory_task = seats.create_task(inventory())
        seats.create_task(dispatch())
        await received.wait()
        inventory_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await inventory_task
    await _assert_closed(_TRIAL)


async def test_missing_dispatch_times_out_and_cannot_join_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channel, "WAIT_TIMEOUT_SECONDS", 0)
    with pytest.raises(TimeoutError):
        async with channel.open_port(_TRIAL, "inventory") as inventory:
            await inventory.receive("brief")
    await _assert_closed(_TRIAL)


def test_claim_limit_fails_closed_and_is_local_to_one_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channel, "_MAX_CLAIMS_PER_LOOP", 1)

    async def play() -> None:
        async with channel.open_port(_TRIAL, "inventory"):
            pass
        await _assert_closed(_TRIAL)
        with pytest.raises(RuntimeError, match="trial limit reached"):
            async with channel.open_port(_OTHER_TRIAL, "inventory"):
                pytest.fail("The relay admitted a trial beyond the claim limit")

    asyncio.run(play())
    asyncio.run(play())
