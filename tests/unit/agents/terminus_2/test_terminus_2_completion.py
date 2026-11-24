import json
from unittest.mock import AsyncMock

import pytest

from harbor.agents.factory import AgentFactory
from harbor.agents.terminus_2.terminus_2 import Terminus2
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.cli.utils import parse_kwargs
from harbor.llms.base import BaseLLM, LLMResponse
from harbor.models.agent.context import AgentContext
from harbor.models.metric import UsageInfo
from harbor.models.trajectories import FinalMetrics, Metrics, Trajectory
from harbor.models.trial.config import AgentConfig


@pytest.mark.parametrize(
    ("agent_kwargs", "expected_turns"),
    [([], 2), (["confirm_task_complete=false"], 1)],
    ids=["confirmation-by-default", "single-completion"],
)
async def test_terminus_2_completion(
    tmp_path, monkeypatch, mock_environment, agent_kwargs, expected_turns
):
    command = "echo done\n"
    terminal_output = "done"
    usage = UsageInfo(
        prompt_tokens=32, completion_tokens=8, cache_tokens=4, cost_usd=0.125
    )
    llm = AsyncMock(spec=BaseLLM)
    llm.call.side_effect = [
        LLMResponse(
            content=json.dumps(
                {
                    "analysis": "Ready to finish",
                    "plan": "Report completion",
                    "commands": [{"keystrokes": command, "duration": 1}],
                    "task_complete": True,
                }
            ),
            usage=usage,
        ),
        LLMResponse(
            content='{"analysis":"Done","plan":"Confirm","commands":[],"task_complete":true}',
            usage=usage,
        ),
    ]
    monkeypatch.setattr(Terminus2, "_init_llm", lambda self, **kwargs: llm)
    config = AgentConfig(
        name="terminus-2",
        model_name="openai/gpt-4o",
        kwargs={"enable_summarize": False, **parse_kwargs(agent_kwargs)},
    )
    agent = AgentFactory.create_agent_from_config(config, logs_dir=tmp_path)
    assert isinstance(agent, Terminus2)
    session = AsyncMock(spec=TmuxSession)
    session.is_session_alive.return_value = True
    session.get_incremental_output.side_effect = ["$", terminal_output, ""]
    agent._session = session
    context = AgentContext()

    await agent.run("Print done", mock_environment, context)

    assert llm.call.await_count == expected_turns
    session.send_keys.assert_awaited_once_with(command, block=False, min_timeout_sec=1)
    trajectory = Trajectory.model_validate_json(
        (tmp_path / "trajectory.json").read_text()
    )
    initial_step, first_completion, *confirmation = trajectory.steps
    assert initial_step.source == "user"
    assert [call.function_name for call in first_completion.tool_calls or []] == [
        "bash_command",
        "mark_task_complete",
    ]
    assert first_completion.observation is not None
    observation = first_completion.observation.results[0].content
    if expected_turns == 2:
        assert observation is not None
        assert "Are you sure you want to mark the task as complete?" in observation
        assert llm.call.await_args_list[1].kwargs["prompt"] == observation
        [confirmed] = confirmation
        assert [call.function_name for call in confirmed.tool_calls or []] == [
            "mark_task_complete"
        ]
    else:
        assert observation == terminal_output
        assert not confirmation

    assert first_completion.metrics == Metrics(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cache_tokens,
        cost_usd=usage.cost_usd,
    )
    prompt_tokens = usage.prompt_tokens * expected_turns
    completion_tokens = usage.completion_tokens * expected_turns
    cached_tokens = usage.cache_tokens * expected_turns
    cost_usd = usage.cost_usd * expected_turns
    assert context.n_input_tokens == prompt_tokens
    assert context.n_output_tokens == completion_tokens
    assert context.n_cache_tokens == cached_tokens
    assert context.cost_usd == cost_usd
    assert trajectory.final_metrics == FinalMetrics(
        total_prompt_tokens=prompt_tokens,
        total_completion_tokens=completion_tokens,
        total_cached_tokens=cached_tokens,
        total_cost_usd=cost_usd,
    )
