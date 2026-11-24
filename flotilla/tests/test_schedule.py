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
    "Not Mon 09; the slot is Thu 14:00",  # the answer is the second mention
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


def cal(*names: str) -> dict:
    return {"calendars": {name: FREE for name in names}}


# Messages of the canonical waves (c3: 3 seats with a scripted peer; c4: at most 2 messages of 100
# characters), with the verdict of a manual reading: does the message carry the sender's own calendar
# information (raw or derived)? The sender's attendees come first.
REAL = [
    # c4: initials and compact days
    (("Ben", "Fay", "Lea"), "B:M11,T9-11,13,W9,Th9-10,12,15;F:M10-13,F10,13,15;L:M13-15,T9,14,W10,16,Fri16", True),
    (("Ana", "Chen", "Ivo"), "A:M9,10,16;W16;Th11 C:M12-14;Tu9,12-14;W9,10;Th12-15;F13,14,16 I:M14,15;Tu12,13", True),
    (("Ana", "Ben", "Fay"), "A/M10-3,T14,W10,16,R12,14,F10,15|B/M15,T10-1,W10,12,15,R9,F16|F/M10,T10,12,14", True),
    (("Ana", "Chen", "Ivo"), "AM9,10,16W16R11;CM12,13,14T9,12,13,14W9,10R12,13,14,15F13,14,16;IM14,15T12", True),
    (("Ben", "Gus", "Lea"), "B M12 T11,13,15 W9,11,16 Th9 F15;G M10,15 T9,16 W10,11,15 Th15;L M10,14 T15", True),
    (("Ben", "Gus", "Lea"), "B M12,T11/13/15,W9/11/16,Th9,F15@G M10/15,T9/16,W10/11/15,Th15@L M10/14,T15", True),
    (("Ben", "Kai", "Lea"), "Ben M101316 T1014 W1016 F12;Kai M1014 T1215 W9 Th12 F111416", True),
    (("Ana", "Chen", "Ivo"), "AM9,10,16", True),  # an initial glued to a compact day
    # c4: groups of the sender's initials with free windows
    (("Ana", "Chen", "Kai"), "Free A,C,K: T11,12,15; R15; F12. Send B,F,L free slots.", True),
    (("Dara", "Eli", "Ivo"), "Free:D,E,I M14,T14,T15,Th13,F12 . Any work 4 B,G,L?", True),
    (("Fay", "Gus", "Ivo"), "F/G/I free: Mon10, Thu13-15, Fri14-15. Which works for B/C/E?", True),
    (("Dara", "Fay", "Lea"), "Slots free for D,F,L: Wed14, Thu09,10,11,14. Pls share Ana+Chen busy hrs.", True),
    (("Ana", "Ben", "Fay"), "My calc: Thu 10-12 only fits A,B,F. Answer: Thu 10:00", True),
    (("Gus", "Hana", "Kai"), "g+h+k avail: Mon10/11/12/13, Tue11, Thu09, Fri13. Send Ana+B+Fay busy hours!", True),
    # c4: no names at all, or a list of 3 or more windows the sender computed
    (("Dara", "Fay", "Lea"), "M9-10,13-14,16 T16 W9,11 R13,16 F11-14,16;M11,14,15 T11,14,16 W10 F10,13,15", True),
    (("Ben", "Kai", "Lea"), "9-13/9-11,15/13-14/9-11,14/11,12;10/9-11/9,12-15/14,15/9-10,14,15", True),
    (("Gus", "Hana", "Kai"), "G:15/9,13/10,14,15/13,14,16/9,10,11,15,16|H:9,16/14/10/14/11", True),
    (("Eli", "Fay", "Hana"), "pls check Mon11,Mon12,Tue13,Tue14,Tue15,Thu09,Fri13 for Ben,Gus,Lea", True),
    (("Eli", "Fay", "Hana"), "From my data, possible slots: Mon 11, Tue 13/14/15, Thu 09, Fri 10/13. Need Ben/Gus/Lea.", True),
    # c4: requests
    (("Chen", "Dara", "Eli"), "info? I have Chen Dara Eli. Need Ana Fay Hana.", False),
    (("Ben", "Kai", "Lea"), "Please share busy hours for Ana, Dara, Fay (Mon-Fri) as Name:day hour list", False),
    (("Ana", "Ben", "Fay"), "Need busy hours for Ana, Ben, Fay. Format: Name day hours; e.g., Ana Mon 9,10", False),
    # c4: 1 or 2 candidate windows without a name, verbatim (Qwen tight-010, tight-002, tight-000)
    (("Ben", "Gus", "Lea"), "Thu11 and Thu13 both work for me. Which is correct?", True),
    (("Chen", "Dara", "Eli"), "Candidates: Tue 09:00 or Thu 14:00. What is Hana busy?", True),
    (("Ana", "Dara", "Fay"), "Thanks p1! Can you also share Lea busy hours? I think Thu 14-16 works for 5.", True),
    (("Chen", "Fay"), "Need Ben's hours. Mon 11, Tue 13, Thu 9 work for me", True),  # a new sentence: not Ben's
    # c3: requests that list every attendee next to a format hint
    (("Dara", "Kai"), "Please reply with your busy hours for each weekday (hours are whole numbers, e.g., Mon 9,12; "
     "Tue 14; ...). We need them to schedule a 2-hour meeting for Ana, Ben, Dara, Fay, Gus, Kai.", False),
    (("Ben", "Dara"), "Please reply with the busy hours you have for each person (Ben, Chen, Dara, Fay, Ivo, Lea). "
     "Use format: Name: Mon 10,11; Tue 14; ...", False),
    (("Ben", "Dara"), "Please provide the full calendars for Ben, Chen, Dara, Fay, Ivo, and Lea as a JSON object. "
     'Example: {"Ben":["Mon 14","Mon 15"], ...}.', False),
    (("Chen", "Gus", "Ivo"), "Hi p2, could you send the busy hours for Kai and Lea? Example: Kai: Mon 13,16; Tue 09; "
     "... Lea: Wed 10,14; ... Also let me know if any of the Chen or Ivo times I sent are wrong.", False),
    # c3: another seat's claims, relayed or questioned
    (("Ben", "Dara"), "p3, can you re-check Fay busy hours? You said Wed 09,11,13 and Fri 15. p1 said Wed 09,11 and "
     "Fri 09,15. Which is correct?", False),
    (("Dara", "Jun"), "I noticed p1 and p3 have different info for Ben: p1: Tue 10; Fri 09,13,15,16 p3: Tue 10,14; "
     "Fri 09,13,16 Which one is correct?", False),
    (("Dara", "Kai"), "I already shared Dara and Kai. p3 shared Gus: Mon 13,15; Tue 09,12,15,16; Wed 10,12,13,15. "
     "p1 says Ben Thu 10; p3 says Ben Thu 10,13.", False),
    # c3: data that the hook must keep
    (("Chen", "Hana"), "Common slot for Chen, Hana, Eli, Ivo: Wednesday 09:00-11:00. p1, please confirm Ana and Gus.",
     True),
    (("Chen", "Fay", "Lea"), 'Here are the calendars I have (format: "Day HH"): {"Chen":["Mon 09","Mon 10"]}', True),
    (("Chen", "Fay", "Lea"), "p2, please share Ben and Dara busy hours. Only 3 candidates left: Tue 11, Wed 12, Wed 13.",
     True),
]


