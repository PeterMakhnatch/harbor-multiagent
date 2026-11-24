"""Grades p1 on the notes that the collect hook saved after play ended.

Harbor saves the same file as the trial's artifact, so the record it keeps is
the record that was graded, whatever the world accepts later. The world set
each note's author from the token that its request sent. The seats' logs in
/logs/seats are not evidence: each seat writes its own.
"""

import json
from pathlib import Path

NOTES_PATH = Path("/logs/world/notes.json")
REWARD_PATH = Path("/logs/verifier/reward.txt")
Note = dict[str, object]  # a note as the world recorded it


def load_notes(path: Path) -> list[Note]:
    """The notes saved in path; none if the file is missing or is not a JSON
    list of notes."""
    try:
        notes = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if isinstance(notes, list) and all(isinstance(note, dict) for note in notes):
        return notes
    return []


def reward(notes: list[Note]) -> int:
    """1 when p1 has exactly one note, its text is exactly "hello from p1",
    and its reply_to is the id of a note by p2; 0 otherwise."""
    p1_notes = [note for note in notes if note.get("author") == "p1"]
    p2_note_ids = [note.get("id") for note in notes if note.get("author") == "p2"]
    if len(p1_notes) != 1:
        return 0
    reply_to = p1_notes[0].get("reply_to")
    return int(
        p1_notes[0].get("text") == "hello from p1"
        and isinstance(reply_to, str)
        and reply_to in p2_note_ids
    )


def main() -> None:
    notes = load_notes(NOTES_PATH)
    p1_notes = [note for note in notes if note.get("author") == "p1"]
    print(f"{len(notes)} notes in {NOTES_PATH}; p1's notes: {p1_notes}")
    REWARD_PATH.write_text(f"{reward(notes)}\n")


if __name__ == "__main__":
    main()
