import asyncio
import json
import sys
from pathlib import Path

import pytest

from examples.seatlab.peer_update.inspect_backend import (
    _load_dispatch,
    _register_provider,
    run_inspect,
)


@pytest.fixture
def dispatch_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    pytest.importorskip("inspect_ai")
    dataset = tmp_path / "samples.json"
    dataset.write_text(
        json.dumps(
            [
                {"id": "source:solo-r2", "input": "  first é\n"},
                {"id": "source:draft-r1", "input": "unselected"},
                {"id": "source:solo-r1", "input": "\nlast\t"},
            ]
        ),
        encoding="utf-8",
    )
    attempts = tmp_path / "attempts.jsonl"
    program = (
        "import json, sys\n"
        "with open(sys.argv[1], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps(json.load(sys.stdin)['prompt']) + '\\n')\n"
        'print(\'{"text":""}\')\n'
        "raise SystemExit(3)\n"
    )
    config = tmp_path / "dispatch.json"
    config.write_text(
        json.dumps(
            {
                "dispatch": {
                    "argv": [sys.executable, "-c", program, str(attempts)],
                    "env": [],
                }
            }
        ),
        encoding="utf-8",
    )
    return dataset, config, attempts


@pytest.mark.parametrize(
    ("ids", "error"),
    [
        ([], "at least one string ID"),
        (["source:solo-r1", "source:solo-r1"], "duplicate requested sample ID"),
        (["source:solo-r1", "other:solo-r1"], "unknown requested sample ID"),
    ],
)
def test_invalid_selection_does_not_start_dispatch(
    tmp_path: Path, dispatch_run: tuple[Path, Path, Path], ids: list[str], error: str
) -> None:
    dataset, config, attempts = dispatch_run
    log_dir = tmp_path / "logs"
    with pytest.raises(ValueError, match=error):
        run_inspect(dataset, config, log_dir, ids)
    assert not attempts.exists()
    assert not log_dir.exists()


def test_duplicate_dataset_id_is_rejected_even_if_not_selected(
    tmp_path: Path, dispatch_run: tuple[Path, Path, Path]
) -> None:
    dataset, config, attempts = dispatch_run
    records = json.loads(dataset.read_text(encoding="utf-8"))
    records.append(records[1])
    dataset.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate dataset sample ID"):
        run_inspect(dataset, config, tmp_path / "logs", ["source:solo-r1"])
    assert not attempts.exists()


@pytest.mark.parametrize(
    ("contents", "error"),
    [
        ('{"dispatch": {}, "reviewer": {}}', "contain only dispatch"),
        ('{"dispatch": {}, "dispatch": {}}', "invalid dispatch command config"),
        (
            '{"dispatch": {"argv": [], "env": ["OPENAI_API_KEY"]}}',
            "cannot use an API key",
        ),
    ],
)
def test_dispatch_config_rejects_extra_roles_duplicate_keys_and_api_keys(
    tmp_path: Path, contents: str, error: str
) -> None:
    path = tmp_path / "dispatch.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        _load_dispatch(path)


def test_registered_dispatch_rejects_tools_and_history_without_running(
    dispatch_run: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from inspect_ai.model import (
        ChatMessageSystem,
        ChatMessageUser,
        GenerateConfig,
        get_model,
    )

    _, config, attempts = dispatch_run
    monkeypatch.setenv("SEATLAB_REVIEW_COMMAND", str(config))
    _register_provider()
    api = get_model("seatlab-command/dispatch", memoize=False).api
    user = ChatMessageUser(content="dispatch")
    with pytest.raises(ValueError, match="does not accept tools"):
        asyncio.run(api.generate([user], [], "any", GenerateConfig()))
    with pytest.raises(ValueError, match="requires one plain-text user message"):
        asyncio.run(
            api.generate(
                [ChatMessageSystem(content="history"), user],
                [],
                "none",
                GenerateConfig(),
            )
        )
    assert not attempts.exists()


def test_failed_dispatches_remain_in_log_and_cannot_run_again(
    tmp_path: Path, dispatch_run: tuple[Path, Path, Path]
) -> None:
    from inspect_ai.log import read_eval_log

    dataset, config, attempts = dispatch_run
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    ids = ["source:solo-r1", "source:solo-r2"]
    log_path = run_inspect(dataset, config, log_dir, ids)
    original_log = log_path.read_bytes()
    samples = read_eval_log(log_path).samples
    assert samples is not None
    assert sorted(sample.id for sample in samples) == ids
    assert all(sample.epoch == 1 for sample in samples)
    assert all(
        sample.error and "seat command failed" in sample.error.message
        for sample in samples
    )
    with pytest.raises(FileExistsError, match="Inspect log directory is not empty"):
        run_inspect(dataset, config, log_dir, ids)
    assert log_path.read_bytes() == original_log
    assert [
        json.loads(line) for line in attempts.read_text(encoding="utf-8").splitlines()
    ] == ["  first é\n", "\nlast\t"]
