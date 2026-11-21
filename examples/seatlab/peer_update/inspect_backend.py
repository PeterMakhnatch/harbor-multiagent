from __future__ import annotations

import asyncio
import json
import os
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..visibility.seat_command import (
    CommandSeat,
    _configured_seat,
    _reject_constant,
    _unique_object,
)

if TYPE_CHECKING:
    from inspect_ai.dataset import Dataset

_CONFIG_ENV = "SEATLAB_REVIEW_COMMAND"
_MODEL = "seatlab-command/dispatch"
_RUN_CONFIG: ContextVar[Path | None] = ContextVar(
    "peer_update_run_config", default=None
)


def _load_dispatch(path: Path) -> CommandSeat:
    if not path.is_absolute():
        raise ValueError(f"{_CONFIG_ENV} must name an absolute config path")
    try:
        doc = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, ValueError, RecursionError):
        raise ValueError("invalid dispatch command config") from None
    if not isinstance(doc, dict) or set(doc) != {"dispatch"}:
        raise ValueError("command config must contain only dispatch")
    spec = doc["dispatch"]
    names = spec.get("env") if isinstance(spec, dict) else None
    if isinstance(names, list) and any(
        isinstance(name, str) and name.upper().endswith("API_KEY") for name in names
    ):
        raise ValueError("dispatch command cannot use an API key")
    return _configured_seat(spec)


@cache
def _register_provider() -> None:
    from inspect_ai.model import (
        ChatMessage,
        ChatMessageUser,
        GenerateConfig,
        ModelAPI,
        ModelOutput,
        modelapi,
    )
    from inspect_ai.tool import ToolChoice, ToolInfo

    @modelapi(name="seatlab-command")
    class DispatchAPI(ModelAPI):
        def __init__(
            self,
            model_name: str,
            *,
            config: GenerateConfig,
            base_url: str | None = None,
            api_key: str | None = None,
        ) -> None:
            if model_name != "dispatch" or base_url is not None or api_key is not None:
                raise ValueError("seatlab-command supports only keyless dispatch")
            super().__init__(model_name=model_name, config=config)
            path = _RUN_CONFIG.get() or Path(os.environ.get(_CONFIG_ENV, ""))
            self._seat = _load_dispatch(path)

        async def generate(
            self,
            input: list[ChatMessage],
            tools: list[ToolInfo],
            tool_choice: ToolChoice,
            config: GenerateConfig,
        ) -> ModelOutput:
            if tools or tool_choice != "none":
                raise ValueError("dispatch does not accept tools")
            if (
                len(input) != 1
                or not isinstance(input[0], ChatMessageUser)
                or not isinstance(input[0].content, str)
                or input[0].tool_call_id is not None
            ):
                raise ValueError("dispatch requires one plain-text user message")
            text = await asyncio.to_thread(self._seat, input[0].content)
            return ModelOutput.from_content(_MODEL, text)


def _load_dataset(path: Path, limit_ids: list[str] | None) -> Dataset:
    from inspect_ai.dataset import Sample, json_dataset

    seen: set[str] = set()

    def sample(record: dict[str, Any]) -> Sample:
        sample_id, text = record.get("id"), record.get("input")
        if not isinstance(sample_id, str) or not sample_id or not isinstance(text, str):
            raise ValueError("dataset records require a string ID and plain-text input")
        if sample_id in seen:
            raise ValueError("duplicate dataset sample ID")
        seen.add(sample_id)
        return Sample(id=sample_id, input=text, metadata=record.get("metadata"))

    dataset = json_dataset(str(path.absolute()), sample_fields=sample, auto_id=False)
    if limit_ids is not None:
        if not limit_ids or any(not isinstance(item, str) for item in limit_ids):
            raise ValueError("sample selection requires at least one string ID")
        allowed = set(limit_ids)
        if len(allowed) != len(limit_ids):
            raise ValueError("duplicate requested sample ID")
        if not allowed <= seen:
            raise ValueError("unknown requested sample ID")
        dataset = dataset.filter(lambda row: row.id in allowed)
    if not len(dataset):
        raise ValueError("no Inspect samples to run")
    return dataset


def _claim_log_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if next(path.iterdir(), None) is not None:
        raise FileExistsError("Inspect log directory is not empty")
    (path / ".inspect-claimed").touch(mode=0o600, exist_ok=False)


def run_inspect(
    dataset: Path, config: Path, log_dir: Path, limit_ids: list[str] | None = None
) -> Path:
    from inspect_ai import Task, eval as inspect_eval
    from inspect_ai.model import GenerateConfig, get_model
    from inspect_ai.solver import generate

    samples = _load_dataset(dataset, limit_ids)
    _register_provider()
    token = _RUN_CONFIG.set(config)
    try:
        model = get_model(
            _MODEL,
            config=GenerateConfig(cache=False, max_retries=0, max_connections=1),
            memoize=False,
        )
    finally:
        _RUN_CONFIG.reset(token)

    log_dir = log_dir.absolute()
    _claim_log_dir(log_dir)
    logs = inspect_eval(
        Task(dataset=samples, solver=generate(tool_calls="none"), checkpoint=False),
        model=model,
        epochs=1,
        sample_shuffle=False,
        max_samples=1,
        retry_on_error=0,
        task_retry_attempts=0,
        fail_on_error=False,
        debug_errors=False,
        score=False,
        display="none",
        acp_server=False,
        ctl_server=False,
        log_dir=str(log_dir),
        log_format="eval",
        log_buffer=1,
        log_samples=True,
        log_model_api=False,
        log_shared=False,
    )
    if len(logs) != 1 or not logs[0].location:
        raise RuntimeError("Inspect did not return one log location")
    return Path(logs[0].location)
