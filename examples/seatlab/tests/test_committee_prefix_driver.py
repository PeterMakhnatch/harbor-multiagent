import hashlib
import io
import json
import sys

from examples.seatlab.committee import (
    contract,
    prefix_driver,
    prefix_schedule,
    schedule,
)


def test_external_views_match_native_inputs_without_exposing_the_cut(capsys):
    receipts = json.loads(
        prefix_schedule.PACKET.with_name("committee-prefix-controls.json").read_bytes()
    )
    for source in receipts["sources"]:
        idx = source["map_index"]
        for role, digest in zip(
            schedule.ROLES, source["original_masked_prompt_sha256"], strict=True
        ):
            args = ["prompt", "--map", str(idx), "--role", role, "--arm"]
            assert prefix_driver.main([*args, "masked"]) == 0
            masked = capsys.readouterr().out
            assert hashlib.sha256(masked.encode()).hexdigest() == digest
            assert prefix_driver.main([*args, "shown"]) == 0
            shown = capsys.readouterr().out
            if role == schedule.chair(idx, 0):
                assert masked == shown
            else:
                changes = [
                    (a, b)
                    for a, b in zip(
                        masked.splitlines(), shown.splitlines(), strict=True
                    )
                    if a != b
                ]
                assert len(changes) == 1
                hidden, visible = changes[0]
                assert hidden == "Recommendation: null"
                assert json.loads(visible.removeprefix("Recommendation: ")).keys() == {
                    "choice",
                    "cited",
                }


def test_external_decode_accepts_wrong_visible_ballot_but_not_native_answer_keys(
    monkeypatch, capsys
):
    options, facts = contract._mapping(2)
    wrong = {"choice": options["Option_B"], "cited": [facts["F_SHARED_1"]]}
    cmd = ["decode", "--role", "analyst_4", "--map", "2"]

    def send(ballot):
        monkeypatch.setattr(
            sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(ballot).encode()))
        )
        return prefix_driver.main(cmd)

    assert send(wrong) == 0
    assert json.loads(capsys.readouterr().out) == wrong
    assert send({**wrong, "cited": ["F_SHARED_1"]}) == 1
    assert capsys.readouterr().out == ""
