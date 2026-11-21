from hashlib import sha256
from zipfile import ZipFile

import pytest

from examples.seatlab.rolepack import import_source, protocol

_MODEL = "private-provider/private-model"
_PROMPT = "  Choose a seat for café.\r\n\n"
_SCALAR = {
    "field": "seat",
    "scalar": True,
    "items": [{"key": "seat", "valid": [0, 1], "optimal": [1]}],
}
_VECTOR = {
    "field": "moves",
    "scalar": False,
    "items": [
        {"key": "B", "valid": ["left", "right"], "optimal": ["right"]},
        {"key": "A", "valid": [1, 2], "optimal": [1]},
    ],
}


def _entry(sample_id, *, record_id="record", task="trusted-task", rubric=_SCALAR):
    return {
        "id": record_id,
        "task": task,
        "world": "trusted-world",
        "sample_id": sample_id,
        "rubric": rubric,
    }


def _index(*records):
    return {"schema": "seatlab.role-index/v1", "records": list(records)}


@pytest.fixture
def native(tmp_path):
    log = pytest.importorskip("inspect_ai.log")
    model = pytest.importorskip("inspect_ai.model")
    event = pytest.importorskip("inspect_ai.event")

    def sample(
        sample_id,
        reply=None,
        *,
        prompt=_PROMPT,
        attachments=None,
        sent=None,
        epoch=1,
        retries=None,
        failed=False,
        stop_reason="stop",
    ):
        output = (
            model.ModelOutput()
            if reply is None
            else model.ModelOutput.from_content(_MODEL, reply, stop_reason=stop_reason)
        )
        return log.EvalSample(
            id=sample_id,
            epoch=epoch,
            input=prompt,
            attachments=attachments or {},
            target="private target",
            output=output,
            scores={"private score": log.EvalSampleScore(value=1)},
            error=log.EvalError(
                message="private failure", traceback="", traceback_ansi=""
            )
            if failed
            else None,
            events=[
                event.ModelEvent(
                    model=_MODEL,
                    role="private role",
                    input=[
                        model.ChatMessageUser(content=prompt if sent is None else sent)
                    ],
                    tools=[],
                    tool_choice="none",
                    config=model.GenerateConfig(),
                    output=output,
                    retries=retries,
                )
            ]
            if reply is not None
            else [],
        )

    def archive(name, *samples):
        path = tmp_path / f"{name}.eval"
        log.write_eval_log(
            log.EvalLog(
                status="success",
                eval=log.EvalSpec(
                    created="2026-11-20T00:00:00Z",
                    task="private task",
                    dataset=log.EvalDataset(samples=len(samples)),
                    model=_MODEL,
                    config=log.EvalConfig(),
                ),
                samples=list(samples),
            ),
            path,
        )
        return path

    return sample, archive


def test_import_preserves_exact_turn_and_nonoptimal_action(native):
    sample, archive = native
    scalar_reply = ' \n {"seat" : 0}\t\r\n'
    vector_reply = ' {"moves": {"A": 2, "B": "left"}}\n'
    source = archive(
        "original",
        sample("original/seat#1", scalar_reply),
        sample(
            "vector",
            vector_reply,
            prompt="attachment://user-text",
            attachments={"user-text": _PROMPT},
        ),
        sample("ignored", "not JSON", epoch=2, prompt="unselected private prompt"),
    )
    scalar = _entry("original/seat#1", record_id="a", task="z")
    vector = _entry("vector", record_id="z", task="a", rubric=_VECTOR)
    scalar2 = _entry("second", record_id="b", task="z")
    second = archive("second", sample("second", scalar_reply))
    pack = import_source.build(_index(scalar2, scalar, vector), [second, source])
    assert protocol.validate(pack) == pack
    assert pack["schema"] == protocol.SCHEMA
    for entry, record, reply, path, answer in zip(
        (vector, scalar, scalar2),
        pack["records"],
        (vector_reply, scalar_reply, scalar_reply),
        (source, source, second),
        ({"moves": {"A": 2, "B": "left"}}, {"seat": 0}, {"seat": 0}),
        strict=True,
    ):
        assert record == {
            "id": entry["id"],
            "task": entry["task"],
            "world": entry["world"],
            "rubric": entry["rubric"],
            "prompt": _PROMPT,
            "prompt_sha256": sha256(_PROMPT.encode("utf-8")).hexdigest(),
            "peer": {
                "run": entry["sample_id"],
                "answer": answer,
                "source_sha256": sha256(path.read_bytes()).hexdigest(),
                "response_sha256": sha256(reply.encode("utf-8")).hexdigest(),
            },
        }


