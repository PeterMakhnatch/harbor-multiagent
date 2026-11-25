# Seams pilot forecast

**No model run. No spend approval. Preview plans only.**

PREVIEW ONLY. This is the maximum envelope, not an executable staged wave.
Stage A: 10 distinct instances per model, level and control arm; require at least 9 full successes in each arm.
Drop a level for a model if either control fails. Invalid or missing cells are not failures; retry once, then stop if incomplete.
Stage B: at the first passing level, run each matched probe pair on 12 fresh instances per model.
Use exact sequential McNemar at 3, 6 and 12 pairs, alpha 0.05, Holm over pairs within model.
At least 2 control-only successes in a pair for at least one model are required to continue.
Ensure 12 valid pairs before declaring a ceiling. An early efficacy stop with at least 2 control-only successes meets the signal gate;
a statistical futility stop does not establish a ceiling at this descriptive threshold. Continue the screen if 2 is still reachable.
If no pair meets the gate, report 'at the ceiling for this model class at this level' and try the next level that passed A.
Do not advance a model whose control gate failed. If no eligible level remains, stop the family.
Stage C: on fresh matched instances at the first signal level, compare team to team-fullrecord when present, otherwise solo.
Include all models that passed A at that level, even if only one model supplied the B signal.
Include hint and solo-seam when present. Use the smallest positive lapse effect from the card consistent with B;
choose n before C, at most 100, for 80% power at alpha 0.05. Use fixed-sample exact McNemar on full success.
A capped sample can have less than 80% power. If no card alternative fits, stop and revise the power plan before C.
A family without probe pairs has no attribution gate: week is only a descriptive demonstration (no headline claim).
Manual hard stops: do NOT run this entire preview. Split into separately committed stage/model/level plans after each gate;
rebuild with a fresh private seed, QA, oracle parity, and approval before paid work. Apply cumulative stage caps.
The runner has no cross-set eligibility gates, stage-specific samples, conditional sample sizing, or operator pause.
Repeat on Terminus 2 and one native command-line harness before claiming an effect; record the delivery mode.
Matched-token and cross-model team controls, and 5 human baselines, are required before publication.
The target result is a boundary cost on the same work, with a similar cost for self and teammate transfer;
hint recovers less than one third and a harness mechanism recovers at least two thirds. This pilot alone cannot establish that.
Kill if standard team reaches 0.9 of control and hint closes the gap, the effect reverses by harness, or heavy solo is below 0.9.
For digest heavy, require at least 0.95 solo success: 10/10 at this pilot size.

## Forecast assumptions

- All eligible levels and models are included in the maximum envelope; actual B checks levels in order. C uses the first signal level and every model that passed A there. A, B and C use disjoint instance IDs.
- For B, b is control-only full success and c is trap-only full success. For C use the smallest positive lapse alternative not rejected by equal-tailed exact binomial tests of b and c at 0.025 each. The card's paired n is an estimate, not observed model power; probe effect may not transfer to team.
- Each row is a maximum count (no early stop, no retries). The cap is the cumulative model-only high estimate rounded up to USD, unless overridden. Sandbox spend is outside the model cap.
- Modal is $0.0021/trial (measured 2026-11-24); scripted seats have no model cost. Costs include sandbox spend, not invalid retries or second-harness replication.
- Profile simulation: first 3 instances per level and arm, careful policy, public disposable seed 1, schedule seed 0. Playtest rows override matching arm/role only; other rows use turn estimates.
- One homogeneous model per trial; Terminus 2, max 100 turns, Modal, concurrency 10, starts spaced 5 s. Turn estimates extrapolate probe history; model cache and turn costs may differ. No assumed compaction.
- The runner has a single agent and roster design; the second harness and cross-model and matched-token arms need separate approved plans. A second pass at the same counts and profile would double these totals.
- The staged runner's `Outcomes` refuses non-`probe` families even when dry-run accepts the syntax. For Seams B, apply `stages.SequentialTest` and count complete pairs from event scores manually.
- Low/high = model estimate divided/multiplied by uncertainty, plus fixed sandbox cost. Not a confidence interval.
- Rebuild using private seeds before real runs. The current built sets are for forecast and simulation only.

## commit

