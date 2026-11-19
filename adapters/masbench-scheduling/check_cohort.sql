/*
Read-only check of a public calendar decision bundle.
Files used: pairs.json, decisions.jsonl, and sources.jsonl.
No Harbor trial, SeatLab store, raw world, or file lock is read.

The manifest in pairs.json is the denominator. A missing arm stays
missing. An explicit JSON null action is a completed no-meeting
decision, not a missing trial.

Folder path, in this order:
  1. table-macro argument
  2. DuckDB variable bundle_dir
  3. the process working directory

CLI, any working directory:
  duckdb <<'SQL'
  SET VARIABLE bundle_dir = '/path/to/bundle';
  .read check_cohort.sql
  SQL

CLI, bundle as the working directory:
  duckdb -c ".read /path/to/check_cohort.sql"

Python (duckdb 1.5+). The last statement is the stratum summary:
  con.execute("SET VARIABLE bundle_dir = ?", [bundle])
  result = None
  for statement in duckdb.extract_statements(sql):
      result = con.execute(statement)
  summary = result.fetchdf()

One registered case, including the per-seat prefixes:
  SELECT * FROM check_cohort_cases('/path/to/bundle')
  WHERE case_id = 'calendar:2-1'

Method:
  Score a returned choice only when that arm has exactly one decision
  line and the line matches the manifest id, mode, and sha256.
  Official means the action is in answers.optimal. Physically feasible
  means the action is in answers.feasible. Those are the published
  answer lists, not a new calendar import.
  The first8 prediction truncates each full seat report to its own
  first 8 starts, then takes the earliest start present in all five
  prefixes. An empty intersection is an explicit null. It is not the
  first 8 starts of a concatenated list.
  sources.jsonl is an identity control for the full arm. It does not
  override decisions.jsonl.
  sha256 of each stored JSONL line is the published canonical byte
  check. DuckDB does not reimplement Python
  json.dumps(sort_keys=True, separators=(",", ":")). Field equality is
  labeled structural_content. A to_json round trip is reported
  separately and is not that Python serializer.
*/