@pytest.mark.parametrize(
    ("reply", "options"),
    [
        (None, {}),
        ("malformed response", {"failed": True}),
        ('{"seat":1}', {"failed": True}),
        ('{"seat":1}', {"stop_reason": "max_tokens"}),
        ('{"seat":0,"seat":1}', {}),
        ('{"seat":2}', {}),
        ('{"seat":NaN}', {}),
        ('{"seat":0,"private":"not an action"}', {}),
    ],
)
def test_unusable_first_attempt_retains_the_original_prompt(native, reply, options):
    sample, archive = native
    source = archive("first", sample("selected", reply, **options))
    index = _index(_entry("selected"))
    [record] = import_source.build(index, [source])["records"]
    assert record["prompt"] == _PROMPT
    assert record["prompt_sha256"] == sha256(_PROMPT.encode("utf-8")).hexdigest()
    assert record["peer"] is None
    later = archive("later", sample("selected", '{"seat":1}'))
    with pytest.raises(ValueError, match="duplicate indexed Inspect sample"):
        import_source.build(index, [source, later])


def test_relogged_archive_member_cannot_replace_the_first_attempt(native):
    sample, archive = native
    source = archive("first", sample("selected"), sample("other", '{"seat":0}'))
    later = archive("later", sample("selected", '{"seat":1}'))
    with ZipFile(later) as replacement:
        name = next(
            name for name in replacement.namelist() if name.startswith("samples/")
        )
        contents = replacement.read(name)
    with ZipFile(source, "a") as original:
        with pytest.warns(UserWarning, match="Duplicate name"):
            original.writestr(name, contents)
    with pytest.raises(ValueError, match="duplicate indexed Inspect sample"):
        import_source.build(_index(_entry("selected")), [source])
    [record] = import_source.build(_index(_entry("other")), [source])["records"]
    assert record["peer"]["answer"] == {"seat": 0}


@pytest.mark.parametrize("options", [{"epoch": 2}, {"retries": 1}, {"sent": "changed"}])
def test_invalid_first_attempt_provenance_is_rejected(native, options):
    sample, archive = native
    source = archive("first", sample("selected", '{"seat":0}', **options))
    with pytest.raises(ValueError, match="first-attempt"):
        import_source.build(_index(_entry("selected")), [source])


def test_missing_sample_is_not_replaced_by_an_unselected_one(native):
    sample, archive = native
    source = archive("unselected", sample("other", '{"seat":0}'))
    with pytest.raises(ValueError, match="absent"):
        import_source.build(_index(_entry("selected")), [source])


def test_index_rejects_duplicate_ids_and_untrusted_fields():
    entry = _entry("selected")
    with pytest.raises(ValueError, match="duplicate record ID"):
        import_source.build(_index(entry, _entry("another-sample")), [])
    with pytest.raises(ValueError, match="duplicate sample ID"):
        import_source.build(
            _index(entry, _entry("selected", record_id="another-id")), []
        )
    with pytest.raises(ValueError, match="invalid index record fields"):
        import_source.build(_index({**entry, "answer": {"seat": 1}}), [])
    with pytest.raises(ValueError, match="absent"):
        import_source.build(_index(entry), [])