Plan: `commit.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $703.00. Profiles (uncertainty ×2):

- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $3.1269 / $6.2329 / $12.4448 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $1.5740 / $3.1269 / $6.2329 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $4.7009 / $9.3598 / $18.6777 |
| A | heavy | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $3.1397 / $6.2583 / $12.4957 |
| A | heavy | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $1.5803 / $3.1397 / $6.2583 |
| A | heavy | solo | **Total** | 20 | 20 / 20 | $4.7200 / $9.3980 / $18.7540 |
| B | standard | probe-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7523 / $7.4794 / $14.9337 |
| B | standard | probe-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8888 / $3.7523 / $7.4794 |
| B | standard | probe-control | **Total** | 24 | 24 / 48 | $5.6411 / $11.2317 / $22.4131 |
| B | standard | probe-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7523 / $7.4794 / $14.9337 |
| B | standard | probe-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8888 / $3.7523 / $7.4794 |
| B | standard | probe-trap | **Total** | 24 | 24 / 48 | $5.6411 / $11.2317 / $22.4131 |
| B | heavy | probe-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7531 / $7.4809 / $14.9366 |
| B | heavy | probe-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8891 / $3.7531 / $7.4809 |
| B | heavy | probe-control | **Total** | 24 | 24 / 48 | $5.6422 / $11.2340 / $22.4176 |
| B | heavy | probe-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7531 / $7.4809 / $14.9366 |
| B | heavy | probe-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8891 / $3.7531 / $7.4809 |
| B | heavy | probe-trap | **Total** | 24 | 24 / 48 | $5.6422 / $11.2340 / $22.4176 |
| C | standard | hint | anthropic/claude-opus-5.5 | 46 | 46 / 92 | $14.4075 / $28.7183 / $57.3401 |
| C | standard | hint | openai/gpt-6.2-sol | 46 | 46 / 92 | $7.2520 / $14.4075 / $28.7183 |
| C | standard | hint | **Total** | 92 | 92 / 184 | $21.6595 / $43.1258 / $86.0584 |
| C | standard | solo | anthropic/claude-opus-5.5 | 46 | 46 / 46 | $14.3840 / $28.6713 / $57.2460 |
| C | standard | solo | openai/gpt-6.2-sol | 46 | 46 / 46 | $7.2403 / $14.3840 / $28.6713 |
| C | standard | solo | **Total** | 92 | 92 / 92 | $21.6242 / $43.0553 / $85.9173 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 46 | 46 / 46 | $14.3858 / $28.6750 / $57.2533 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 46 | 46 / 46 | $7.2412 / $14.3858 / $28.6750 |
| C | standard | solo-seam | **Total** | 92 | 92 / 92 | $21.6270 / $43.0607 / $85.9283 |
| C | standard | team | anthropic/claude-opus-5.5 | 46 | 92 / 92 | $14.5516 / $29.0067 / $57.9167 |
| C | standard | team | openai/gpt-6.2-sol | 46 | 92 / 92 | $7.3241 / $14.5516 / $29.0067 |
| C | standard | team | **Total** | 92 | 184 / 184 | $21.8757 / $43.5583 / $86.9234 |
| C | heavy | hint | anthropic/claude-opus-5.5 | 31 | 31 / 62 | $9.7095 / $19.3539 / $38.6427 |
| C | heavy | hint | openai/gpt-6.2-sol | 31 | 31 / 62 | $4.8873 / $9.7095 / $19.3539 |
| C | heavy | hint | **Total** | 62 | 62 / 124 | $14.5968 / $29.0634 / $57.9965 |
| C | heavy | solo | anthropic/claude-opus-5.5 | 31 | 31 / 31 | $9.7330 / $19.4009 / $38.7367 |
| C | heavy | solo | openai/gpt-6.2-sol | 31 | 31 / 31 | $4.8990 / $9.7330 / $19.4009 |
| C | heavy | solo | **Total** | 62 | 62 / 62 | $14.6320 / $29.1339 / $58.1375 |
| C | heavy | solo-seam | anthropic/claude-opus-5.5 | 31 | 31 / 31 | $9.6949 / $19.3246 / $38.5842 |
| C | heavy | solo-seam | openai/gpt-6.2-sol | 31 | 31 / 31 | $4.8800 / $9.6949 / $19.3246 |
| C | heavy | solo-seam | **Total** | 62 | 62 / 62 | $14.5749 / $29.0195 / $57.9088 |
| C | heavy | team | anthropic/claude-opus-5.5 | 31 | 62 / 62 | $9.8074 / $19.5498 / $39.0345 |
| C | heavy | team | openai/gpt-6.2-sol | 31 | 62 / 62 | $4.9363 / $9.8074 / $19.5498 |
| C | heavy | team | **Total** | 62 | 124 / 124 | $14.7437 / $29.3572 / $58.5843 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high (stop) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 20 | 20 / 20 | $3.1543 / $6.2666 / $12.4912 | 20 | 20 / 20 | $3.1543 / $6.2666 / $12.4912 | $13.00 |
| A | anthropic/claude-opus-5.5 | 20 | 20 / 20 | $6.2666 / $12.4912 / $24.9405 | 20 | 20 / 20 | $6.2666 / $12.4912 / $24.9405 | $25.00 |
| A | all | 40 | 40 / 40 | $9.4209 / $18.7579 / $37.4317 | 40 | 40 / 40 | $9.4209 / $18.7579 / $37.4317 | $38.00 |
| B | openai/gpt-6.2-sol | 48 | 48 / 96 | $7.5558 / $15.0108 / $29.9207 | 68 | 68 / 116 | $10.7101 / $21.2774 / $42.4120 | $43.00 |
| B | anthropic/claude-opus-5.5 | 48 | 48 / 96 | $15.0108 / $29.9207 / $59.7406 | 68 | 68 / 116 | $21.2774 / $42.4120 / $84.6811 | $85.00 |
| B | all | 96 | 96 / 192 | $22.5665 / $44.9315 / $89.6613 | 136 | 136 / 232 | $31.9875 / $63.6893 / $127.0931 | $127.00 |
| C | openai/gpt-6.2-sol | 308 | 385 / 462 | $48.6602 / $96.6736 / $192.7005 | 376 | 453 / 578 | $59.3703 / $117.9510 / $235.1124 | $235.00 |
| C | anthropic/claude-opus-5.5 | 308 | 385 / 462 | $96.6736 / $192.7005 / $384.7542 | 376 | 453 / 578 | $117.9510 / $235.1124 / $469.4353 | $469.00 |
| C | all | 616 | 770 / 924 | $145.3339 / $289.3741 / $577.4546 | 752 | 906 / 1156 | $177.3213 / $353.0634 / $704.5477 | $703.00 |

Power source: `commit.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 46 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-trap lapse-10 0.167, 46; probe-trap lapse-20 0.417, 18; probe-trap lapse-30 0.542, 13; probe-trap lapse-50 0.750, 9
- heavy: 31 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-trap lapse-10 0.250, 31; probe-trap lapse-20 0.375, 20; probe-trap lapse-30 0.667, 11; probe-trap lapse-50 1.000, 6

