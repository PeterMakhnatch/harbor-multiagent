import json
import os
import select
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from stat import S_IMODE

import pytest

from examples.seatlab.visibility import replica_io


@pytest.fixture
def turns(tmp_path: Path) -> Path:
    directory = tmp_path / "turns"
    directory.mkdir(mode=0o700)
    return directory


def test_exchange_preserves_raw_text_and_writes_only_private_receipt(
    turns: Path,
) -> None:
    prompt = "private prompt\né"
    text = "\n not a model action  \N{SNOWMAN} \n"
    incoming = BytesIO(
        json.dumps({"id": "new-family:check", "text": text}).encode() + b"\n"
    )
    outgoing = BytesIO()
    exchange = replica_io.Exchange(incoming, outgoing, turns)
    assert exchange.ask("new-family", "check", "seat_a", prompt) == text
    receipt = turns / "new-family_check.json"
    assert S_IMODE(receipt.stat().st_mode) == 0o600
    assert json.loads(receipt.read_bytes()) == {
        "schema": "seatlab.relay-turn/v1",
        "run": "new-family",
        "role": "check",
        "seat": "seat_a",
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "response_sha256": sha256(text.encode()).hexdigest(),
        "response_text": text,
    }
    with pytest.raises(ValueError, match="cannot be retried"):
        exchange.ask("new-family", "check", "seat_b", "changed prompt")
    assert outgoing.getvalue().endswith(b"\n")
    assert outgoing.getvalue().count(b"\n") == 1
    assert json.loads(outgoing.getvalue()) == {
        "type": "turn",
        "id": "new-family:check",
        "prompt": prompt,
    }


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b'{"id":"rx-a:check","text":"missing LF"}',
        b'{"id":"rx-a:check","text":"CRLF"}\r\n',
        b'{"id":"wrong","text":"raw response"}\n',
        b'{"id":"rx-a:check","text":"raw response","text":"duplicate"}\n',
        b'{"id":"rx-a:check","text":"raw response","model":"extra"}\n',
        b'{"id":"rx-a:check","text":true}\n',
        b'{"id":"rx-a:check","text":"\xff"}\n',
        b'{"id":"rx-a:check","text":"\\ud800"}\n',
        b'{"id":"rx-a:check","text":"1"}\n{"id":"rx-a:check","text":"2"}\n',
        b'{"id":"rx-a:check","text":"'
        + b"x" * (replica_io.MAX_TEXT_BYTES + 1)
        + b'"}\n',
        b" " * replica_io.MAX_FRAME_BYTES + b"\n",
    ],
)
def test_invalid_reply_is_not_a_receipt_or_a_retry(turns: Path, reply: bytes) -> None:
    outgoing = BytesIO()
    exchange = replica_io.Exchange(BytesIO(reply), outgoing, turns)
    with pytest.raises(ValueError, match="^Invalid relay turn$"):
        exchange.ask("rx-a", "check", "seat_a", "prompt")
    with pytest.raises(ValueError, match="cannot be retried"):
        exchange.ask("rx-a", "check", "seat_a", "prompt")
    with pytest.raises(ValueError, match="cannot be retried"):
        exchange.ask("rx-a", "next", "seat_b", "another prompt")
    assert outgoing.getvalue().count(b"\n") == 1
    assert not list(turns.iterdir())


def test_existing_receipt_is_not_replaced_or_redispatched(turns: Path) -> None:
    receipt = turns / "rx-a_check.json"
    receipt.write_bytes(b"original")
    outgoing = BytesIO()
    exchange = replica_io.Exchange(BytesIO(), outgoing, turns)
    with pytest.raises(ValueError, match="^Invalid relay turn$"):
        exchange.ask("rx-a", "check", "seat_a", "prompt")
    assert receipt.read_bytes() == b"original"
    assert not outgoing.getvalue()


def test_open_pipe_without_full_line_times_out(
    turns: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(replica_io, "_TURN_TIMEOUT_SEC", 0.25)
    in_read, in_write = os.pipe()
    out_read, out_write = os.pipe()
    with (
        os.fdopen(in_read, "rb") as incoming,
        os.fdopen(in_write, "wb") as reply,
        os.fdopen(out_read, "rb") as request,
        os.fdopen(out_write, "wb") as outgoing,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        exchange = replica_io.Exchange(incoming, outgoing, turns)
        turn = executor.submit(exchange.ask, "rx-a", "check", "seat_a", "prompt")
        assert select.select([request], [], [], 1)[0]
        assert json.loads(os.read(request.fileno(), 1024)) == {
            "type": "turn",
            "id": "rx-a:check",
            "prompt": "prompt",
        }
        reply.write(b'{"id":"rx-a:check","text":"partial')
        reply.flush()
        with pytest.raises(TimeoutError, match="^Relay turn timed out$"):
            turn.result(timeout=1)
        with pytest.raises(ValueError, match="cannot be retried"):
            exchange.ask("rx-a", "check", "seat_a", "prompt")
    assert not list(turns.iterdir())


def test_binding_is_exclusive_visible_to_worker_and_cleared_on_error(
    turns: Path,
) -> None:
    exchange = replica_io.Exchange(BytesIO(), BytesIO(), turns)
    with pytest.raises(ValueError, match="already bound"), replica_io.bind(exchange):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(replica_io.current).result(timeout=1) is exchange
        with replica_io.bind(exchange):
            pytest.fail("nested exchange binding succeeded")
    with pytest.raises(ValueError, match="No relay exchange is bound"):
        replica_io.current()
