import re
from collections.abc import Mapping, Sequence
from pathlib import Path

_REDACTED = "[REDACTED]"

# A string ends with its line, so a stray quote cannot shift later lines and
# a truncated file stays linear.
_JSON_STRING_RE = re.compile(r'"[^"\\\n]*(?:\\.[^"\\\n]*)*(?:"|\\?$)', re.MULTILINE)

# Output wrapped to a width breaks a long secret: a line break, with the
# indentation or padding around it. JSON escapes that whitespace, once more
# for each level of JSON nested in a string. Every part is bounded and
# possessive: a wrap never holds a character of a secret (no whitespace or
# backslash), so a match never needs to give a wrap's characters back, and a
# scan stays linear however long the runs of escapes in the text are.
_WRAP = (
    r"(?:[ \t\r]|\\{1,8}[rt]){0,128}+"
    r"(?:\n|\\{1,8}n)"
    r"(?:[ \t\r\n]|\\{1,8}[nrt]){0,128}+"
)
# Every character a wrap can hold. A text holds a wrapped secret only if it
# holds the secret once both are without these characters.
_WRAP_CHARS = b" \t\r\n\\nrt"
# Two short values on adjacent lines are more likely ordinary output than a
# wrapped secret.
_MIN_WRAPPED_SECRET_LENGTH = 16
_WHITESPACE_OR_BACKSLASH_RE = re.compile(r"[\s\\]")


def _can_be_wrapped(secret: str) -> bool:
    return (
        len(secret) >= _MIN_WRAPPED_SECRET_LENGTH
        # Numbers often fill a column, one per line.
        and not secret.isdigit()
        # A wrap could claim such a character of the secret, and the
        # possessive wrap would then miss the secret.
        and not _WHITESPACE_OR_BACKSLASH_RE.search(secret)
    )


def _without_wrap_chars(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass").translate(None, _WRAP_CHARS)


def _wrapped_secret_patterns(
    text: str, secrets: Sequence[str]
) -> dict[str, re.Pattern[str]]:
    """Patterns for the secrets that the text may hold with wraps in them.

    A pattern costs a regex scan of the text per secret, so one pass first
    rules out the secrets that the text cannot hold however it is wrapped.
    """
    candidates = [secret for secret in secrets if _can_be_wrapped(secret)]
    if not candidates:
        return {}
    remainder = _without_wrap_chars(text)
    return {
        secret: re.compile(f"(?:{_WRAP})?+".join(map(re.escape, secret)))
        for secret in candidates
        if _without_wrap_chars(secret) in remainder
    }


def _scrub_text(
    text: str,
    secrets: Sequence[str],
    wrapped: Mapping[str, re.Pattern[str]],
) -> str:
    for secret in secrets:
        if (pattern := wrapped.get(secret)) is not None:
            text = pattern.sub(_REDACTED, text)
        else:
            text = text.replace(secret, _REDACTED)
    return text


def scrub_file_text(path: Path, text: str, secrets: Sequence[str]) -> str:
    """Replace secrets, ordered longest first, in a trial output file's text.

    A secret of 16 or more characters is also replaced where wrapped output
    split it across lines, raw or JSON-escaped; the line break goes with it.
    Digit-only secrets, and secrets that hold whitespace or a backslash, are
    replaced only where they appear unbroken. JSON files keep digit-only
    secrets in their string tokens only: replacing an unquoted number such as
    a token count would make the file invalid.
    """
    wrapped = _wrapped_secret_patterns(text, secrets)
    if not wrapped and not any(secret in text for secret in secrets):
        return text
    if path.suffix.lower() in (".json", ".jsonl") and any(
        secret.isdigit() and secret in text for secret in secrets
    ):
        text = _JSON_STRING_RE.sub(
            lambda match: _scrub_text(match.group(), secrets, wrapped), text
        )
        secrets = [secret for secret in secrets if not secret.isdigit()]
    return _scrub_text(text, secrets, wrapped)