- **standard: hint does not match the team roster.** 1/2 model/all seats versus 2/2. Costed as built; do not claim an isolated effect.
- **heavy: hint does not match the team roster.** 1/2 model/all seats versus 2/2. Costed as built; do not claim an isolated effect.

Task sets (zero-based runner index): 0=A/standard/solo; 1=A/heavy/solo; 2=B/standard/probe-control,probe-trap; 3=B/heavy/probe-control,probe-trap; 4=C/standard/solo,team,solo-seam; 5=C/standard/hint; 6=C/heavy/solo,team,solo-seam; 7=C/heavy/hint.

## digest

Plan: `digest.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $258.00. Profiles (uncertainty ×2):

- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $1.3961 / $2.7712 / $5.5214 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $0.7086 / $1.3961 / $2.7712 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $2.1047 / $4.1673 / $8.2926 |
| A | standard | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $2.2522 / $4.4834 / $8.9459 |
| A | standard | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $1.1366 / $2.2522 / $4.4834 |
| A | standard | team-fullrecord | **Total** | 20 | 80 / 80 | $3.3888 / $6.7357 / $13.4293 |
| A | heavy | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $1.4360 / $2.8511 / $5.6812 |
| A | heavy | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $0.7285 / $1.4360 / $2.8511 |
| A | heavy | solo | **Total** | 20 | 20 / 20 | $2.1646 / $4.2871 / $8.5322 |
| A | heavy | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 50 / 50 | $2.6135 / $5.2061 / $10.3912 |
| A | heavy | team-fullrecord | openai/gpt-6.2-sol | 10 | 50 / 50 | $1.3173 / $2.6135 / $5.2061 |
| A | heavy | team-fullrecord | **Total** | 20 | 100 / 100 | $3.9308 / $7.8196 / $15.5973 |
| B | standard | probe-worker-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $1.5299 / $3.0345 / $6.0439 |
| B | standard | probe-worker-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.7775 / $1.5299 / $3.0345 |
| B | standard | probe-worker-control | **Total** | 24 | 24 / 96 | $2.3074 / $4.5644 / $9.0784 |
| B | standard | probe-worker-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $1.5299 / $3.0345 / $6.0439 |
| B | standard | probe-worker-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.7775 / $1.5299 / $3.0345 |
| B | standard | probe-worker-trap | **Total** | 24 | 24 / 96 | $2.3074 / $4.5644 / $9.0784 |
| B | standard | probe-writer-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4128 / $0.8005 / $1.5758 |
| B | standard | probe-writer-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2190 / $0.4128 / $0.8005 |
| B | standard | probe-writer-control | **Total** | 24 | 24 / 48 | $0.6319 / $1.2133 / $2.3763 |
| B | standard | probe-writer-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4128 / $0.8005 / $1.5757 |
| B | standard | probe-writer-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2190 / $0.4128 / $0.8005 |
| B | standard | probe-writer-trap | **Total** | 24 | 24 / 48 | $0.6319 / $1.2133 / $2.3762 |
| B | heavy | probe-worker-control | anthropic/claude-opus-5.5 | 12 | 12 / 60 | $1.5315 / $3.0378 / $6.0503 |
| B | heavy | probe-worker-control | openai/gpt-6.2-sol | 12 | 12 / 60 | $0.7783 / $1.5315 / $3.0378 |
| B | heavy | probe-worker-control | **Total** | 24 | 24 / 120 | $2.3098 / $4.5692 / $9.0881 |
| B | heavy | probe-worker-trap | anthropic/claude-opus-5.5 | 12 | 12 / 60 | $1.5315 / $3.0378 / $6.0503 |
| B | heavy | probe-worker-trap | openai/gpt-6.2-sol | 12 | 12 / 60 | $0.7783 / $1.5315 / $3.0378 |
| B | heavy | probe-worker-trap | **Total** | 24 | 24 / 120 | $2.3098 / $4.5692 / $9.0881 |
| B | heavy | probe-writer-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4129 / $0.8006 / $1.5761 |
| B | heavy | probe-writer-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2191 / $0.4129 / $0.8006 |
| B | heavy | probe-writer-control | **Total** | 24 | 24 / 48 | $0.6320 / $1.2136 / $2.3767 |
| B | heavy | probe-writer-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4129 / $0.8006 / $1.5760 |
| B | heavy | probe-writer-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2191 / $0.4129 / $0.8006 |
| B | heavy | probe-writer-trap | **Total** | 24 | 24 / 48 | $0.6320 / $1.2135 / $2.3766 |
| C | standard | hint | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.0601 / $8.0823 / $16.1268 |
| C | standard | hint | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0489 / $4.0601 / $8.0823 |
| C | standard | hint | **Total** | 36 | 144 / 144 | $6.1090 / $12.1424 / $24.2092 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.0510 / $8.0641 / $16.0904 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0444 / $4.0510 / $8.0641 |
| C | standard | solo-seam | **Total** | 36 | 144 / 144 | $6.0953 / $12.1151 / $24.1546 |
| C | standard | team | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.0524 / $8.0669 / $16.0961 |
| C | standard | team | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0451 / $4.0524 / $8.0669 |
| C | standard | team | **Total** | 36 | 144 / 144 | $6.0975 / $12.1193 / $24.1630 |
| C | standard | team-fullrecord | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.0540 / $8.0702 / $16.1026 |
| C | standard | team-fullrecord | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0459 / $4.0540 / $8.0702 |
| C | standard | team-fullrecord | **Total** | 36 | 144 / 144 | $6.0999 / $12.1242 / $24.1728 |
| C | heavy | hint | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.9589 / $5.8947 / $11.7664 |
| C | heavy | hint | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4910 / $2.9589 / $5.8947 |
| C | heavy | hint | **Total** | 22 | 110 / 110 | $4.4499 / $8.8536 / $17.6611 |
| C | heavy | solo-seam | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.9515 / $5.8798 / $11.7365 |
| C | heavy | solo-seam | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4873 / $2.9515 / $5.8798 |
| C | heavy | solo-seam | **Total** | 22 | 110 / 110 | $4.4387 / $8.8313 / $17.6163 |
| C | heavy | team | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.9526 / $5.8822 / $11.7413 |
| C | heavy | team | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4879 / $2.9526 / $5.8822 |
| C | heavy | team | **Total** | 22 | 110 / 110 | $4.4405 / $8.8348 / $17.6235 |
| C | heavy | team-fullrecord | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.8749 / $5.7267 / $11.4303 |
| C | heavy | team-fullrecord | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4490 / $2.8749 / $5.7267 |
| C | heavy | team-fullrecord | **Total** | 22 | 110 / 110 | $4.3239 / $8.6016 / $17.1570 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high (stop) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 40 | 110 / 110 | $3.8910 / $7.6979 / $15.3118 | 40 | 110 / 110 | $3.8910 / $7.6979 / $15.3118 | $16.00 |
| A | anthropic/claude-opus-5.5 | 40 | 110 / 110 | $7.6979 / $15.3118 / $30.5396 | 40 | 110 / 110 | $7.6979 / $15.3118 / $30.5396 | $31.00 |
| A | all | 80 | 220 / 220 | $11.5889 / $23.0097 / $45.8514 | 80 | 220 / 220 | $11.5889 / $23.0097 / $45.8514 | $46.00 |
| B | openai/gpt-6.2-sol | 96 | 96 / 312 | $3.9879 / $7.7742 / $15.3468 | 136 | 206 / 422 | $7.8789 / $15.4721 / $30.6586 | $31.00 |
| B | anthropic/claude-opus-5.5 | 96 | 96 / 312 | $7.7742 / $15.3468 / $30.4920 | 136 | 206 / 422 | $15.4721 / $30.6586 / $61.0317 | $61.00 |
| B | all | 192 | 192 / 624 | $11.7621 / $23.1210 / $45.8388 | 272 | 412 / 844 | $23.3510 / $46.1307 / $91.6903 | $92.00 |
| C | openai/gpt-6.2-sol | 116 | 508 / 508 | $14.0994 / $27.9553 / $55.6670 | 252 | 714 / 930 | $21.9783 / $43.4274 / $86.3256 | $86.00 |
| C | anthropic/claude-opus-5.5 | 116 | 508 / 508 | $27.9553 / $55.6670 / $111.0904 | 252 | 714 / 930 | $43.4274 / $86.3256 / $172.1220 | $172.00 |
| C | all | 232 | 1016 / 1016 | $42.0547 / $83.6223 / $166.7573 | 504 | 1428 / 1860 | $65.4057 / $129.7530 / $258.4476 | $258.00 |

Power source: `digest.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 18 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-writer-trap lapse-10 0.417, 18; probe-worker-trap lapse-10 0.750, 9; probe-writer-trap lapse-30 0.750, 9; probe-writer-trap lapse-20 0.792, 9; probe-worker-trap lapse-20 0.958, 7; probe-worker-trap lapse-30 1.000, 6; probe-worker-trap lapse-50 1.000, 6; probe-writer-trap lapse-50 1.000, 6
- heavy: 11 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-writer-trap lapse-10 0.625, 11; probe-writer-trap lapse-20 0.708, 10; probe-worker-trap lapse-10 0.833, 8; probe-worker-trap lapse-20 0.875, 8; probe-writer-trap lapse-30 0.958, 7; probe-worker-trap lapse-30 1.000, 6; probe-worker-trap lapse-50 1.000, 6; probe-writer-trap lapse-50 1.000, 6

