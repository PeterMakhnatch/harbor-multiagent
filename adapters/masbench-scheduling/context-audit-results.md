# Which context fields distinguish a scheduling coordinator's actions?

**Round number alone cannot reproduce the recorded behavior.** Even the best deterministic lookup using that field must disagree on at least 237 of 321 fit decisions. Keeping all 5 participant replies gives zero contradictions on the fit cases. It does not give a transferable lookup: 168 of 176 held-out reply inputs never appeared in the fit set.

We used the [prespecified split and representations](context-audit-protocol.md) on 24 real five-participant Olam Harbor trials. Sixteen cases supplied 321 accepted coordinator decisions for fit; 8 separate cases supplied 176 for held-out scoring. Both sets had 0 unbound or missing decisions. No case or trial UUID appears in both. The audit reads the coordinator's acknowledged pre-action view. It does not read the reference meeting time, a future view, or a reward.

| Current role view | Fit classes | Fit classes seen more than once | Minimum fit errors / 321 | Held-out matched | Disagreed | Tied | Unseen |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full | 321 | 0 | 0 | 0 | 0 | 0 | 176 |
| Role only | 1 | 1 | 305 | 8 | 168 | 0 | 0 |
| Round | 50 | 32 | 237 | 9 | 89 | 78 | 0 |
| Request bound | 154 | 79 | 132 | 19 | 65 | 65 | 27 |
| Participant replies | 305 | 2 | 0 | 8 | 0 | 0 | 168 |
| Round, bound and replies | 306 | 1 | 0 | 8 | 0 | 0 | 168 |

The bound is exact on the fit sample: for each identical input within a role, subtract the largest action count from the group size. The held-out lookup predicts only a unique most-frequent fit action. Unseen and tied keys are abstentions, not errors or correct predictions. All 8 held-out matches in the replies-only and exchange rows are the initial `query:0` decisions. There are **no matched held-out post-reply decisions** for either lookup. A rule that computes the maximum of new replies can generalize where an exact lookup cannot. Zero collisions do not prove the retained fields suffice on a new case.

## Reproduce without Harbor, Docker or a model

1. Download and extract [`native-calendar-prefix-24.tar.gz`](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.3.0/native-calendar-prefix-24.tar.gz). The archive includes both arms, the original MIT notice and validation receipts. This analysis uses **only** `source/`.
2. Install a SeatLab version that supplies `seatlab context-audit`. Run `python adapters/masbench-scheduling/context_study.py /path/to/source --out study.json`. The tool refuses to overwrite an existing file.
3. For an independent implementation, run DuckDB from the extracted archive directory: `duckdb -c '.read /path/to/context_audit_check.sql'`. DuckDB 1.5.6 reproduced all 12 reported count fields for all 6 representations. This second check groups the public data in SQL; it does not replace native receipt verification.

The [machine-readable results](results/context-audit.json) include source file digests, case lists, exclusions and bounded receipts for contradictory fit groups. [Study code](context_study.py) · [independent SQL](context_audit_check.sql) · [native source study](interactive-results.md).