CREATE OR REPLACE MACRO check_cohort_cases(bundle_dir) AS TABLE
WITH manifest_doc AS (
    SELECT schema AS pair_schema, pairs
    FROM read_json(rtrim(bundle_dir, '/') || '/pairs.json')
),
manifest AS (
    SELECT
        p.id AS case_id,
        split_part(split_part(p.id, ':', 2), '-', 1) AS stratum_raw,
        p.source.case_sha256 AS source_case_sha256,
        p.target.case_sha256 AS target_case_sha256,
        p.source.key AS full_key,
        p.target.key AS first8_key,
        p.source.native_trial_id AS full_uuid,
        p.target.native_trial_id AS first8_uuid,
        p.source.decision_sha256 AS full_sha,
        p.target.decision_sha256 AS first8_sha
    FROM manifest_doc, unnest(pairs) AS t(p)
),
decision_lines AS (
    SELECT
        line,
        sha256(line) AS line_sha,
        json_extract_string(line, '$.key') AS key
    FROM (
        SELECT unnest(string_split(content, chr(10))) AS line
        FROM read_text(rtrim(bundle_dir, '/') || '/decisions.jsonl')
    )
    WHERE line <> ''
),
source_lines AS (
    SELECT
        line,
        sha256(line) AS line_sha,
        json_extract_string(line, '$.key') AS key
    FROM (
        SELECT unnest(string_split(content, chr(10))) AS line
        FROM read_text(rtrim(bundle_dir, '/') || '/sources.jsonl')
    )
    WHERE line <> ''
),
decision_one AS (
    SELECT
        key,
        count(*) AS line_count,
        min(line) AS line,
        min(line_sha) AS line_sha
    FROM decision_lines
    GROUP BY key
),
source_one AS (
    SELECT
        key,
        count(*) AS line_count,
        min(line) AS line,
        min(line_sha) AS line_sha
    FROM source_lines
    GROUP BY key
),
typed AS (
    SELECT
        d.key,
        d.schema AS decision_schema,
        d.native_trial_id,
        d.basis,
        d.role,
        d.observed.action AS action,
        d.view.mode AS mode,
        d.view.reports.m1 AS m1,
        d.view.reports.m2 AS m2,
        d.view.reports.m3 AS m3,
        d.view.reports.m4 AS m4,
        d.view.reports.m5 AS m5,
        d.answers.optimal AS optimal,
        d.answers.feasible AS feasible,
        d.answers.include_null AS include_null,
        to_json(d) AS reprinted
    FROM read_json(
        rtrim(bundle_dir, '/') || '/decisions.jsonl',
        format = 'newline_delimited'
    ) AS d
),
typed_one AS (
    SELECT key
    FROM typed
    GROUP BY key
    HAVING count(*) = 1
),
full_arm AS (
    SELECT
        m.case_id,
        m.full_key AS arm_key,
        m.full_uuid AS manifest_uuid,
        m.full_sha AS manifest_sha,
        'full' AS expected_mode,
        d.line_count,
        d.line,
        d.line_sha,
        t.decision_schema,
        t.native_trial_id,
        t.basis,
        t.role,
        t.action,
        t.mode,
        t.m1,
        t.m2,
        t.m3,
        t.m4,
        t.m5,
        t.optimal,
        t.feasible,
        t.include_null,
        t.reprinted
    FROM manifest AS m
    LEFT JOIN decision_one AS d ON d.key = m.full_key
    LEFT JOIN typed_one AS u ON u.key = m.full_key
    LEFT JOIN typed AS t ON t.key = u.key
),
first8_arm AS (
    SELECT
        m.case_id,
        m.first8_key AS arm_key,
        m.first8_uuid AS manifest_uuid,
        m.first8_sha AS manifest_sha,
        'first8' AS expected_mode,
        d.line_count,
        d.line,
        d.line_sha,
        t.decision_schema,
        t.native_trial_id,
        t.basis,
        t.role,
        t.action,
        t.mode,
        t.m1,
        t.m2,
        t.m3,
        t.m4,
        t.m5,
        t.optimal,
        t.feasible,
        t.include_null,
        t.reprinted
    FROM manifest AS m
    LEFT JOIN decision_one AS d ON d.key = m.first8_key
    LEFT JOIN typed_one AS u ON u.key = m.first8_key
    LEFT JOIN typed AS t ON t.key = u.key
)
,
arms AS (
    SELECT * FROM full_arm
    UNION ALL
    SELECT * FROM first8_arm
)
,
scored AS (
    SELECT
        *,
        coalesce(manifest_uuid IS NOT NULL AND line_count = 1, false) AS acquired,
        coalesce(
            manifest_uuid IS NOT NULL
                AND line_count = 1
                AND line_sha = manifest_sha
                AND native_trial_id = manifest_uuid
                AND mode = expected_mode
                AND decision_schema = 'seatlab.decision/v1'
                AND basis = 'bound'
                AND role = 'm1'
                AND include_null IS TRUE
                AND optimal IS NOT NULL
                AND feasible IS NOT NULL
                AND m1 IS NOT NULL
                AND m2 IS NOT NULL
                AND m3 IS NOT NULL
                AND m4 IS NOT NULL
                AND m5 IS NOT NULL
                AND json_extract_string(line, '$.key') = arm_key
                AND json_extract_string(line, '$.native_trial_id') = manifest_uuid::VARCHAR
                AND json_extract_string(line, '$.view.mode') = expected_mode
                AND (
                    (
                        json_type(json_extract(line, '$.observed.action')) = 'NULL'
                        AND action IS NULL
                    )
                    OR try_cast(json_extract(line, '$.observed.action') AS BIGINT) = action
                )
                AND json_extract(line, '$.view.reports.m1')::BIGINT[] = m1
                AND json_extract(line, '$.view.reports.m2')::BIGINT[] = m2
                AND json_extract(line, '$.view.reports.m3')::BIGINT[] = m3
                AND json_extract(line, '$.view.reports.m4')::BIGINT[] = m4
                AND json_extract(line, '$.view.reports.m5')::BIGINT[] = m5,
            false
        ) AS identity_ok
    FROM arms
)
,
full_scored AS (
    SELECT * FROM scored WHERE expected_mode = 'full'
)
,
first8_scored AS (
    SELECT * FROM scored WHERE expected_mode = 'first8'
)
,
predicted AS (
    SELECT
        f.case_id,
        list_slice(f.m1, 1, 8) AS p1,
        list_slice(f.m2, 1, 8) AS p2,
        list_slice(f.m3, 1, 8) AS p3,
        list_slice(f.m4, 1, 8) AS p4,
        list_slice(f.m5, 1, 8) AS p5,
        list_min(
            list_filter(
                list_slice(f.m1, 1, 8),
                x -> list_contains(list_slice(f.m2, 1, 8), x)
                    AND list_contains(list_slice(f.m3, 1, 8), x)
                    AND list_contains(list_slice(f.m4, 1, 8), x)
                    AND list_contains(list_slice(f.m5, 1, 8), x)
            )
        ) AS predicted_action
    FROM full_scored AS f
    WHERE f.identity_ok
)
,
joined AS (
    SELECT
        m.case_id,
        CASE
            WHEN m.stratum_raw IN ('2', '3', '4', '5') THEN m.stratum_raw
            ELSE 'other'
        END AS stratum,
        m.full_uuid,
        m.first8_uuid,
        coalesce(
            m.source_case_sha256 IS NOT NULL
                AND m.source_case_sha256 = m.target_case_sha256,
            false
        ) AS case_sha_equal,
        f.acquired AS full_acquired,
        t.acquired AS first8_acquired,
        f.identity_ok AS full_identity_ok,
        t.identity_ok AS first8_identity_ok,
        (m.full_uuid IS NOT NULL AND NOT f.acquired) AS full_gap,
        (m.first8_uuid IS NOT NULL AND NOT t.acquired) AS first8_gap,
        CASE
            WHEN NOT f.acquired THEN 'missing'
            WHEN NOT f.identity_ok THEN 'unverified'
            WHEN f.action IS NULL THEN 'null'
            ELSE 'value'
        END AS full_action_status,
        CASE
            WHEN NOT t.acquired THEN 'missing'
            WHEN NOT t.identity_ok THEN 'unverified'
            WHEN t.action IS NULL THEN 'null'
            ELSE 'value'
        END AS first8_action_status,
        CASE WHEN f.identity_ok THEN f.action END AS full_action,
        CASE WHEN t.identity_ok THEN t.action END AS first8_action,
        CASE WHEN f.identity_ok THEN p.predicted_action END AS predicted_action,
        CASE WHEN f.identity_ok THEN p.p1 END AS full_prefix_m1,
        CASE WHEN f.identity_ok THEN p.p2 END AS full_prefix_m2,
        CASE WHEN f.identity_ok THEN p.p3 END AS full_prefix_m3,
        CASE WHEN f.identity_ok THEN p.p4 END AS full_prefix_m4,
        CASE WHEN f.identity_ok THEN p.p5 END AS full_prefix_m5,
        coalesce(
            f.identity_ok AND f.action IS NOT NULL AND list_contains(f.optimal, f.action),
            false
        ) AS full_official,
        coalesce(
            t.identity_ok AND t.action IS NOT NULL AND list_contains(t.optimal, t.action),
            false
        ) AS first8_official,
        coalesce(
            f.identity_ok AND f.action IS NOT NULL AND list_contains(f.feasible, f.action),
            false
        ) AS full_physically_feasible,
        coalesce(
            t.identity_ok AND t.action IS NOT NULL AND list_contains(t.feasible, t.action),
            false
        ) AS first8_physically_feasible,
        coalesce(
            f.identity_ok
                AND t.identity_ok
                AND p.p1 = t.m1
                AND p.p2 = t.m2
                AND p.p3 = t.m3
                AND p.p4 = t.m4
                AND p.p5 = t.m5,
            false
        ) AS prefix_reports_equal,
        CASE
            WHEN NOT f.acquired OR NOT t.acquired THEN 'missing'
            WHEN NOT f.identity_ok OR NOT t.identity_ok THEN 'unverified'
            WHEN p.predicted_action IS NOT DISTINCT FROM t.action THEN 'agree'
            ELSE 'disagree'
        END AS truncation_status
    FROM manifest AS m
    JOIN full_scored AS f ON f.case_id = m.case_id
    JOIN first8_scored AS t ON t.case_id = m.case_id
    LEFT JOIN predicted AS p ON p.case_id = m.case_id
)
SELECT
    j.*,
    (j.full_action_status = 'null') AS full_explicit_null,
    (j.first8_action_status = 'null') AS first8_explicit_null,
    (j.full_action_status = 'value') AS full_returned,
    (j.first8_action_status = 'value') AS first8_returned,
    (j.truncation_status = 'agree') AS truncation_agree,
    (j.truncation_status = 'disagree') AS truncation_disagree,
    (j.truncation_status = 'missing') AS truncation_missing,
    (j.truncation_status = 'unverified') AS truncation_unverified,
    (SELECT pair_schema = 'seatlab.calibration/v1' FROM manifest_doc) AS pair_schema_ok,
    (SELECT count(*) FROM decision_lines) AS bundle_decision_lines,
    (SELECT count(*) FROM source_lines) AS bundle_source_lines,
    (
        SELECT count(*)
        FROM decision_lines
        WHERE json_extract_string(line, '$.view.mode') = 'full'
    ) AS bundle_full_decision_lines,
    (
        SELECT count(*)
        FROM source_one AS s
        JOIN decision_one AS d
            ON d.key = s.key
            AND d.line = s.line
            AND d.line_count = 1
            AND s.line_count = 1
    ) AS bundle_source_byte_matches,
    (
        SELECT count(*)
        FROM typed AS tv
        JOIN typed_one AS u ON u.key = tv.key
        JOIN decision_one AS d ON d.key = tv.key AND d.line_count = 1
        WHERE tv.reprinted = d.line
    ) AS bundle_reprint_matches,
    (
        SELECT count(*)
        FROM decision_one AS d
        WHERE d.key NOT IN (SELECT full_key FROM manifest)
            AND d.key NOT IN (SELECT first8_key FROM manifest)
    ) AS bundle_extra_decision_keys,
    (
        SELECT count(*)
        FROM (
            SELECT native_trial_id
            FROM (
                SELECT full_uuid AS native_trial_id
                FROM manifest
                WHERE full_uuid IS NOT NULL
                UNION ALL
                SELECT first8_uuid
                FROM manifest
                WHERE first8_uuid IS NOT NULL
            )
            GROUP BY native_trial_id
            HAVING count(*) > 1
        )
    ) AS bundle_duplicate_uuids