Task sets (zero-based runner index): 0=A/standard/solo,team-fullrecord; 1=A/heavy/solo,team-fullrecord; 2=B/standard/probe-writer-control,probe-writer-trap; 3=B/standard/probe-worker-control,probe-worker-trap; 4=B/heavy/probe-writer-control,probe-writer-trap; 5=B/heavy/probe-worker-control,probe-worker-trap; 6=C/standard/team-fullrecord,team,hint,solo-seam; 7=C/heavy/team-fullrecord,team,hint,solo-seam.

## handoff

Plan: `handoff.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $1836.00. Profiles (uncertainty ×2):

- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $5.3855 / $10.7501 / $21.4791 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $2.7033 / $5.3855 / $10.7501 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $8.0888 / $16.1356 / $32.2292 |
| A | standard | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $3.1138 / $6.2066 / $12.3923 |
| A | standard | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $1.5674 / $3.1138 / $6.2066 |
| A | standard | team-fullrecord | **Total** | 20 | 80 / 80 | $4.6812 / $9.3205 / $18.5989 |
| A | heavy | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $17.0695 / $34.1180 / $68.2150 |
| A | heavy | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $8.5452 / $17.0695 / $34.1180 |
| A | heavy | solo | **Total** | 20 | 20 / 20 | $25.6147 / $51.1875 / $102.3330 |
| A | heavy | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 80 / 80 | $6.1178 / $12.2147 / $24.4084 |
| A | heavy | team-fullrecord | openai/gpt-6.2-sol | 10 | 80 / 80 | $3.0694 / $6.1178 / $12.2147 |
| A | heavy | team-fullrecord | **Total** | 20 | 160 / 160 | $9.1873 / $18.3325 / $36.6230 |
| B | standard | probe-hunt-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9238 / $1.8224 / $3.6196 |
| B | standard | probe-hunt-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4745 / $0.9238 / $1.8224 |
| B | standard | probe-hunt-control | **Total** | 24 | 24 / 96 | $1.3983 / $2.7462 / $5.4420 |
| B | standard | probe-hunt-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9238 / $1.8224 / $3.6196 |
| B | standard | probe-hunt-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4745 / $0.9238 / $1.8224 |
| B | standard | probe-hunt-trap | **Total** | 24 | 24 / 96 | $1.3983 / $2.7462 / $5.4420 |
| B | standard | probe-ref-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9238 / $1.8224 / $3.6196 |
| B | standard | probe-ref-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4745 / $0.9238 / $1.8224 |
| B | standard | probe-ref-control | **Total** | 24 | 24 / 96 | $1.3983 / $2.7462 / $5.4420 |
| B | standard | probe-ref-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9238 / $1.8224 / $3.6196 |
| B | standard | probe-ref-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4745 / $0.9238 / $1.8224 |
| B | standard | probe-ref-trap | **Total** | 24 | 24 / 96 | $1.3983 / $2.7462 / $5.4420 |
| B | heavy | probe-hunt-control | anthropic/claude-opus-5.5 | 12 | 12 / 96 | $0.9238 / $1.8224 / $3.6196 |
| B | heavy | probe-hunt-control | openai/gpt-6.2-sol | 12 | 12 / 96 | $0.4745 / $0.9238 / $1.8224 |
| B | heavy | probe-hunt-control | **Total** | 24 | 24 / 192 | $1.3983 / $2.7462 / $5.4420 |
| B | heavy | probe-hunt-trap | anthropic/claude-opus-5.5 | 12 | 12 / 96 | $0.9238 / $1.8224 / $3.6196 |
| B | heavy | probe-hunt-trap | openai/gpt-6.2-sol | 12 | 12 / 96 | $0.4745 / $0.9238 / $1.8224 |
| B | heavy | probe-hunt-trap | **Total** | 24 | 24 / 192 | $1.3983 / $2.7462 / $5.4420 |
| B | heavy | probe-ref-control | anthropic/claude-opus-5.5 | 12 | 12 / 96 | $0.9238 / $1.8224 / $3.6196 |
| B | heavy | probe-ref-control | openai/gpt-6.2-sol | 12 | 12 / 96 | $0.4745 / $0.9238 / $1.8224 |
| B | heavy | probe-ref-control | **Total** | 24 | 24 / 192 | $1.3983 / $2.7462 / $5.4420 |
| B | heavy | probe-ref-trap | anthropic/claude-opus-5.5 | 12 | 12 / 96 | $0.9238 / $1.8224 / $3.6196 |
| B | heavy | probe-ref-trap | openai/gpt-6.2-sol | 12 | 12 / 96 | $0.4745 / $0.9238 / $1.8224 |
| B | heavy | probe-ref-trap | **Total** | 24 | 24 / 192 | $1.3983 / $2.7462 / $5.4420 |
| C | standard | hint | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $29.2730 / $58.3485 / $116.4996 |
| C | standard | hint | openai/gpt-6.2-sol | 94 | 376 / 376 | $14.7352 / $29.2730 / $58.3485 |
| C | standard | hint | **Total** | 188 | 752 / 752 | $44.0081 / $87.6215 / $174.8481 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $29.2237 / $58.2500 / $116.3026 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 94 | 376 / 376 | $14.7105 / $29.2237 / $58.2500 |
| C | standard | solo-seam | **Total** | 188 | 752 / 752 | $43.9342 / $87.4737 / $174.5526 |
| C | standard | team | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $29.1867 / $58.1761 / $116.1548 |
| C | standard | team | openai/gpt-6.2-sol | 94 | 376 / 376 | $14.6921 / $29.1867 / $58.1761 |
| C | standard | team | **Total** | 188 | 752 / 752 | $43.8788 / $87.3628 / $174.3309 |
| C | standard | team-fullrecord | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $29.2699 / $58.3424 / $116.4873 |
| C | standard | team-fullrecord | openai/gpt-6.2-sol | 94 | 376 / 376 | $14.7336 / $29.2699 / $58.3424 |
| C | standard | team-fullrecord | **Total** | 188 | 752 / 752 | $44.0035 / $87.6122 / $174.8297 |
| C | heavy | hint | anthropic/claude-opus-5.5 | 62 | 496 / 496 | $37.9346 / $75.7391 / $151.3479 |
| C | heavy | hint | openai/gpt-6.2-sol | 62 | 496 / 496 | $19.0324 / $37.9346 / $75.7391 |
| C | heavy | hint | **Total** | 124 | 992 / 992 | $56.9670 / $113.6737 / $227.0870 |
| C | heavy | solo-seam | anthropic/claude-opus-5.5 | 62 | 496 / 496 | $37.8703 / $75.6104 / $151.0906 |
| C | heavy | solo-seam | openai/gpt-6.2-sol | 62 | 496 / 496 | $19.0003 / $37.8703 / $75.6104 |
| C | heavy | solo-seam | **Total** | 124 | 992 / 992 | $56.8706 / $113.4807 / $226.7011 |
| C | heavy | team | anthropic/claude-opus-5.5 | 62 | 496 / 496 | $37.8221 / $75.5139 / $150.8977 |
| C | heavy | team | openai/gpt-6.2-sol | 62 | 496 / 496 | $18.9761 / $37.8221 / $75.5139 |
| C | heavy | team | **Total** | 124 | 992 / 992 | $56.7982 / $113.3360 / $226.4116 |
| C | heavy | team-fullrecord | anthropic/claude-opus-5.5 | 62 | 496 / 496 | $37.9306 / $75.7310 / $151.3318 |
| C | heavy | team-fullrecord | openai/gpt-6.2-sol | 62 | 496 / 496 | $19.0304 / $37.9306 / $75.7310 |
| C | heavy | team-fullrecord | **Total** | 124 | 992 / 992 | $56.9610 / $113.6616 / $227.0629 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high (stop) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 40 | 140 / 140 | $15.8853 / $31.6867 / $63.2894 | 40 | 140 / 140 | $15.8853 / $31.6867 / $63.2894 | $64.00 |
| A | anthropic/claude-opus-5.5 | 40 | 140 / 140 | $31.6867 / $63.2894 / $126.4947 | 40 | 140 / 140 | $31.6867 / $63.2894 / $126.4947 | $127.00 |
| A | all | 80 | 280 / 280 | $47.5720 / $94.9760 / $189.7841 | 80 | 280 / 280 | $47.5720 / $94.9760 / $189.7841 | $190.00 |
| B | openai/gpt-6.2-sol | 96 | 96 / 576 | $3.7960 / $7.3905 / $14.5793 | 136 | 236 / 716 | $19.6814 / $39.0771 / $77.8687 | $78.00 |
| B | anthropic/claude-opus-5.5 | 96 | 96 / 576 | $7.3905 / $14.5793 / $28.9571 | 136 | 236 / 716 | $39.0771 / $77.8687 / $155.4518 | $156.00 |
| B | all | 192 | 192 / 1152 | $11.1865 / $21.9698 / $43.5364 | 272 | 472 / 1432 | $58.7585 / $116.9458 / $233.3205 | $233.00 |
| C | openai/gpt-6.2-sol | 624 | 3488 / 3488 | $134.9106 / $268.5109 / $535.7114 | 760 | 3724 / 4204 | $154.5920 / $307.5880 / $613.5801 | $612.00 |
| C | anthropic/claude-opus-5.5 | 624 | 3488 / 3488 | $268.5109 / $535.7114 / $1070.1124 | 760 | 3724 / 4204 | $307.5880 / $613.5801 / $1225.5642 | $1224.00 |
| C | all | 1248 | 6976 / 6976 | $403.4215 / $804.2223 / $1605.8238 | 1520 | 7448 / 8408 | $462.1801 / $921.1681 / $1839.1443 | $1836.00 |

Power source: `handoff.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 94 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-ref-trap lapse-10 0.083, 94; probe-hunt-trap lapse-10 0.125, 62; probe-ref-trap lapse-20 0.208, 37; probe-hunt-trap lapse-20 0.375, 20; probe-ref-trap lapse-30 0.375, 20; probe-ref-trap lapse-50 0.458, 16; probe-hunt-trap lapse-30 0.625, 11; probe-hunt-trap lapse-50 0.875, 8
- heavy: 62 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-ref-trap lapse-10 0.125, 62; probe-hunt-trap lapse-10 0.167, 46; probe-ref-trap lapse-20 0.208, 37; probe-hunt-trap lapse-20 0.250, 31; probe-hunt-trap lapse-30 0.333, 23; probe-ref-trap lapse-30 0.417, 18; probe-hunt-trap lapse-50 0.542, 13; probe-ref-trap lapse-50 0.542, 13

