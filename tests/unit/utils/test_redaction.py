"""Secrets that wrapped output split across lines are still scrubbed."""

import json
import random
import string
import time
from pathlib import Path

import pytest

from harbor.utils.redaction import scrub_file_text

# 73 characters, as long as the provider key a wrapped agent log once kept.
SECRET = "sk-or-v1-" + "0123456789abcdef" * 4
LOG = Path("mini-swe-agent.txt")
TRAJECTORY = Path("trajectory.json")

WRAPS = {
    "line feed": "\n",
    "CRLF": "\r\n",
    "indented": "\n    ",
    "tab-indented": "\n\t",
    "padded": "  \n  ",
    "blank line": "\n\n",
    "JSON string": "\\n",
    "JSON string, CRLF": "\\r\\n",
    "JSON string, indented": "\\n  ",
    "JSON string, escaped tab": "\\n\\t",
    "JSON in a JSON string": "\\\\n",
}


def test_scrub_covers_a_key_wrapped_at_80_columns() -> None:
    # mini-swe-agent prints its config specs through Rich, which wraps a
    # line at 80 columns when the output is not a terminal.
    log = (
        "'model.model_kwargs.reasoning_effort=low', \n"
        f"'model.model_kwargs.api_key=\"{SECRET[:51]}\n{SECRET[51:]}\"']\n"
    )

    assert scrub_file_text(LOG, log, [SECRET]) == (
        "'model.model_kwargs.reasoning_effort=low', \n"
        "'model.model_kwargs.api_key=\"[REDACTED]\"']\n"
    )


@pytest.mark.parametrize("wrap", WRAPS.values(), ids=WRAPS.keys())
def test_scrub_covers_a_wrap_at_every_position(wrap: str) -> None:
    for cut in range(1, len(SECRET)):
        text = f"key {SECRET[:cut]}{wrap}{SECRET[cut:]} ok\n"

        assert scrub_file_text(LOG, text, [SECRET]) == "key [REDACTED] ok\n", cut


@pytest.mark.parametrize("width", [1, 7, 24, 36])
def test_scrub_covers_a_secret_wrapped_more_than_once(width: int) -> None:
    lines = [SECRET[i : i + width] for i in range(0, len(SECRET), width)]
    text = "key " + "\n  ".join(lines) + " ok\n"

    assert scrub_file_text(LOG, text, [SECRET]) == "key [REDACTED] ok\n"


def test_scrub_covers_wrapped_and_unwrapped_copies_together() -> None:
    text = f"{SECRET}\n{SECRET[:10]}\n{SECRET[10:]}\nx{SECRET}y\n"

    assert scrub_file_text(LOG, text, [SECRET]) == (
        "[REDACTED]\n[REDACTED]\nx[REDACTED]y\n"
    )


def test_scrub_covers_a_wrapped_secret_in_non_ascii_text() -> None:
    # A lone surrogate reaches the text from a file read with surrogateescape.
    text = f"│ ✓ 😀 \udc80 {SECRET[:40]}\n│ {SECRET[40:]}\n{SECRET[:40]}\n  {SECRET[40:]} │"

    assert scrub_file_text(LOG, text, [SECRET]) == (
        f"│ ✓ 😀 \udc80 {SECRET[:40]}\n│ {SECRET[40:]}\n[REDACTED] │"
    )


def test_scrub_covers_random_wraps_of_random_secrets() -> None:
    rng = random.Random(0)
    alphabet = string.ascii_letters + string.digits + "-_./+=:"
    # Any run of these that holds a line break is a wrap.
    line_breaks = ["\n", "\\n", "\\\\n"]
    spacing = [" ", "\t", "\r", "\\t", "\\r"]
    for _ in range(60):
        # The letters that end an escape also start and end secrets.
        secret = rng.choice("nrtN") + "".join(
            rng.choice(alphabet) for _ in range(rng.randint(15, 40))
        )
        wrapped = secret[0]
        for char in secret[1:]:
            if rng.random() < 0.15:
                atoms = rng.choices(spacing, k=rng.randint(0, 3))
                atoms.insert(rng.randint(0, len(atoms)), rng.choice(line_breaks))
                wrapped += "".join(atoms)
            wrapped += char
        before = rng.choice(["", "x", "\\", " ", "\n", '"'])
        after = rng.choice(["", "y", " ", "\n", "\\n", '"'])

        assert (
            scrub_file_text(LOG, f"{before}{wrapped}{after}", [secret])
            == f"{before}[REDACTED]{after}"
        ), (secret, wrapped)


