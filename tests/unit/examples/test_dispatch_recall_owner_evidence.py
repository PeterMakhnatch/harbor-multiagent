import copy
import json
import re
import tempfile
from pathlib import Path

import pytest

from examples.seatlab.dispatch_recall.native import Session
from examples.seatlab.dispatch_recall.owner_evidence import owner_evidence
from tests.unit.examples.test_dispatch_recall_participant import (
    CLOSE_TICKET,
    COMMIT_TICKET,
    SEED,
    owners,
    play,
)


def transcript(timing: str, transition: str = "move"):
    with tempfile.TemporaryDirectory(prefix="de-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        session = Session(
            SEED, transition, timing, tickets=(COMMIT_TICKET, CLOSE_TICKET)
        )
        texts, _, _, _, contexts, cut, failure = play(root, session, owners(False))
    assert failure is None
    return cut, texts, {role: ctx.metadata for role, ctx in contexts.items()}


@pytest.fixture(scope="module")
def runs():
    return {timing: transcript(timing) for timing in ("immediate", "deferred")}


def prefix(cut, count):
    session = Session(SEED, cut["transition"], cut["timing"])
    for record in cut["actions"][:count]:
        view = session.view(record["role"])
        request = {key: value for key, value in record.items() if key != "role"}
        if record["op"] in ("commit", "close"):
            request["ticket"] = view["ticket"]
        session.apply(record["role"], request)
    return session.snapshot()


def statuses(evidence):
    return [(row["turn"], row["status"]) for row in evidence["turns"]]


def test_adapter_transcript_confirms_every_turn_without_leaking(runs):
    for timing, notices in (("immediate", "bay"), ("deferred", "blank")):
        cut, texts, metadata = runs[timing]
        before = copy.deepcopy(cut)
        evidence = owner_evidence(cut, texts, metadata)
        assert cut == before
        assert statuses(evidence) == [
            (turn, "confirmed") for turn in ("stage", "report", "commit", "close")
        ]
        assert evidence["counts"] == {"confirmed": 4, "unconfirmed": 0}
        assert evidence["roles"] == {
            "loader": {"metadata": "present", "logged": 3, "accepted": 3},
            "depot": {"metadata": "present", "logged": 1, "accepted": 1},
        }
        assert evidence["commit_request"] == {"status": "confirmed", "notice": notices}
        assert evidence["close_request"] == {"status": "confirmed", "notice": "bay"}
        shown = json.dumps(evidence)
        for secret in (*cut["bays"], COMMIT_TICKET, CLOSE_TICKET, texts["loader"]):
            assert secret not in shown
        assert "reward" not in shown and re.search(r"[0-9a-f]{64}", shown) is None


def test_missing_or_short_logs_stay_unconfirmed(runs):
    cut, texts, metadata = runs["immediate"]
    absent = owner_evidence(cut, texts, {**metadata, "loader": None})
    assert statuses(absent) == [
        ("stage", "unconfirmed"),
        ("report", "confirmed"),
        ("commit", "unconfirmed"),
        ("close", "unconfirmed"),
    ]
    assert absent["roles"]["loader"] == {
        "metadata": "absent",
        "logged": 0,
        "accepted": 3,
    }
    loader = metadata["loader"]
    short = {
        **loader,
        "accepted_actions": loader["accepted_actions"][:1],
        "accepted_requests": loader["accepted_requests"][:1],
        "phase": "commit",
    }
    lost_ack = owner_evidence(prefix(cut, 3), texts, {**metadata, "loader": short})
    assert statuses(lost_ack) == [
        ("stage", "confirmed"),
        ("report", "confirmed"),
        ("commit", "unconfirmed"),
    ]
    assert lost_ack["commit_request"] == {"status": "unconfirmed", "notice": "bay"}
    assert lost_ack["close_request"] == {"status": "not_accepted", "notice": None}
    early = owner_evidence(prefix(cut, 1), texts, {"loader": short, "depot": None})
    assert statuses(early) == [("stage", "confirmed")]


def test_contradictory_logs_raise(runs):
    cut, texts, metadata = runs["deferred"]
    early_cut, _, early_metadata = runs["immediate"]
    assert early_cut["actions"][:2] == cut["actions"][:2]
    with pytest.raises(ValueError, match="loader commit decision contradicts"):
        owner_evidence(cut, texts, {**metadata, "loader": early_metadata["loader"]})
    with pytest.raises(ValueError, match="request differs"):
        owner_evidence(cut, {**texts, "depot": texts["depot"] + " "}, metadata)
    depot = copy.deepcopy(metadata["depot"])
    depot["accepted_requests"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="depot report request differs"):
        owner_evidence(cut, texts, {**metadata, "depot": depot})
    depot = copy.deepcopy(metadata["depot"])
    depot["accepted_actions"][0] = {"op": "report", "silent": True}
    with pytest.raises(ValueError, match="depot report decision contradicts"):
        owner_evidence(cut, texts, {**metadata, "depot": depot})
    with pytest.raises(ValueError, match="decision the native cut lacks"):
        owner_evidence(prefix(cut, 2), texts, metadata)
    loader = {**metadata["loader"], "phase": "commit"}
    with pytest.raises(ValueError, match="phase contradicts"):
        owner_evidence(cut, texts, {**metadata, "loader": loader})
    with pytest.raises(ValueError, match="not a recall participant log"):
        owner_evidence(cut, texts, {**metadata, "loader": metadata["depot"]})