Task sets (zero-based runner index): 0=A/standard/solo,team-fullrecord; 1=A/heavy/solo,team-fullrecord; 2=B/standard/probe-hunt-control,probe-hunt-trap; 3=B/standard/probe-ref-control,probe-ref-trap; 4=B/heavy/probe-hunt-control,probe-hunt-trap; 5=B/heavy/probe-ref-control,probe-ref-trap; 6=C/standard/team-fullrecord,team,hint,solo-seam; 7=C/heavy/team-fullrecord,team,hint,solo-seam.

## interface

**Not forecast:** no Seams family interface

## week

Plan: `week.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $231.00. Profiles (uncertainty ×2):

- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | canary | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $2.1568 / $4.2925 / $8.5641 |
| A | canary | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $1.0889 / $2.1568 / $4.2925 |
| A | canary | solo | **Total** | 20 | 20 / 20 | $3.2456 / $6.4493 / $12.8566 |
| A | canary | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $9.0696 / $18.1182 / $36.2155 |
| A | canary | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $4.5453 / $9.0696 / $18.1182 |
| A | canary | team-fullrecord | **Total** | 20 | 80 / 80 | $13.6149 / $27.1879 / $54.3337 |
| C | canary | hint | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $9.0883 / $18.1555 / $36.2901 |
| C | canary | hint | openai/gpt-6.2-sol | 10 | 40 / 40 | $4.5546 / $9.0883 / $18.1555 |
| C | canary | hint | **Total** | 20 | 80 / 80 | $13.6429 / $27.2438 / $54.4456 |
| C | canary | team | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $9.0693 / $18.1176 / $36.2141 |
| C | canary | team | openai/gpt-6.2-sol | 10 | 40 / 40 | $4.5451 / $9.0693 / $18.1176 |
| C | canary | team | **Total** | 20 | 80 / 80 | $13.6144 / $27.1868 / $54.3317 |
| C | canary | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $9.0696 / $18.1182 / $36.2155 |
| C | canary | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $4.5453 / $9.0696 / $18.1182 |
| C | canary | team-fullrecord | **Total** | 20 | 80 / 80 | $13.6149 / $27.1879 / $54.3337 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high (stop) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 20 | 50 / 50 | $5.6342 / $11.2264 / $22.4108 | 20 | 50 / 50 | $5.6342 / $11.2264 / $22.4108 | $23.00 |
| A | anthropic/claude-opus-5.5 | 20 | 50 / 50 | $11.2264 / $22.4108 / $44.7795 | 20 | 50 / 50 | $11.2264 / $22.4108 / $44.7795 | $45.00 |
| A | all | 40 | 100 / 100 | $16.8606 / $33.6371 / $67.1903 | 40 | 100 / 100 | $16.8606 / $33.6371 / $67.1903 | $68.00 |
| C | openai/gpt-6.2-sol | 30 | 120 / 120 | $13.6451 / $27.2272 / $54.3913 | 50 | 170 / 170 | $19.2793 / $38.4535 / $76.8021 | $77.00 |
| C | anthropic/claude-opus-5.5 | 30 | 120 / 120 | $27.2272 / $54.3913 / $108.7196 | 50 | 170 / 170 | $38.4535 / $76.8021 / $153.4991 | $154.00 |
| C | all | 60 | 240 / 240 | $40.8722 / $81.6185 / $163.1109 | 100 | 340 / 340 | $57.7328 / $115.2556 / $230.3012 | $231.00 |

Power source: `unavailable`. Stage B skipped: no probe pair. Stage C envelope (largest eligible card n, capped):

- canary: 10 pairs/model maximum. No probes; descriptive demonstration only.

Task sets (zero-based runner index): 0=A/canary/solo,team-fullrecord; 1=C/canary/team-fullrecord,team,hint.

## Suite: maximum envelope, one harness

| Family | Trials | Model / all seats | Cost low / expected / high | Model cap |
|---|---:|---:|---:|---:|
| commit | 752 | 906 / 1156 | $177.3213 / $353.0634 / $704.5477 | $703.00 |
| digest | 504 | 1428 / 1860 | $65.4057 / $129.7530 / $258.4476 | $258.00 |
| handoff | 1520 | 7448 / 8408 | $462.1801 / $921.1681 / $1839.1443 | $1836.00 |
| interface | unavailable | — | — | — |
| week | 100 | 340 / 340 | $57.7328 / $115.2556 / $230.3012 | $231.00 |
| **Total** | 2876 | 10122 / 11764 | $762.6399 / $1519.2402 / $3032.4408 | $3028.00 |
