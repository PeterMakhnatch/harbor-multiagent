"""Family `schedule`: the analysis hooks that read message texts (carries_own_data, answer_in_text)."""

from __future__ import annotations

import json

import pytest
from conftest import FLOTILLA, family, instance, load

SCHEDULE = family("schedule")
ORACLE = load(FLOTILLA / "world" / "oracle.py")
WORLD = load(FLOTILLA / "world" / "world.py")
SETTINGS = [(difficulty, n_seats) for difficulty in ("easy", "hard") for n_seats in (2, 3)]
FREE = {day: [] for day in SCHEDULE.DAYS}
ANA_CHEN = {"calendars": {"Ana": {**FREE, "Mon": [9, 10]}, "Chen": {**FREE, "Wed": [11]}}}


def test_a_seat_briefing_carries_its_own_data_and_not_a_teammates():
    for difficulty, n_seats in SETTINGS:
        for i in range(20):
            inst = instance("schedule", i, n_seats, difficulty)
            for seat, part in inst["private"].items():
                assert SCHEDULE.carries_own_data(inst["private_text"][seat], part)
                # the oracle's message: the briefing as compact JSON
                payload = json.dumps(part, sort_keys=True, separators=(",", ":"))
                for text in ORACLE.briefing_messages(payload, WORLD.DEFAULT_MAX_TEXT, WORLD.DEFAULT_MAX_MESSAGES):
                    assert SCHEDULE.carries_own_data(text, part)
                for other, text in inst["private_text"].items():
                    if other != seat:  # the text names only the other seat's attendees
                        assert not SCHEDULE.carries_own_data(text, part)


@pytest.mark.parametrize("text", [
    "Ana: Mon 09,10; Tue -; Wed -; Thu -; Fri -",
    "Ana: Mon09,10; Tue-; Wed-",
    "Ana:\nMon:09,10\nTue:-",
    "Ana: Mon(09,10), Tue(-). Chen: Wed(11).",
    "Ana busy Mon[9,10]",
    'Here you go: {"calendars":{"Ana":{"Fri":[],"Mon":[9,10]}}}',
    "Ana:M9,10 T- W- Th- F-",  # the narrow channel's compact form
    "Based on Ana, Chen: possible slots are Mon 11:00, Thu 14:00",  # derived: free windows
    "I have Ana and Chen. My free windows: Tue 13:00-15:00. Please send Ben (e.g. Mon 09,10).",
])
def test_data_in_the_forms_seats_write(text):
    assert SCHEDULE.carries_own_data(text, ANA_CHEN)


@pytest.mark.parametrize("text", [
    "Please send Ben's hours (e.g. Mon 09,10)",  # names the receiver's attendee
    "Please send the busy hours for Ben and Dara (e.g., \"Ben is busy: Mon 09,10; Tue 13\" etc.)",
    "Please share your busy hours for the week (e.g., Mon 09,10; Tue 13).",  # names nobody
    "I have Ana and Chen. Please send Ben's and Dara's busy hours (e.g. Mon 09,10).",  # example in brackets
    'I have Ana and Chen. Send the others in the format "Name is busy: Mon 10,13; Tue 09".',
    "I have Ana and Chen. Can you share Ben and Dara?",  # names without hours
    'Ana, Ben, Chen, Dara: reply with a JSON object like {"day": "Wed", "start": "14:00"}.',
    "Dara busy hours (Mon-Fri)?",
    "",
])
def test_requests_and_format_examples_are_not_data(text):
    assert not SCHEDULE.carries_own_data(text, ANA_CHEN)


def test_carries_own_data_tolerates_odd_briefings():
    for private in (None, {}, {"calendars": None}, {"calendars": []}, "Ana", {"part": ANA_CHEN}):
        assert SCHEDULE.carries_own_data("Ana: Mon 09,10", private) is False


KEY = {"day": "Thu", "start": "14:00"}


@pytest.mark.parametrize("text", [
    "The only slot is Thursday 14:00-16:00. Submitting now.",
    "I found it: Thu 14:00",
    "thu 14",
    "Thursday at 2pm works for everyone",
    'SUBMITTED: {"day": "Thu", "start": "14:00"}',
    "SUBMITTED: {'start': '14:00', 'day': 'Thursday'}",
    "day=Thu start=14",
])
def test_the_answer_in_text(text):
    assert SCHEDULE.answer_in_text(text, KEY)


@pytest.mark.parametrize("text", [
    "Thu 13,14,16 are busy for Ben",  # a busy list names only its first hour: (Thu, 13)
    "Thu 14:30",
    "Wed 14:00",
    'Reply like {"day": "Thu", "start": "14:00"}',  # a format example
    "Please share your hours (e.g. Thu 14)",
    "",
])
def test_not_the_answer(text):
    assert not SCHEDULE.answer_in_text(text, KEY)


def test_no_answer_format_text_names_the_answer():
    for i in range(60):
        inst = instance("schedule", i, 2, "easy")
        key = inst["answer_key"]
        assert not SCHEDULE.answer_in_text(inst["answer_format"], key)
        assert SCHEDULE.answer_in_text(json.dumps(key), key)
        assert SCHEDULE.answer_in_text(f"{key['day']} {key['start']}", key)


def test_mentions():
    assert SCHEDULE.day_hours("Ben: Mon 15; Tue 13,14; Wed -; Thu(9); Fri[10]") == [
        ("Mon", 15), ("Tue", 13), ("Thu", 9), ("Fri", 10)]
    assert SCHEDULE.day_hours("this month 12; Friend 3; m1 p2; Mon 2026; Tue 14:30") == []
    assert SCHEDULE.mentions('Mon 9am, then {"day": "Fri", "start": "16:00"}') == [("Mon", 9), ("Fri", 16)]
