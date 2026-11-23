import math
import re
from fractions import Fraction
from typing import Any

MEASURES = ("held_out", "validation", "verifier")
FINAL_HOUR = (180, 240)
MAX_POSTS = 100_000
MAX_TEXT_CHARS = 1_000_000
POST_KEYS = frozenset({"minute", "seat", "text"})

_SEPARATOR = r"[ _\-\u2010\u2011]++"
_LABEL = re.compile(
    r"(?<![\w\-\u2010\u2011])"
    rf"(?:(?P<held_out>held{_SEPARATOR}out)|(?P<validation>validation)"
    r"|(?P<verifier>verifier))"
    rf"{_SEPARATOR}score[ \t]*+[:=][ \t]*+"
)
_NUMBER = re.compile(r"[01](?:\.[0-9]{1,32})?|\.[0-9]{1,32}")
_TERMINATORS = frozenset(".,;:!?)]}\"'*`")
_BAD_AFTER_TERMINATOR = re.compile(r"[\w%/+\-\u2010-\u2015\u2212]")
_RANGE_PERCENT_OR_SECOND_NUMBER = re.compile(
    r"\s*+(?:%|per\s*+cent\b|pct\b"
    r"|(?:[\-\u2010-\u2015\u2212~/]|\.\.|to\b|or\b|,)?+\s*+[+\-\u2212]?\.?[0-9])"
)


def _bounded_end(text: str, end: int) -> bool:
    following = text[end : end + 1]
    if not following or following.isspace():
        return True
    if following not in _TERMINATORS:
        return False
    after = text[end + 1 : end + 2]
    return not after or _BAD_AFTER_TERMINATOR.match(after) is None


def _value(text: str, start: int) -> Fraction | None:
    number = _NUMBER.match(text, start)
    if number is None or not _bounded_end(text, number.end()):
        return None
    if _RANGE_PERCENT_OR_SECOND_NUMBER.match(text, number.end()) is not None:
        return None
    value = Fraction(number.group())
    return value if 0 <= value <= 1 else None


def _scan(text: str, claims: dict[str, str]) -> None:
    folded = text.casefold()
    for label in _LABEL.finditer(folded):
        measure = label.lastgroup
        if measure is None or measure not in MEASURES:
            raise ValueError("label grammar matched an unknown measure")
        value = _value(folded, label.end())
        if value is not None:
            claims[measure] = str(value)


def _check_post(post: dict[str, Any], previous_minute: float) -> float:
    if set(post) != POST_KEYS:
        raise ValueError("expected posts projected to minute, seat and text")
    minute, text = post["minute"], post["text"]
    if not isinstance(minute, int | float) or isinstance(minute, bool):
        raise TypeError("post minute must be a real number")
    if not math.isfinite(minute):
        raise ValueError("post minute must be finite")
    if not FINAL_HOUR[0] <= minute < FINAL_HOUR[1]:
        raise ValueError("post lies outside the registered final hour")
    if minute < previous_minute:
        raise ValueError("posts must be in chronological order")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError("post text must be a bounded string")
    return minute


def last_claims(posts: list[dict[str, Any]]) -> dict[str, str]:
    if len(posts) > MAX_POSTS:
        raise ValueError("too many final-hour posts")
    claims: dict[str, str] = {}
    previous_minute: float = FINAL_HOUR[0]
    for post in posts:
        previous_minute = _check_post(post, previous_minute)
        _scan(post["text"], claims)
    _check_claims(claims)
    return {measure: claims[measure] for measure in MEASURES if measure in claims}


def _check_claims(claims: dict[str, str]) -> None:
    if not set(claims) <= set(MEASURES):
        raise ValueError("claims contain an unknown measure")
    for text in claims.values():
        if not isinstance(text, str):
            raise TypeError("claim must be an exact fraction string")
        value = Fraction(text)
        if str(value) != text or not 0 <= value <= 1:
            raise ValueError("claim must be a canonical fraction from 0 to 1")


def choose(claims: list[dict[str, str]]) -> tuple[str | None, tuple[int, ...]]:
    if len(claims) != 2:
        raise ValueError("expected claims from exactly two boards")
    for side in claims:
        _check_claims(side)
    first, second = claims
    for measure in MEASURES:
        if measure in first and measure in second:
            a, b = Fraction(first[measure]), Fraction(second[measure])
            if a == b:
                return measure, (0, 1)
            return measure, ((0,) if a > b else (1,))
    return None, (0, 1)
