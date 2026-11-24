import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from examples.seat_handoffs.attempt_keeper import HandoffAttemptKeeper
from examples.seatlab.workbench import instrument
from examples.seatlab.workbench.account_keeper import AccountKeeper
from examples.seatlab.workbench.attempt_keeper import AttemptKeeper
from examples.seatlab.workbench.attention import load_spool
from examples.seatlab.workbench.input_keeper import InputStartKeeper
from examples.seatlab.workbench.private_receipt import _linkable
from harbor.cli.job_plugins import (
    PluginConfig,
    attach_job_plugins,
    plugin_configs_from_cli,
)
from harbor.models.job.config import JobConfig
from harbor.models.job.plugin import JobPlugin
from harbor.models.trial.config import Participant
from harbor.trial.hooks import TrialEvent

if TYPE_CHECKING:
    from harbor.job import Job

type Preset = dict[type[JobPlugin], tuple[str, str]]

pytestmark = pytest.mark.skipif(not _linkable(), reason="linkat unsupported")

FLAGS = ("--plugin", "--plugin-kwarg")
JOB_BINDING = "expected_job_dir"
DEFAULT: Preset = {
    InputStartKeeper: ("archive_dir", "starts"),
    AttemptKeeper: ("archive_dir", "seats"),
    AccountKeeper: ("account_dir", "accounts"),
}
WITH_HANDOFFS: Preset = {**DEFAULT, HandoffAttemptKeeper: ("archive_dir", "handoffs")}
STARTED_BY = ("AttemptKeeper", "InputStartKeeper")
ENDED_BY = (AccountKeeper, HandoffAttemptKeeper)
SHARED = "runs a=b c"


class Host:
    __slots__ = ("config", "id", "job_dir", "observers", "routes")

    def __init__(self, job_dir: Path) -> None:
        self.job_dir, self.id = job_dir, uuid4()
        self.config = JobConfig(
            job_name=job_dir.name,
            jobs_dir=job_dir.parent,
            participants=[
                Participant(seat="booker", name="oracle"),
                Participant(seat="reporter", name="nop"),
            ],
        )
        self.routes = {TrialEvent.START: [], TrialEvent.END: []}
        self.observers = []

    def on_trial_started(self, callback):
        self.routes[TrialEvent.START].append(callback)
        return self

    def on_trial_ended(self, callback):
        self.routes[TrialEvent.END].append(callback)
        return self

    def observe_trial_attempts(self, factory):
        if self.observers:
            raise RuntimeError("A trial attempt observer is already installed.")
        self.observers.append(factory)
        return self


async def attach(host: Host, configs: list[PluginConfig]) -> list[JobPlugin]:
    return await attach_job_plugins(cast("Job", host), configs)


def native_configs(bundle: Path) -> list[PluginConfig]:
    tokens = json.loads((bundle / "flags.json").read_bytes())
    assert type(tokens) is list and len(tokens) % 2 == 0
    picked: dict[str, list[str]] = {flag: [] for flag in FLAGS}
    for flag, value in zip(tokens[::2], tokens[1::2], strict=True):
        assert flag in FLAGS and type(value) is str and value == value.strip()
        picked[flag].append(value)
    configs = plugin_configs_from_cli(picked["--plugin"], picked["--plugin-kwarg"])
    assert all(":" in config.import_path for config in configs)
    return configs


def enroll(worker: Path, *, handoffs: bool) -> tuple[Path, Path]:
    worker.mkdir(parents=True)
    job, bundle = worker / "job", worker / "bundle"
    instrument.prepare(job, bundle, handoffs=handoffs)
    assert not job.exists() and not job.is_symlink()
    return job, bundle


def archive_targets(configs: list[PluginConfig]) -> set[str]:
    return {
        value
        for config in configs
        for key, value in config.kwargs.items()
        if key != JOB_BINDING
    }


def receipts(*bundles: Path) -> list[str]:
    return [
        entry
        for bundle in bundles
        for kept in os.listdir(bundle)
        if (bundle / kept).is_dir()
        for entry in os.listdir(bundle / kept)
    ]


async def attach_own(job: Path, bundle: Path, expected: Preset) -> set[str]:
    configs = native_configs(bundle)
    job.mkdir()
    host = Host(job)
    plugins = await attach(host, configs)
    assert sorted(type(p).__name__ for p in plugins) == sorted(
        cls.__name__ for cls in expected
    )
    (entry,), _ = load_spool(str(bundle / "spool.json"), None)
    assert entry["job"] == str(job)
    for config, plugin in zip(configs, plugins, strict=True):
        param, name = expected[type(plugin)]
        enrolled = {param: str(bundle / name)} | (
            {JOB_BINDING: str(job)} if type(plugin) is InputStartKeeper else {}
        )
        assert config.kwargs == enrolled
        assert entry[name] == enrolled[param]
    attempt = next(p for p in plugins if type(p) is AttemptKeeper)
    assert [observer.__self__ for observer in host.observers] == [attempt]
    started = sorted(type(cb.__self__).__name__ for cb in host.routes[TrialEvent.START])
    ended = sorted(type(cb.__self__).__name__ for cb in host.routes[TrialEvent.END])
    assert started == list(STARTED_BY)
    assert ended == sorted(cls.__name__ for cls in ENDED_BY if cls in expected)
    assert os.listdir(job) == [] and receipts(bundle) == []
    targets = archive_targets(configs)
    assert len(targets) == len(expected)
    return targets


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "preset", [DEFAULT, WITH_HANDOFFS], ids=["default", "handoffs"]
)
async def test_native_preset_attaches_enrolled_job_exactly(
    tmp_path: Path, preset: Preset
):
    job, bundle = enroll(
        tmp_path / SHARED / "worker = a", handoffs=preset is WITH_HANDOFFS
    )
    await attach_own(job, bundle, preset)
    assert (bundle / "handoffs").exists() is (preset is WITH_HANDOFFS)


@pytest.mark.asyncio
async def test_worker_flags_refuse_another_workers_job_before_observing(tmp_path: Path):
    job_a, bundle_a = enroll(tmp_path / SHARED / "worker a", handoffs=True)
    job_b, bundle_b = enroll(tmp_path / SHARED / "worker b", handoffs=True)
    job_a.mkdir()
    job_b.mkdir()
    host = Host(job_b)
    with pytest.raises(ValueError):
        await attach(host, native_configs(bundle_a))
    assert host.observers == []
    assert host.routes == {TrialEvent.START: [], TrialEvent.END: []}
    assert os.listdir(job_a) == os.listdir(job_b) == []
    assert receipts(bundle_a, bundle_b) == []


@pytest.mark.asyncio
async def test_sibling_bundles_keep_independent_archives(tmp_path: Path):
    left = enroll(tmp_path / SHARED / "left=a b", handoffs=False)
    right = enroll(tmp_path / SHARED / "right=a b", handoffs=True)
    kept_left = await attach_own(*left, DEFAULT)
    kept_right = await attach_own(*right, WITH_HANDOFFS)
    assert kept_left.isdisjoint(kept_right)
