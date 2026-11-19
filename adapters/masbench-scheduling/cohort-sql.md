# Public calendar cohort check

`check_cohort.sql` reads one public decision bundle. It uses only `pairs.json`, `decisions.jsonl`, and `sources.jsonl`. It does not read Harbor, SeatLab, or raw worlds. It does not lock any file.

The manifest in `pairs.json` is the denominator. This cohort has 200 registered cases. A missing arm stays missing. An explicit JSON null is a completed decision with no meeting. Do not treat a missing row as a null, and do not treat a null as a missing row.

## Run

Set the bundle folder in one of three ways. Do not edit the SQL to add a path.

1. Pass the folder to the macro.
2. Set the DuckDB variable `bundle_dir`.
3. Run with the bundle as the working directory.

CLI, from any directory:

```sh
duckdb <<'SQL'
SET VARIABLE bundle_dir = '/path/to/bundle';
.read /path/to/check_cohort.sql
SQL
```

CLI, with the bundle as the working directory:

```sh
duckdb -c ".read /path/to/check_cohort.sql"
```

Python 3 with DuckDB 1.5 or newer. The last statement is the stratum summary.

```python
import duckdb
from pathlib import Path

bundle = "/path/to/bundle"
sql = Path("check_cohort.sql").read_text()
con = duckdb.connect()
con.execute("SET VARIABLE bundle_dir = ?", [bundle])
result = None
for statement in duckdb.extract_statements(sql):
    result = con.execute(statement)
summary = result.fetchall()
```

One registered case:

```sql
SELECT *
FROM check_cohort_cases('/path/to/bundle')
WHERE case_id = 'calendar:2-1';
```

## Method

Each manifest row is one case. `source` is the full arm. `target` is the first8 arm. Count an arm as acquired only when the manifest has a trial id and the decision file has exactly one line for that key.

Score a returned choice only when that line also matches the manifest id, mode, and sha256, and the parsed fields match the stored line. This field check is a structural and content comparison. It is not a second copy of the Python serializer.

Official means the action is in `answers.optimal`. Physically feasible means the action is in `answers.feasible`. Those lists are the published answer sets. This check does not import calendars again.

The first8 prediction uses the full arm only. Take the first 8 starts from each seat report. Do not take 8 starts from a joined list. The prediction is the earliest start that is in all five prefixes. An empty intersection is an explicit null.

Compare that prediction with the native first8 action, including an explicit null. If either arm is missing, the comparison status is `missing`. Do not count that case as agreement or disagreement.

`sources.jsonl` is an identity control for the full arm. A source line must equal the matching full decision line. The source file does not replace or override `decisions.jsonl`.

The sha256 of each stored JSONL line is the published canonical byte check. The publisher wrote those lines with Python `json.dumps(sort_keys=True, separators=(",", ":"))`. DuckDB does not reimplement that serializer. `to_json` of a parsed row can match these already sorted lines. That round trip is reported as `bundle_reprint_matches`. It is not proof that DuckDB sorts keys the way Python does. The summary labels the comparison `published_line_sha256_and_structural_content`.

`integrity_ok` means the published files agree with the manifest. It does not mean every case has a trial. `observations_complete` is true only when both arms are present for every registered case. Read `full_missing` and `first8_missing` before you read a rate.

## Final cohort

The [sealed result](cohort-results.md) contains all 200 registered pairs. The SQL reproduced all 200 first8 decisions. The 185 null decisions and 15 returned choices are observations, not missing cells.

## Worked example

Case `calendar:2-1` has a full action of 245. That action is in the published optimal set and the published feasible set. The native first8 action is an explicit null.

The full reports are longer than 8 starts. The check keeps the first 8 starts of each seat:

- m1: 58, 59, 64, 65, 66, 84, 85, 91
- m2: 50, 51, 54, 55, 56, 57, 58, 59
- m3: 54, 55, 59, 60, 61, 62, 63, 64
- m4: 52, 53, 54, 58, 59, 60, 61, 64
- m5: 48, 53, 54, 62, 70, 84, 85, 86

Those five prefixes have no start in common. The prediction is null. It matches the native first8 null. The same prefixes equal the native first8 reports. A global first-8 cut would not build these five lists.

