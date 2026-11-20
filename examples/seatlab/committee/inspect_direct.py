"""Run the registered Inspect cohort with the existing metered first-party transport."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import override

from inspect_ai import eval as inspect_eval
from inspect_ai.model import (
    ChatMessage,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    get_model,
    modelapi,
)
from inspect_ai.tool import ToolChoice, ToolInfo

from .. import inspect_replay
from ..visibility import deepseek, spend

_PROVIDER = "seatlab-direct"


@modelapi(name=_PROVIDER)
class DirectSeat(ModelAPI):
    """Accept only a single, exact voter view from the active SeatLab sample."""

    _stopped = False

    @override
    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        if self._stopped:
            raise RuntimeError("a previous direct turn stopped the cohort")
        bound = inspect_replay.active_cell()
        if bound is None:
            self._stopped = True
            raise ValueError("no active SeatLab sample")
        cell, expected = bound
        if (
            self.model_name != deepseek.MODEL
            or tools
            or tool_choice not in ("auto", "none")
            or len(input) != 2
            or not isinstance(input[0], ChatMessageSystem)
            or input[0].content != deepseek._SYSTEM
            or not isinstance(input[1], ChatMessageUser)
            or input[1].content != expected
            or config.temperature != 0.7
            or config.max_tokens != 512
            or config.num_choices not in (None, 1)
            or config.fallback_models
        ):
            self._stopped = True
            raise ValueError("generation does not match the registered seat turn")
        try:
            turn = await asyncio.to_thread(
                deepseek.turn, expected, run_id=cell.cell_id, role=cell.role
            )
        except Exception:
            self._stopped = True
            raise
        result = ModelOutput.from_content(turn.served_model, turn.text)
        result.usage = ModelUsage(
            input_tokens=turn.input_tokens,
            input_tokens_cache_read=turn.cache_tokens,
            output_tokens=turn.output_tokens,
            total_tokens=turn.input_tokens + turn.output_tokens,
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-dir",
        type=Path,
        required=True,
        help="new private directory outside any repository",
    )
    args = parser.parse_args()
    log_dir = args.log_dir.expanduser().resolve()
    if any(os.path.lexists(path / ".git") for path in (log_dir, *log_dir.parents)):
        parser.error("Inspect logs cannot be written in a repository")
    ledger = os.environ.get("SEATLAB_SPEND_DIR")
    if not ledger or not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("a direct key and existing SeatLab budget are required")
    deepseek.bind_host_key()
    spend.bind_host_spend_dir()
    if spend.report(Path(ledger), run_prefix="ix-")["calls"]:
        parser.error("this registered cohort already started; do not retry")
    deepseek.preflight()
    log_dir.mkdir(mode=0o700, exist_ok=False)
    task = inspect_replay.seatlab_facts()
    config = GenerateConfig(
        temperature=0.7,
        max_tokens=512,
        max_retries=0,
        cache=False,
        fallback_models=[],
        max_connections=1,
    )
    model = get_model(f"{_PROVIDER}/{deepseek.MODEL}", config=config, memoize=False)
    logs = inspect_eval(
        task,
        model=model,
        sandbox=None,
        epochs=1,
        max_samples=1,
        max_tasks=1,
        sample_shuffle=False,
        retry_on_error=0,
        fail_on_error=True,
        continue_on_fail=False,
        task_retry_attempts=0,
        score_on_error=False,
        log_samples=True,
        log_model_api=False,
        log_format="eval",
        log_dir=str(log_dir),
        log_realtime=False,
        log_shared=False,
        trace=False,
        notification=False,
        ctl_server=False,
        acp_server=False,
        display="none",
    )
    if len(logs) != 1:
        raise RuntimeError("expected one Inspect evaluation")
    log = logs[0]
    samples = len(log.samples) if log.samples is not None else 0
    print(json.dumps({"status": log.status, "samples": samples}))
    return 0 if log.status == "success" and samples == len(task.dataset) else 1


if __name__ == "__main__":
    raise SystemExit(main())