@pytest.mark.parametrize(
    "text",
    [
        f"{SECRET[:30]} {SECRET[30:]}",
        f"{SECRET[:30]}\t{SECRET[30:]}",
        f"{SECRET[:30]}\r{SECRET[30:]}",
        f"{SECRET[:30]}\\t{SECRET[30:]}",
        f"{SECRET[:30]}\\{SECRET[30:]}",
        f"{SECRET[:30]}\\\n{SECRET[30:]}",
        f"{SECRET[:30]}-\n{SECRET[30:]}",
        f"{SECRET[:30]}\n> {SECRET[30:]}",
        f"{SECRET[:30]} │\n│ {SECRET[30:]}",
        f"{SECRET[:30]}\n{SECRET[31:]}",
        f"{SECRET[:30]}\nX{SECRET[31:]}",
        f"{SECRET[:30]}\n{SECRET[30:-1]}",
    ],
    ids=[
        "space",
        "tab",
        "carriage return",
        "escaped tab",
        "backslash",
        "line continuation",
        "hyphen",
        "quote prefix",
        "box border",
        "missing character",
        "wrong character",
        "cut short",
    ],
)
def test_scrub_joins_secret_fragments_only_across_wraps(text: str) -> None:
    assert scrub_file_text(LOG, text, [SECRET]) == text


@pytest.mark.parametrize(
    "secret",
    [
        "fifteen-chars-k",
        "1234567890123456",
        "correct horse battery staple",
        "pass\\word-with-a-backslash",
    ],
    ids=["short", "digit-only", "holds whitespace", "holds a backslash"],
)
def test_scrub_matches_other_secrets_exactly_as_before(secret: str) -> None:
    wrapped = f"{secret[:8]}\n{secret[8:]}"

    assert scrub_file_text(LOG, f"a {wrapped} b {secret} c", [secret]) == (
        f"a {wrapped} b [REDACTED] c"
    )


def test_scrub_covers_wrapped_secrets_from_sixteen_characters() -> None:
    assert scrub_file_text(LOG, "sixteen-\nchars-ke", ["sixteen-chars-ke"]) == (
        "[REDACTED]"
    )


def test_scrub_replaces_the_longer_of_overlapping_wrapped_secrets() -> None:
    header = f"Bearer-{SECRET}"
    text = f"auth {header[:20]}\n{header[20:]} ok"

    assert scrub_file_text(LOG, text, [header, SECRET]) == "auth [REDACTED] ok"


@pytest.mark.parametrize("line_break", ["\n", "\r\n", "\n    "])
def test_scrub_keeps_json_valid_around_a_wrapped_secret(line_break: str) -> None:
    screen = f"key {SECRET[:51]}{line_break}{SECRET[51:]} ok\n"
    trajectory = json.dumps({"observation": screen}, indent=2)
    # A model reply that quotes the screen is JSON inside a JSON string.
    reply = json.dumps({"content": json.dumps({"analysis": screen})})
    # A terminal recording keeps the line break of one output event.
    cast = json.dumps([1.5, "o", screen])

    assert json.loads(scrub_file_text(TRAJECTORY, trajectory, [SECRET])) == {
        "observation": "key [REDACTED] ok\n"
    }
    scrubbed = json.loads(scrub_file_text(TRAJECTORY, reply, [SECRET]))
    assert json.loads(scrubbed["content"]) == {"analysis": "key [REDACTED] ok\n"}
    assert json.loads(scrub_file_text(Path("recording.cast"), cast, [SECRET])) == [
        1.5,
        "o",
        "key [REDACTED] ok\n",
    ]


def test_scrub_keeps_json_numbers_beside_a_wrapped_secret() -> None:
    pin = "1234567890123456"
    screen = f"key {SECRET[:51]}\n{SECRET[51:]} pin {pin}\n"
    trajectory = json.dumps({"n_input_tokens": int(pin), "observation": screen})
    # Two numbers on adjacent lines must not join into the digit-only secret.
    counts = f"{pin[:8]}\n{pin[8:]}\n"

    scrubbed = scrub_file_text(TRAJECTORY, trajectory, [SECRET, pin])

    assert json.loads(scrubbed) == {
        "n_input_tokens": int(pin),
        "observation": "key [REDACTED] pin [REDACTED]\n",
    }
    assert scrub_file_text(Path("counts.jsonl"), counts, [SECRET, pin]) == counts
    assert scrub_file_text(Path("counts.txt"), counts, [SECRET, pin]) == counts


def test_scrub_stays_linear_on_large_and_hostile_logs() -> None:
    rng = random.Random(0)
    secrets = sorted(
        ["".join(rng.choices(string.ascii_letters, k=40)) for _ in range(20)]
        + [SECRET],
        key=len,
        reverse=True,
    )
    lines = "\n".join(" ".join(rng.choices(secrets, k=3))[:60] for _ in range(1000))
    start = SECRET[:5]
    hostile = [
        (lines + "\n") * 20,
        # Long runs of everything a wrap can hold, after the start of a secret.
        (start + " " * 200_000 + "\n") * 5,
        (start + "\n" * 200_000) * 5,
        (start + "\\" * 200_000 + "n") * 5,
        (start + "\\n \\r\\t" * 30_000) * 5,
        # Almost the secret, over and over.
        (SECRET[:-1] + "\n  ") * 20_000,
    ]

    # CPU time: a busy machine must not fail this.
    started = time.process_time()
    for text in hostile:
        scrubbed = scrub_file_text(LOG, f"{text}{SECRET[:9]}\n{SECRET[9:]}", secrets)
        assert scrubbed.endswith("[REDACTED]")

    assert time.process_time() - started < 5
