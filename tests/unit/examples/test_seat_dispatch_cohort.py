import hashlib
import hmac
import itertools
import json
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.physical_dispatch import certificate, cohort, cohort_draw

SCHEMA = "seatlab.physical-dispatch.cohort-draw.v1"
CELLS = ("original-other", "original-relevant", "swapped-other", "swapped-relevant")
SEED_LIMIT = 2**63
SECRET = hashlib.sha256(b"development-only-replay").digest()


def naturally_focal(seed: int) -> bool:
    case = certificate.make_case(seed, "fork", layout="draw")
    return case["realized"] == min(brief["id"] for brief in case["briefs"])


def candidate_sample(secret: bytes, index: int) -> bytes:
    message = (
        SCHEMA.encode("ascii")
        + b"\x00candidate-seed\x00"
        + index.to_bytes(8, "big")
        + (0).to_bytes(8, "big")
    )
    return hmac.digest(secret, message, "sha256")


def candidate_seed(sample: bytes) -> int:
    return int.from_bytes(sample[:8], "big") & (SEED_LIMIT - 1)


def assert_williams_blocks(cases: list[dict[str, Any]]) -> None:
    assert len(cases) % 4 == 0
    every_pair = set(itertools.permutations(CELLS, 2))
    assert len(every_pair) == 12
    for start in range(0, len(cases), 4):
        rows = [case["order"] for case in cases[start : start + 4]]
        for row in rows:
            assert sorted(row) == sorted(CELLS)
        for position in range(4):
            assert {row[position] for row in rows} == set(CELLS)
        pairs = [pair for row in rows for pair in itertools.pairwise(row)]
        assert len(pairs) == 12
        assert set(pairs) == every_pair


def test_roster_does_not_replace_eligible_draws_and_balances_order() -> None:
    design = cohort_draw.make_design(8, SECRET, development=True)
    candidates = [
        (index, candidate_seed(candidate_sample(SECRET, index)))
        for index in range(1, design["candidates_checked"] + 1)
    ]
    assert [(case["draw_index"], case["seed"]) for case in design["cases"]] == [
        (index, seed) for index, seed in candidates if naturally_focal(seed)
    ]
    assert_williams_blocks(design["cases"])
    digests = []
    for row in design["cases"]:
        _, maps = certificate.native_pair(row["seed"])
        assert {
            name: certificate.case_commitment(case) for name, case in maps.items()
        } == row["case_commitments"]
        digests.extend(row["case_commitments"].values())
    assert len(digests) == len(set(digests)) == 16
    assert cohort_draw.validate_design(json.loads(json.dumps(design))) == design
    for key in ("order", "seed", "case_commitments", "draw_index"):
        altered = deepcopy(design)
        first, second = altered["cases"][:2]
        first[key] = second[key] if key != "draw_index" else True
        with pytest.raises(ValueError):
            cohort_draw.validate_design(altered)


def test_external_pin_rejects_another_valid_design_and_never_discloses_cases(
    tmp_path: Path,
) -> None:
    out = tmp_path / "study"
    second = tmp_path / "other"
    receipt = cohort.enroll(out, 4, 7)
    other = cohort.enroll(second, 4, 17)
    pin = receipt["cohort_digest"]
    design, checked = cohort.load_design(out, pin)
    assert checked == receipt
    with pytest.raises(ValueError):
        cohort.load_design(second, pin)
    before = (out / "design.json").read_bytes()
    with pytest.raises((OSError, ValueError)):
        cohort.enroll(out, 4, 17)
    assert (out / "design.json").read_bytes() == before and other[
        "cohort_digest"
    ] != pin
    for path, expected in (
        (out, 0o700),
        (out / "design.json", 0o600),
        (out / "receipt.json", 0o600),
    ):
        assert stat.S_IMODE(path.stat().st_mode) == expected
    public = json.dumps(receipt)
    assert design["secret"] not in public
    assert all(str(row["seed"]) not in public for row in design["cases"])
    assert receipt["cases"] == 4 and receipt["planned_decisions"] == 16
    row = design["cases"][0]
    check = cohort.qualify_case(out, row["id"], pin)
    assert (
        check["native_dispatches_checked"] == 256
        and check["attainable_other"] == "1/2"
        and check["attainable_relevant"] == "1"
    )
    assert str(row["seed"]) not in json.dumps(check)
    (out / "design.json").chmod(0o644)
    with pytest.raises(ValueError):
        cohort.load_design(out, pin)
