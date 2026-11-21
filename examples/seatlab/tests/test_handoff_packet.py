import hashlib
import json
from random import Random

from examples.seatlab.handoff.packet import grid, parse


_CHOICES = ("amber", "blue", "green")
_BRIEF = "Set the reading room lamp to the filter named on each manuscript's conservation card."


def _card(record_id: str, lamp: str) -> dict[str, str]:
    body = f"Approved filter: {lamp}."
    return {
        "id": record_id,
        "old_version": "Catalogue 1 / α",
        "new_version": "Catalogue 1 / α",
        "before": body,
        "after": body,
        "question": "Which filter does the conservation card require?",
        "old_best": lamp,
        "best": lamp,
    }


def _encode(
    cases: list[dict[str, object]], choices: tuple[str, ...] = _CHOICES
) -> bytes:
    return (
        json.dumps(
            {
                "schema": "seatlab.handoff-input/v1",
                "brief": _BRIEF,
                "choices": choices,
                "cases": cases,
            }
        ).encode("utf-8")
        + b"\n"
    )


def _reject(raw: bytes, expected: str | None = None) -> None:
    try:
        parse(raw, expected)
    except ValueError:
        return
    raise AssertionError("accepted a malformed or uncommitted packet")


def test_handoff_packet_replay() -> None:
    rng = Random(18271)
    for _ in range(8):
        first, second, new = rng.sample(_CHOICES, k=3)
        a, b = _card("folio_A", first), _card("folio_B", second)
        revised = a | {
            "new_version": "Catalogue 2 / β",
            "after": f"Approved filter: {new}.",
            "best": new,
        }
        stable_case: dict[str, object] = {
            "id": "vault-z",
            "cluster": "east-wing",
            "records": [a, b],
        }
        revised_case = stable_case | {"id": "vault-a", "records": [revised, b]}
        raw = _encode([stable_case, revised_case])
        digest = hashlib.sha256(raw).hexdigest()
        packet = parse(raw, digest)
        assert packet.brief == _BRIEF and packet.choices == _CHOICES
        stable, changed = (case.records[0] for case in packet.cases)
        assert (stable.before, stable.after, stable.old_best, stable.best) == (
            a["before"],
            a["before"],
            first,
            first,
        )
        assert (changed.before, changed.after, changed.old_best, changed.best) == (
            a["before"],
            revised["after"],
            first,
            new,
        )
        assert (
            changed.old_version == "Catalogue 1 / α"
            and changed.new_version == "Catalogue 2 / β"
        )
        assert [(block.index, block.rep, block.case.id) for block in grid(packet)] == [
            (0, 0, "vault-z"),
            (0, 1, "vault-z"),
            (1, 0, "vault-a"),
            (1, 1, "vault-a"),
        ]
        for block in grid(packet):
            assert block.fingerprint == digest
            assert block.key == f"ho-{digest[:12]}-{block.case.id}-r{block.rep}"
            assert block.case.cluster == "east-wing"
        assert parse(raw + b"\n").sha256 != digest
        _reject(raw + b"\n", digest)
        _reject(raw.replace(b'"id": "folio_A"', b'"id": "other", "id": "folio_A"', 1))
        _reject(_encode([stable_case, stable_case]))
        _reject(_encode([stable_case], (first, second, second)))
        _reject(_encode([stable_case | {"id": "Vault-Z"}]))
        _reject(_encode([stable_case | {"records": [a, a]}]))
        _reject(
            _encode([revised_case | {"records": [revised, b | {"new_version": "2"}]}])
        )
        for fault in (
            {"after": revised["after"]},
            {"before": revised["after"]},
            {"best": new},
            {"old_best": new},
            {"best": "unknown"},
            {"question": "é" * 129},
            {"question": "Control\ncharacter"},
            {"new_version": "é" * 33},
            {"new_version": "\ud800"},
            {"extra": "field"},
        ):
            _reject(_encode([stable_case | {"records": [a | fault, b]}]))
