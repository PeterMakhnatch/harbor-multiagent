"""Tests for flotilla_analysis (standard library unittest; pytest runs them too)."""

import unittest


def _assertions_are_live() -> None:
    """Stop at once if this interpreter disabled unittest's assertions (a sitecustomize or .pth hook
    that replaces TestCase.assert* would make every test pass without checking anything)."""
    try:
        unittest.TestCase().assertEqual(1, 2)
    except AssertionError:
        return
    raise RuntimeError(
        "unittest assertions do nothing in this Python interpreter; run the tests with another one"
    )


_assertions_are_live()
