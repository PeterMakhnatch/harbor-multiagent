import os
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.rolepack import calendar_source, evidence, protocol

_REPORT = Path("examples/seatlab/results/flotilla-calendar-list-results.json")


@pytest.fixture(scope="module")
def calendar_root() -> Path:
    if not (path := os.environ.get("SEATLAB_CALENDAR_ROOT")):
        pytest.skip("set SEATLAB_CALENDAR_ROOT to the trusted calendar-list checkout")
    return Path(path).absolute()


def test_altered_public_report_is_rejected_before_source_execution(
    tmp_path: Path,
) -> None:
    source = tmp_path.resolve() / _REPORT
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{"schema":"seatlab.calendar-list-report/v1","rows":[]}\n')
    with pytest.raises(ValueError, match="altered"):
        calendar_source.export(tmp_path.resolve())


def test_real_source_cannot_be_aliased(calendar_root: Path, tmp_path: Path) -> None:
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(calendar_root, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        calendar_source.export(alias)


def test_public_rr_export_matches_original_cli(
    calendar_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    records = calendar_source.export(calendar_root)
    assert [record["id"] for record in records] == [
        f"cal-rx-w{world:02d}-d{direction}-r{repetition}-blind"
        for world in (0, 4, 8)
        for direction in range(2)
        for repetition in range(2)
    ]
    published = sha256((calendar_root / _REPORT).read_bytes()).hexdigest()
    assert {record["peer"]["source_sha256"] for record in records} == {published}
    assert {record["peer"]["response_sha256"] for record in records} == {None}
    assert len({record["peer"]["run"] for record in records}) == 12
    raw = subprocess.run(
        [
            sys.executable,
            "-S",
            "-B",
            "-m",
            "examples.seatlab.visibility.calendar_list_protocol",
            "prompt",
            "rx-w00-d0-r0-blind",
            "rr",
        ],
        cwd=calendar_root,
        env={},
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout
    source = records[0]
    assert source["prompt"].encode("utf-8") == raw
    assert source["prompt_sha256"] == sha256(raw).hexdigest()
    assert source["peer"]["run"] == "cl-w00-d0-r0-blind-k0-rr"
    assert source["peer"]["answer"] == {"slot": 24}
    assert source["rubric"]["items"] == [
        {"key": "slot", "valid": list(range(35)), "optimal": [26]}
    ]


def test_stock_check_scores_actions_not_provenance_aliases(calendar_root: Path) -> None:
    records = calendar_source.export(calendar_root)
    pack = {"schema": "seatlab.rolepack/v1", "records": records}
    first = records[0]
    samples = [
        {
            "id": f"{first['id']}:d0:{arm}",
            "input": protocol.prompt(first, arm),
            "output": text,
            "error": None,
        }
        for arm, text in (("original", '{"slot":26}'), ("peer", '{"slot":24}'))
    ]
    report = evidence.from_samples(pack, samples)
    assert calendar_source.check(calendar_root, pack, report) == {
        "actions_checked": 2,
        "optimal_actions": 1,
        "sources_checked": 1,
    }

    wrong_score = deepcopy(report)
    first_accepted = next(
        row for row in wrong_score["rows"] if row["state"] == "complete"
    )
    first_accepted["outcome"]["correct"][0] = not first_accepted["outcome"]["correct"][
        0
    ]
    with pytest.raises(ValueError, match="score"):
        calendar_source.check(calendar_root, pack, wrong_score)

    aliased = deepcopy(pack)
    aliased["records"][0]["peer"], aliased["records"][1]["peer"] = (
        aliased["records"][1]["peer"],
        aliased["records"][0]["peer"],
    )
    with pytest.raises(ValueError, match="published source"):
        calendar_source.check(
            calendar_root, aliased, evidence.from_samples(aliased, [])
        )