FROM joined AS j;

CREATE OR REPLACE MACRO check_cohort(bundle_dir) AS TABLE
WITH cases AS (
    SELECT * FROM check_cohort_cases(bundle_dir)
),
acquired_ids AS (
    SELECT stratum, full_uuid AS uuid
    FROM cases
    WHERE full_acquired
    UNION ALL
    SELECT stratum, first8_uuid
    FROM cases
    WHERE first8_acquired
),
by_stratum AS (
    SELECT
        c.stratum,
        count(*) AS registered,
        count(*) FILTER (WHERE c.full_acquired) AS full_acquired,
        count(*) FILTER (WHERE c.first8_acquired) AS first8_acquired,
        count(*) FILTER (WHERE c.full_uuid IS NULL) AS full_missing,
        count(*) FILTER (WHERE c.first8_uuid IS NULL) AS first8_missing,
        count(*) FILTER (WHERE c.full_gap) AS full_gap,
        count(*) FILTER (WHERE c.first8_gap) AS first8_gap,
        count(*) FILTER (WHERE c.full_explicit_null) AS full_explicit_null,
        count(*) FILTER (WHERE c.first8_explicit_null) AS first8_explicit_null,
        count(*) FILTER (WHERE c.full_returned) AS full_returned,
        count(*) FILTER (WHERE c.first8_returned) AS first8_returned,
        count(*) FILTER (WHERE c.full_official) AS full_official,
        count(*) FILTER (WHERE c.first8_official) AS first8_official,
        count(*) FILTER (WHERE c.full_physically_feasible) AS full_physically_feasible,
        count(*) FILTER (WHERE c.first8_physically_feasible) AS first8_physically_feasible,
        count(*) FILTER (WHERE c.truncation_agree) AS truncation_agree,
        count(*) FILTER (WHERE c.truncation_disagree) AS truncation_disagree,
        count(*) FILTER (WHERE c.truncation_missing) AS truncation_missing,
        count(*) FILTER (WHERE c.truncation_unverified) AS truncation_unverified,
        count(*) FILTER (WHERE c.prefix_reports_equal) AS prefix_reports_equal,
        count(*) FILTER (WHERE c.full_identity_ok) AS full_identity_ok,
        count(*) FILTER (WHERE c.first8_identity_ok) AS first8_identity_ok,
        count(*) FILTER (WHERE NOT c.case_sha_equal) AS case_sha_mismatches,
        coalesce(u.distinct_uuids, 0) AS distinct_uuids
    FROM cases AS c
    LEFT JOIN (
        SELECT stratum, count(DISTINCT uuid) AS distinct_uuids
        FROM acquired_ids
        GROUP BY stratum
    ) AS u ON u.stratum = c.stratum
    GROUP BY c.stratum, u.distinct_uuids
),
overall AS (
    SELECT
        'all' AS stratum,
        count(*) AS registered,
        count(*) FILTER (WHERE full_acquired) AS full_acquired,
        count(*) FILTER (WHERE first8_acquired) AS first8_acquired,
        count(*) FILTER (WHERE full_uuid IS NULL) AS full_missing,
        count(*) FILTER (WHERE first8_uuid IS NULL) AS first8_missing,
        count(*) FILTER (WHERE full_gap) AS full_gap,
        count(*) FILTER (WHERE first8_gap) AS first8_gap,
        count(*) FILTER (WHERE full_explicit_null) AS full_explicit_null,
        count(*) FILTER (WHERE first8_explicit_null) AS first8_explicit_null,
        count(*) FILTER (WHERE full_returned) AS full_returned,
        count(*) FILTER (WHERE first8_returned) AS first8_returned,
        count(*) FILTER (WHERE full_official) AS full_official,
        count(*) FILTER (WHERE first8_official) AS first8_official,
        count(*) FILTER (WHERE full_physically_feasible) AS full_physically_feasible,
        count(*) FILTER (WHERE first8_physically_feasible) AS first8_physically_feasible,
        count(*) FILTER (WHERE truncation_agree) AS truncation_agree,
        count(*) FILTER (WHERE truncation_disagree) AS truncation_disagree,
        count(*) FILTER (WHERE truncation_missing) AS truncation_missing,
        count(*) FILTER (WHERE truncation_unverified) AS truncation_unverified,
        count(*) FILTER (WHERE prefix_reports_equal) AS prefix_reports_equal,
        count(*) FILTER (WHERE full_identity_ok) AS full_identity_ok,
        count(*) FILTER (WHERE first8_identity_ok) AS first8_identity_ok,
        count(*) FILTER (WHERE NOT case_sha_equal) AS case_sha_mismatches,
        (SELECT count(DISTINCT uuid) FROM acquired_ids) AS distinct_uuids
    FROM cases
),
rolled AS (
    SELECT * FROM by_stratum
    UNION ALL
    SELECT * FROM overall
),
shape AS (
    SELECT
        count(*) = 200
            AND count(*) FILTER (WHERE stratum = '2') = 40
            AND count(*) FILTER (WHERE stratum = '3') = 60
            AND count(*) FILTER (WHERE stratum = '4') = 60
            AND count(*) FILTER (WHERE stratum = '5') = 40
            AND count(*) FILTER (WHERE stratum = 'other') = 0 AS stratum_shape_ok
    FROM cases
)
SELECT
    r.stratum,
    r.registered,
    r.full_acquired,
    r.first8_acquired,
    r.full_missing,
    r.first8_missing,
    r.full_gap,
    r.first8_gap,
    r.distinct_uuids,
    r.full_explicit_null,
    r.first8_explicit_null,
    r.full_returned,
    r.first8_returned,
    r.full_official,
    r.first8_official,
    r.full_physically_feasible,
    r.first8_physically_feasible,
    r.truncation_agree,
    r.truncation_disagree,
    r.truncation_missing,
    r.truncation_unverified,
    r.prefix_reports_equal,
    r.full_identity_ok,
    r.first8_identity_ok,
    r.case_sha_mismatches,
    CASE WHEN r.stratum = 'all' THEN s.stratum_shape_ok END AS bundle_stratum_shape_ok,
    CASE WHEN r.stratum = 'all' THEN c.pair_schema_ok END AS pair_schema_ok,
    CASE WHEN r.stratum = 'all' THEN c.bundle_decision_lines END AS bundle_decision_lines,
    CASE WHEN r.stratum = 'all' THEN c.bundle_source_lines END AS bundle_source_lines,
    CASE
        WHEN r.stratum = 'all' THEN c.bundle_source_byte_matches = c.bundle_full_decision_lines
            AND c.bundle_source_lines = c.bundle_full_decision_lines
    END AS bundle_source_equals_full_decisions,
    CASE WHEN r.stratum = 'all' THEN c.bundle_duplicate_uuids END AS bundle_duplicate_uuids,
    CASE WHEN r.stratum = 'all' THEN c.bundle_extra_decision_keys END AS bundle_extra_decision_keys,
    CASE WHEN r.stratum = 'all' THEN c.bundle_reprint_matches END AS bundle_reprint_matches,
    CASE
        WHEN r.stratum = 'all' THEN 'published_line_sha256_and_structural_content'
    END AS canonical_comparison,
    CASE
        WHEN r.stratum = 'all' THEN s.stratum_shape_ok
            AND c.pair_schema_ok
            AND r.full_gap = 0
            AND r.first8_gap = 0
            AND r.full_identity_ok = r.full_acquired
            AND r.first8_identity_ok = r.first8_acquired
            AND r.case_sha_mismatches = 0
            AND c.bundle_duplicate_uuids = 0
            AND c.bundle_extra_decision_keys = 0
            AND c.bundle_source_byte_matches = c.bundle_full_decision_lines
            AND c.bundle_source_lines = c.bundle_full_decision_lines
    END AS integrity_ok,
    CASE
        WHEN r.stratum = 'all' THEN r.full_missing = 0 AND r.first8_missing = 0
    END AS observations_complete
FROM rolled AS r
CROSS JOIN shape AS s
CROSS JOIN (
    SELECT
        pair_schema_ok,
        bundle_decision_lines,
        bundle_source_lines,
        bundle_full_decision_lines,
        bundle_source_byte_matches,
        bundle_reprint_matches,
        bundle_extra_decision_keys,
        bundle_duplicate_uuids
    FROM cases
    LIMIT 1
) AS c
ORDER BY CASE r.stratum
    WHEN '2' THEN 2
    WHEN '3' THEN 3
    WHEN '4' THEN 4
    WHEN '5' THEN 5
    WHEN 'other' THEN 8
    ELSE 9
END;

SELECT * FROM check_cohort(coalesce(getvariable('bundle_dir'), '.'));
