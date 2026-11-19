-- Run from the extracted native-calendar-prefix-24 release archive with DuckDB.
-- This is an independent counting implementation, not a native receipt validator.
-- The source tape uses canonical JSON object order. Keep JSON null distinct from absence.
WITH episodes AS (
    SELECT i.case_id, e.json AS episode
    FROM read_json_auto('source/index.json') AS i
    JOIN read_json_objects('source/episodes.jsonl', format = 'newline_delimited') AS e
      ON (e.json->>'id') = i.episode_id AND (e.json->>'native_trial_id') = i.native_trial_id
    WHERE i.status = 'verified'
), eligible AS (
    SELECT split_part(case_id, '/', 2)::INTEGER <= 4 AS fit,
           episode->>'role' AS role, f.value->'view' AS v,
           (f.value->'observed')::VARCHAR AS action
    FROM episodes, LATERAL json_each(episode, '$.frames') AS f
    WHERE (f.value->>'basis') = 'bound' AND json_type(f.value, '$.observed') = 'OBJECT'
), fields AS (
    SELECT *,
       CASE WHEN json_exists(v, '$.round') THEN '[true,' || (v->'round')::VARCHAR || ']' ELSE '[false]' END AS r,
       CASE WHEN json_exists(v, '$.bound') THEN '[true,' || (v->'bound')::VARCHAR || ']' ELSE '[false]' END AS b,
       CASE WHEN json_exists(v, '$.replies') THEN '[true,' || (v->'replies')::VARCHAR || ']' ELSE '[false]' END AS m
    FROM eligible
), inputs AS (
    SELECT fit, role, action, p.representation, p.input
    FROM fields, LATERAL (VALUES
        ('full', v::VARCHAR), ('role_only', '[]'), ('round', '[' || r || ']'),
        ('bound', '[' || b || ']'), ('replies', '[' || m || ']'),
        ('exchange', '[' || b || ',' || m || ',' || r || ']')
    ) AS p(representation, input)
), votes AS (
    SELECT representation, role, input, action, count(*) AS n
    FROM inputs WHERE fit GROUP BY ALL
), ranked AS (
    SELECT *, max(n) OVER (PARTITION BY representation, role, input) AS top_n FROM votes
), groups AS (
    SELECT representation, role, input, sum(n) AS frames, count(*) AS actions,
           max(top_n) AS best, count(*) FILTER (WHERE n = top_n) AS top_actions,
           min(action) FILTER (WHERE n = top_n) AS top_action
    FROM ranked GROUP BY representation, role, input
), fitted AS (
    SELECT representation, count(*) AS classes, sum(frames)::BIGINT AS fit_frames,
           count(*) FILTER (WHERE frames > 1) AS repeated_classes,
           coalesce(sum(frames) FILTER (WHERE frames > 1), 0)::BIGINT AS repeated_frames,
           count(*) FILTER (WHERE actions > 1) AS conflicting_classes,
           sum(frames - best)::BIGINT AS minimum_errors, sum(best)::BIGINT AS maximum_matches
    FROM groups GROUP BY representation
), tested AS (
    SELECT i.representation, count(*) AS heldout_frames,
           count(*) FILTER (WHERE g.top_actions IS NULL) AS unseen,
           count(*) FILTER (WHERE g.top_actions > 1) AS tied,
           count(*) FILTER (WHERE g.top_actions = 1 AND i.action = g.top_action) AS matched,
           count(*) FILTER (WHERE g.top_actions = 1 AND i.action != g.top_action) AS disagreed
    FROM inputs AS i LEFT JOIN groups AS g USING (representation, role, input)
    WHERE NOT i.fit GROUP BY i.representation
)
SELECT * FROM fitted JOIN tested USING (representation) ORDER BY representation;