@pytest.mark.parametrize(("names", "text", "verdict"), REAL)
def test_real_messages_as_read_by_hand(names, text, verdict):
    assert SCHEDULE.carries_own_data(text, cal(*names)) is verdict


def test_an_initial_counts_only_when_one_attendee_of_the_sender_has_it():
    assert SCHEDULE.carries_own_data("A:M9,10 T11", cal("Ana", "Ben"))
    assert not SCHEDULE.carries_own_data("A:M9,10 T11", {"calendars": {"Ana": FREE, "Abe": FREE}})
    assert not SCHEDULE.carries_own_data("B:M9,10 T11", cal("Ana", "Chen"))  # the receiver's Ben


def test_format_hints_run_to_the_end_of_the_line_or_parenthesis():
    hinted = SCHEDULE.without_hints("Ana: Mon 09 (same format, e.g. Tue 10) Wed 11\nFormat: Thu 12\nFri 13")
    assert SCHEDULE.day_hours(hinted) == [("Mon", 9), ("Wed", 11), ("Fri", 13)]
    assert SCHEDULE.without_hints("no hint, Mon 09") == "no hint, Mon 09"


# Misreads found by the review of #30, and cases that pin single branches of the rule.
CASES = [
    (("Ana", "Chen"), 'Reply as JSON, e.g. {"Ben": {"Mon": [9], "Tue": [10], "Thu": [11]}}', False),
    (("Ana", "Chen"), "Please send Ben's busy hours in the format:\n- Name is busy: Mon 9,10; Tue 13", False),
    (("Ana", "Chen"), "I have Ana. Use this format:\n- Name is busy: Mon 9,10; Tue 13", False),  # the next line
    (("Ben", "Dara"), 'Please send Ben, Chen, Dara, Fay as arrays of strings like "Mon 9".', False),
    (("Ana", "Ben"), "p1, Mon 10, Tue 11 and Wed 12 work for Ana and Ben.", True),  # p1 is addressed
    (("Ana", "Ben"), "I M9,10 T11 W12 free", True),  # the pronoun, not Ivo's initial
    (("Ana", "Ben"), "p3, p2 claims only Thu 13:00-14:00 works but that is not a 2-hour meeting.", False),
    (("Chen", "Hana"), "Are Ana and Gus free on Wednesday 09:00-11:00?", False),
    (("Ben", "Dara"), "a Mon 9 or Tue 10 slot works for me", True),  # a lower-case "a" is not Ana's initial
    (("Ana", "Chen"), "A,C,E,I: Mon9 Tue10 Wed11", True),  # a group of own and other initials
    (("Dara", "Fay"), "D/F ok with Mon9, Tue10", True),  # a group of two initials
    (("Ana", "Chen"), "Send your hours like: Mon 9, Tue 10, Wed 11", False),
    (("Ana", "Chen"), "You said Mon 9, Tue 10 and Wed 11", False),
    (("Ben", "Gus"), "Mon09 Tue13", True),  # a bare list of windows
    (("Ben", "Gus"), "Thu 14:00, submitting now", False),  # one window, no cue: an announcement
]


@pytest.mark.parametrize(("names", "text", "verdict"), CASES)
def test_rule_branches(names, text, verdict):
    assert SCHEDULE.carries_own_data(text, cal(*names)) is verdict
