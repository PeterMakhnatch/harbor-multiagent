import json
import sys

import pytest

from examples.seatlab.workbench.__main__ import main


@pytest.fixture
def order(capfd, tmp_path):
    source = ["--example", "--draws", "2"]
    spool = tmp_path / "spool"
    spool.mkdir(mode=0o700)

    def run(mode, *args, code=0):
        actual = main(["order", mode, *source, *args])
        output = capfd.readouterr()
        assert actual == code, output.err
        return output.out

    plan = json.loads(run("prepare"))
    pin = ("--panel-sha256", plan["panel_sha256"])
    return run, plan, pin, ("--spool", str(spool))


def requests(output):
    return [json.loads(line) for line in output.splitlines()]


def message_bytes(request):
    return len(
        json.dumps(
            request["messages"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    )


def test_claim_checks_whole_panel_before_reserving_an_affordable_shard(order):
    run, plan, pin, spool = order
    panel = [
        request
        for wave in range(4)
        for request in requests(run("emit", *pin, "--wave", str(wave)))
    ]
    assert (
        plan["worlds"],
        plan["unique_shipments"],
        plan["different_recommendations"],
    ) == (2, 24, 10)
    assert len(panel) == plan["expected_first_replies"] == 8
    assert len({request["id"] for request in panel}) == 8
    panel_bytes = sum(map(message_bytes, panel))
    assert plan["canonical_message_bytes"]["panel"] == panel_bytes

    shard = ("--wave", "0", "--shard", "0/2")
    (preview,) = requests(run("emit", *pin, *shard))
    assert message_bytes(preview) <= panel_bytes - 1
    for max_requests, max_bytes in ((7, panel_bytes), (8, panel_bytes - 1)):
        assert (
            run(
                "claim",
                *pin,
                *spool,
                *shard,
                "--max-requests",
                str(max_requests),
                "--max-input-bytes",
                str(max_bytes),
                code=1,
            )
            == ""
        )
        report = json.loads(run("status", *pin, *spool, code=3))
        assert report["counts"]["unreserved_missing"] == 8

    (claimed,) = requests(
        run(
            "claim",
            *pin,
            *spool,
            *shard,
            "--max-requests",
            "8",
            "--max-input-bytes",
            str(panel_bytes),
        )
    )
    assert claimed["id"] == preview["id"]


class InterruptedOutput:
    @property
    def buffer(self):
        return self

    def write(self, data):
        raise BrokenPipeError("worker handoff interrupted")


def test_interrupted_claim_stays_reserved_and_orphan_reply_stays_invalid(
    order, tmp_path, capfd, monkeypatch
):
    run, _, pin, spool = order
    wave = ("--wave", "0")
    own_shard = ("--shard", "0/2")
    (reserved,) = requests(run("emit", *pin, *wave, *own_shard))
    (orphan,) = requests(run("emit", *pin, *wave, "--shard", "1/2"))
    claim = (*pin, *spool, *wave, *own_shard, "--max-requests", "8")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", InterruptedOutput())
        assert main(["order", "claim", "--example", "--draws", "2", *claim]) == 2
    capfd.readouterr()

    assert run("claim", *claim) == ""
    report = json.loads(run("status", *pin, *spool, code=3))
    assert report["counts"]["reserved_missing"] == 1
    reply_file = tmp_path / "first-reply.jsonl"
    reply_file.write_text(
        json.dumps(
            {
                "id": orphan["id"],
                "prompt_sha256": orphan["prompt_sha256"],
                "reply": "not a JSON reply",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    run("score", *pin, str(reply_file), code=3)
    assert run("claim", *claim) == ""
    report = json.loads(run("status", *pin, *spool, str(reply_file), code=1))
    assert report["counts"] == {
        "reserved_missing": 1,
        "reserved_valid": 0,
        "reserved_invalid": 0,
        "unreserved_missing": 6,
        "unreserved_valid": 0,
        "unreserved_invalid": 1,
    }
    states = {row["id"]: (row["reserved"], row["reply"]) for row in report["subjects"]}
    assert states[reserved["id"]] == (True, "missing")
    assert states[orphan["id"]] == (False, "invalid")
