# Seams pilot forecast

**No model run. No spend approval. Gated plans.**

Seams staged pilot: executable maximum envelope, not spending approval.
Stage A: 10 distinct instances per model, level and control arm; require at least 9 full successes in each arm.
Drop a model-level group if either control fails or remains incomplete after one retry.
Stage B: run each matched probe pair on 12 fresh instances for each model-level group that passed A.
Use exact sequential McNemar at 3, 6 and 12 pairs, alpha 0.05, Holm over pairs within model and level.
At least 2 control-only successes in any pair are required for that model-level group to continue.
Early positive efficacy can stop with this threshold met; statistical futility cannot establish no effect.
If the threshold is not met, report no-effect only with 12 valid pairs; otherwise report incomplete.
Stage C: on fresh matched instances for each passing group, compare team to team-fullrecord when present, otherwise solo.
Include hint and solo-seam when present. Freeze n at the largest eligible positive card alternative, at most 100.
Use fixed-sample exact McNemar on full success. A capped sample can have less than 80% power.
Conditional selection of the smallest compatible card effect, first signal level, or inclusion of other A-passing
models at that level requires a separately pre-registered headline plan. The runner does not resize the headline.
A family without probe pairs has no attribution gate: week is only a descriptive demonstration (no headline claim).
Require a private seed, QA, oracle parity and approval before paid work. Use --stop-after-stage A, then B, then C
with the same run directory; the runner applies the gates and cumulative model caps. For week skip B.
Repeat on Terminus 2 and one native command-line harness before claiming an effect; record the delivery mode.
Matched-token and cross-model team controls, and 5 human baselines, are required before publication.
The target result is a boundary cost on the same work, with a similar cost for self and teammate transfer;
hint recovers less than one third and a harness mechanism recovers at least two thirds. This pilot alone cannot establish that.
Kill if standard team reaches 0.9 of control and hint closes the gap, the effect reverses by harness, or heavy solo is below 0.9.
For digest heavy, require at least 0.95 solo success: 10/10 at this pilot size.

## Forecast assumptions

- All planned levels and models are included in the maximum envelope. B and C run only groups that pass the preceding gate. A, B and C use disjoint instance IDs.
- For B, b is control-only full success and c is trap-only full success. C uses the largest eligible card n, capped at 100. The card's paired n is an estimate, not observed model power; probe effect may not transfer to team.
- Each row is a maximum count (no early stop, no retries). The cap is the cumulative model-only high estimate rounded up to USD, unless overridden. Sandbox spend is outside the model cap.
- Modal is $0.0021/trial (measured 2026-11-24); scripted seats have no model cost. Costs include sandbox spend, not invalid retries or second-harness replication.
- Profile simulation: first 3 instances per level and arm, careful policy, private build seed, schedule seed 0. Playtest rows override matching arm/role only; other rows use turn estimates.
- One homogeneous model per trial; Terminus 2, max 100 turns, Modal, concurrency 10, starts spaced 5 s. Turn estimates extrapolate probe history; model cache and turn costs may differ. No assumed compaction.
- The runner has a single agent and roster design; the second harness and cross-model and matched-token arms need separate approved plans. A second pass at the same counts and profile would double these totals.
- Low/high = model estimate divided/multiplied by uncertainty, plus fixed sandbox cost. Not a confidence interval.
- Keep the private builds and manifests frozen; require QA and oracle parity before approved model runs.

## commit

Plan: `commit.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $704.00. Profiles (uncertainty ×2):

- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $3.1278 / $6.2346 / $12.4481 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $1.5744 / $3.1278 / $6.2346 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $4.7022 / $9.3623 / $18.6827 |
| A | heavy | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $3.1401 / $6.2592 / $12.4974 |
| A | heavy | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $1.5806 / $3.1401 / $6.2592 |
| A | heavy | solo | **Total** | 20 | 20 / 20 | $4.7207 / $9.3993 / $18.7566 |
| B | standard | probe-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7524 / $7.4796 / $14.9340 |
| B | standard | probe-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8888 / $3.7524 / $7.4796 |
| B | standard | probe-control | **Total** | 24 | 24 / 48 | $5.6412 / $11.2320 / $22.4136 |
| B | standard | probe-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7524 / $7.4796 / $14.9340 |
| B | standard | probe-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8888 / $3.7524 / $7.4796 |
| B | standard | probe-trap | **Total** | 24 | 24 / 48 | $5.6412 / $11.2320 / $22.4136 |
| B | heavy | probe-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7531 / $7.4810 / $14.9369 |
| B | heavy | probe-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8892 / $3.7531 / $7.4810 |
| B | heavy | probe-control | **Total** | 24 | 24 / 48 | $5.6423 / $11.2342 / $22.4179 |
| B | heavy | probe-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $3.7531 / $7.4810 / $14.9369 |
| B | heavy | probe-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $1.8892 / $3.7531 / $7.4810 |
| B | heavy | probe-trap | **Total** | 24 | 24 / 48 | $5.6423 / $11.2342 / $22.4179 |
| C | standard | hint | anthropic/claude-opus-5.5 | 46 | 46 / 92 | $14.4117 / $28.7268 / $57.3569 |
| C | standard | hint | openai/gpt-6.2-sol | 46 | 46 / 92 | $7.2541 / $14.4117 / $28.7268 |
| C | standard | hint | **Total** | 92 | 92 / 184 | $21.6658 / $43.1384 / $86.0837 |
| C | standard | solo | anthropic/claude-opus-5.5 | 46 | 46 / 46 | $14.3878 / $28.6789 / $57.2613 |
| C | standard | solo | openai/gpt-6.2-sol | 46 | 46 / 46 | $7.2422 / $14.3878 / $28.6789 |
| C | standard | solo | **Total** | 92 | 92 / 92 | $21.6300 / $43.0667 / $85.9402 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 46 | 46 / 46 | $14.3900 / $28.6834 / $57.2702 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 46 | 46 / 46 | $7.2433 / $14.3900 / $28.6834 |
| C | standard | solo-seam | **Total** | 92 | 92 / 92 | $21.6333 / $43.0734 / $85.9536 |
| C | standard | team | anthropic/claude-opus-5.5 | 46 | 92 / 92 | $14.5558 / $29.0150 / $57.9335 |
| C | standard | team | openai/gpt-6.2-sol | 46 | 92 / 92 | $7.3262 / $14.5558 / $29.0150 |
| C | standard | team | **Total** | 92 | 184 / 184 | $21.8820 / $43.5708 / $86.9485 |
| C | heavy | hint | anthropic/claude-opus-5.5 | 31 | 31 / 62 | $9.7076 / $19.3500 / $38.6349 |
| C | heavy | hint | openai/gpt-6.2-sol | 31 | 31 / 62 | $4.8863 / $9.7076 / $19.3500 |
| C | heavy | hint | **Total** | 62 | 62 / 124 | $14.5939 / $29.0576 / $57.9850 |
| C | heavy | solo | anthropic/claude-opus-5.5 | 31 | 31 / 31 | $9.7343 / $19.4036 / $38.7420 |
| C | heavy | solo | openai/gpt-6.2-sol | 31 | 31 / 31 | $4.8997 / $9.7343 / $19.4036 |
| C | heavy | solo | **Total** | 62 | 62 / 62 | $14.6340 / $29.1379 / $58.1456 |
| C | heavy | solo-seam | anthropic/claude-opus-5.5 | 31 | 31 / 31 | $9.6929 / $19.3208 / $38.5765 |
| C | heavy | solo-seam | openai/gpt-6.2-sol | 31 | 31 / 31 | $4.8790 / $9.6929 / $19.3208 |
| C | heavy | solo-seam | **Total** | 62 | 62 / 62 | $14.5720 / $29.0137 / $57.8973 |
| C | heavy | team | anthropic/claude-opus-5.5 | 31 | 62 / 62 | $9.8055 / $19.5459 / $39.0267 |
| C | heavy | team | openai/gpt-6.2-sol | 31 | 62 / 62 | $4.9353 / $9.8055 / $19.5459 |
| C | heavy | team | **Total** | 62 | 124 / 124 | $14.7408 / $29.3514 / $58.5726 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high estimate (stage cap on all row) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 20 | 20 / 20 | $3.1549 / $6.2679 / $12.4938 | 20 | 20 / 20 | $3.1549 / $6.2679 / $12.4938 | $12.45 |
| A | anthropic/claude-opus-5.5 | 20 | 20 / 20 | $6.2679 / $12.4938 / $24.9455 | 20 | 20 / 20 | $6.2679 / $12.4938 / $24.9455 | $24.90 |
| A | all | 40 | 40 / 40 | $9.4228 / $18.7616 / $37.4393 | 40 | 40 / 40 | $9.4228 / $18.7616 / $37.4393 | $38.00 |
| B | openai/gpt-6.2-sol | 48 | 48 / 96 | $7.5559 / $15.0110 / $29.9213 | 68 | 68 / 116 | $10.7109 / $21.2789 / $42.4151 | $42.27 |
| B | anthropic/claude-opus-5.5 | 48 | 48 / 96 | $15.0110 / $29.9213 / $59.7418 | 68 | 68 / 116 | $21.2789 / $42.4151 / $84.6873 | $84.54 |
| B | all | 96 | 96 / 192 | $22.5670 / $44.9323 / $89.6631 | 136 | 136 / 232 | $31.9898 / $63.6940 / $127.1024 | $127.00 |
| C | openai/gpt-6.2-sol | 308 | 385 / 462 | $48.6662 / $96.6856 / $192.7244 | 376 | 453 / 578 | $59.3771 / $117.9645 / $235.1394 | $234.35 |
| C | anthropic/claude-opus-5.5 | 308 | 385 / 462 | $96.6856 / $192.7244 / $384.8020 | 376 | 453 / 578 | $117.9645 / $235.1394 / $469.4893 | $468.70 |
| C | all | 616 | 770 / 924 | $145.3518 / $289.4100 / $577.5264 | 752 | 906 / 1156 | $177.3416 / $353.1040 / $704.6287 | $704.00 |

Power source: `commit.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 46 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-trap lapse-10 0.167, 46; probe-trap lapse-20 0.417, 18; probe-trap lapse-30 0.542, 13; probe-trap lapse-50 0.750, 9
- heavy: 31 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-trap lapse-10 0.250, 31; probe-trap lapse-20 0.375, 20; probe-trap lapse-30 0.667, 11; probe-trap lapse-50 1.000, 6

- **standard: hint does not match the team roster.** 1/2 model/all seats versus 2/2. Costed as built; do not claim an isolated effect.
- **heavy: hint does not match the team roster.** 1/2 model/all seats versus 2/2. Costed as built; do not claim an isolated effect.

Task sets (zero-based runner index): 0=A/standard/solo; 1=A/heavy/solo; 2=B/standard/probe-control,probe-trap; 3=B/heavy/probe-control,probe-trap; 4=C/standard/solo,team,solo-seam; 5=C/standard/hint; 6=C/heavy/solo,team,solo-seam; 7=C/heavy/hint.

## digest

Plan: `digest.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $259.00. Profiles (uncertainty ×2):

- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $1.4371 / $2.8532 / $5.6854 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $0.7291 / $1.4371 / $2.8532 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $2.1662 / $4.2903 / $8.5387 |
| A | standard | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $2.2916 / $4.5622 / $9.1034 |
| A | standard | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $1.1563 / $2.2916 / $4.5622 |
| A | standard | team-fullrecord | **Total** | 20 | 80 / 80 | $3.4479 / $6.8538 / $13.6656 |
| A | heavy | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $1.3975 / $2.7740 / $5.5270 |
| A | heavy | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $0.7093 / $1.3975 / $2.7740 |
| A | heavy | solo | **Total** | 20 | 20 / 20 | $2.1068 / $4.1715 / $8.3010 |
| A | heavy | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 50 / 50 | $2.5777 / $5.1344 / $10.2478 |
| A | heavy | team-fullrecord | openai/gpt-6.2-sol | 10 | 50 / 50 | $1.2993 / $2.5777 / $5.1344 |
| A | heavy | team-fullrecord | **Total** | 20 | 100 / 100 | $3.8770 / $7.7121 / $15.3822 |
| B | standard | probe-worker-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $1.4859 / $2.9467 / $5.8681 |
| B | standard | probe-worker-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.7556 / $1.4859 / $2.9467 |
| B | standard | probe-worker-control | **Total** | 24 | 24 / 96 | $2.2415 / $4.4326 / $8.8148 |
| B | standard | probe-worker-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $1.4859 / $2.9467 / $5.8681 |
| B | standard | probe-worker-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.7556 / $1.4859 / $2.9467 |
| B | standard | probe-worker-trap | **Total** | 24 | 24 / 96 | $2.2415 / $4.4326 / $8.8148 |
| B | standard | probe-writer-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4129 / $0.8006 / $1.5761 |
| B | standard | probe-writer-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2191 / $0.4129 / $0.8006 |
| B | standard | probe-writer-control | **Total** | 24 | 24 / 48 | $0.6320 / $1.2136 / $2.3767 |
| B | standard | probe-writer-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4129 / $0.8006 / $1.5760 |
| B | standard | probe-writer-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2191 / $0.4129 / $0.8006 |
| B | standard | probe-writer-trap | **Total** | 24 | 24 / 48 | $0.6320 / $1.2135 / $2.3766 |
| B | heavy | probe-worker-control | anthropic/claude-opus-5.5 | 12 | 12 / 60 | $1.5722 / $3.1192 / $6.2132 |
| B | heavy | probe-worker-control | openai/gpt-6.2-sol | 12 | 12 / 60 | $0.7987 / $1.5722 / $3.1192 |
| B | heavy | probe-worker-control | **Total** | 24 | 24 / 120 | $2.3709 / $4.6914 / $9.3324 |
| B | heavy | probe-worker-trap | anthropic/claude-opus-5.5 | 12 | 12 / 60 | $1.5722 / $3.1192 / $6.2132 |
| B | heavy | probe-worker-trap | openai/gpt-6.2-sol | 12 | 12 / 60 | $0.7987 / $1.5722 / $3.1192 |
| B | heavy | probe-worker-trap | **Total** | 24 | 24 / 120 | $2.3709 / $4.6914 / $9.3324 |
| B | heavy | probe-writer-control | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4130 / $0.8008 / $1.5764 |
| B | heavy | probe-writer-control | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2191 / $0.4130 / $0.8008 |
| B | heavy | probe-writer-control | **Total** | 24 | 24 / 48 | $0.6321 / $1.2138 / $2.3772 |
| B | heavy | probe-writer-trap | anthropic/claude-opus-5.5 | 12 | 12 / 24 | $0.4130 / $0.8008 / $1.5764 |
| B | heavy | probe-writer-trap | openai/gpt-6.2-sol | 12 | 12 / 24 | $0.2191 / $0.4130 / $0.8008 |
| B | heavy | probe-writer-trap | **Total** | 24 | 24 / 48 | $0.6321 / $1.2138 / $2.3771 |
| C | standard | hint | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.1239 / $8.2100 / $16.3821 |
| C | standard | hint | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0808 / $4.1239 / $8.2100 |
| C | standard | hint | **Total** | 36 | 144 / 144 | $6.2047 / $12.3339 / $24.5921 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.1148 / $8.1918 / $16.3458 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0763 / $4.1148 / $8.1918 |
| C | standard | solo-seam | **Total** | 36 | 144 / 144 | $6.1911 / $12.3066 / $24.5376 |
| C | standard | team | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.1162 / $8.1946 / $16.3514 |
| C | standard | team | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0770 / $4.1162 / $8.1946 |
| C | standard | team | **Total** | 36 | 144 / 144 | $6.1932 / $12.3108 / $24.5460 |
| C | standard | team-fullrecord | anthropic/claude-opus-5.5 | 18 | 72 / 72 | $4.1249 / $8.2120 / $16.3861 |
| C | standard | team-fullrecord | openai/gpt-6.2-sol | 18 | 72 / 72 | $2.0813 / $4.1249 / $8.2120 |
| C | standard | team-fullrecord | **Total** | 36 | 144 / 144 | $6.2062 / $12.3369 / $24.5981 |
| C | heavy | hint | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.9196 / $5.8161 / $11.6091 |
| C | heavy | hint | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4714 / $2.9196 / $5.8161 |
| C | heavy | hint | **Total** | 22 | 110 / 110 | $4.3910 / $8.7357 / $17.4253 |
| C | heavy | solo-seam | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.9121 / $5.8012 / $11.5793 |
| C | heavy | solo-seam | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4676 / $2.9121 / $5.8012 |
| C | heavy | solo-seam | **Total** | 22 | 110 / 110 | $4.3798 / $8.7133 / $17.3804 |
| C | heavy | team | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.9133 / $5.8036 / $11.5841 |
| C | heavy | team | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4682 / $2.9133 / $5.8036 |
| C | heavy | team | **Total** | 22 | 110 / 110 | $4.3816 / $8.7169 / $17.3877 |
| C | heavy | team-fullrecord | anthropic/claude-opus-5.5 | 11 | 55 / 55 | $2.8355 / $5.6478 / $11.2725 |
| C | heavy | team-fullrecord | openai/gpt-6.2-sol | 11 | 55 / 55 | $1.4293 / $2.8355 / $5.6478 |
| C | heavy | team-fullrecord | **Total** | 22 | 110 / 110 | $4.2647 / $8.4833 / $16.9204 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high estimate (stage cap on all row) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 40 | 110 / 110 | $3.8940 / $7.7039 / $15.3238 | 40 | 110 / 110 | $3.8940 / $7.7039 / $15.3238 | $15.24 |
| A | anthropic/claude-opus-5.5 | 40 | 110 / 110 | $7.7039 / $15.3238 / $30.5636 | 40 | 110 / 110 | $7.7039 / $15.3238 / $30.5636 | $30.48 |
| A | all | 80 | 220 / 220 | $11.5979 / $23.0277 / $45.8874 | 80 | 220 / 220 | $11.5979 / $23.0277 / $45.8874 | $46.00 |
| B | openai/gpt-6.2-sol | 96 | 96 / 312 | $3.9848 / $7.7681 / $15.3345 | 136 | 206 / 422 | $7.8788 / $15.4720 / $30.6583 | $30.37 |
| B | anthropic/claude-opus-5.5 | 96 | 96 / 312 | $7.7681 / $15.3345 / $30.4674 | 136 | 206 / 422 | $15.4720 / $30.6583 / $61.0310 | $60.75 |
| B | all | 192 | 192 / 624 | $11.7529 / $23.1026 / $45.8020 | 272 | 412 / 844 | $23.3507 / $46.1303 / $91.6894 | $92.00 |
| C | openai/gpt-6.2-sol | 116 | 508 / 508 | $14.1520 / $28.0603 / $55.8770 | 252 | 714 / 930 | $22.0307 / $43.5323 / $86.5354 | $86.01 |
| C | anthropic/claude-opus-5.5 | 116 | 508 / 508 | $28.0603 / $55.8770 / $111.5105 | 252 | 714 / 930 | $43.5323 / $86.5354 / $172.5415 | $172.01 |
| C | all | 232 | 1016 / 1016 | $42.2123 / $83.9374 / $167.3875 | 504 | 1428 / 1860 | $65.5630 / $130.0677 / $259.0769 | $259.00 |

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

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high estimate (stage cap on all row) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 40 | 140 / 140 | $15.8853 / $31.6867 / $63.2894 | 40 | 140 / 140 | $15.8853 / $31.6867 / $63.2894 | $63.21 |
| A | anthropic/claude-opus-5.5 | 40 | 140 / 140 | $31.6867 / $63.2894 / $126.4947 | 40 | 140 / 140 | $31.6867 / $63.2894 / $126.4947 | $126.41 |
| A | all | 80 | 280 / 280 | $47.5720 / $94.9760 / $189.7841 | 80 | 280 / 280 | $47.5720 / $94.9760 / $189.7841 | $190.00 |
| B | openai/gpt-6.2-sol | 96 | 96 / 576 | $3.7960 / $7.3905 / $14.5793 | 136 | 236 / 716 | $19.6814 / $39.0771 / $77.8687 | $77.58 |
| B | anthropic/claude-opus-5.5 | 96 | 96 / 576 | $7.3905 / $14.5793 / $28.9571 | 136 | 236 / 716 | $39.0771 / $77.8687 / $155.4518 | $155.17 |
| B | all | 192 | 192 / 1152 | $11.1865 / $21.9698 / $43.5364 | 272 | 472 / 1432 | $58.7585 / $116.9458 / $233.3205 | $233.00 |
| C | openai/gpt-6.2-sol | 624 | 3488 / 3488 | $134.9106 / $268.5109 / $535.7114 | 760 | 3724 / 4204 | $154.5920 / $307.5880 / $613.5801 | $611.98 |
| C | anthropic/claude-opus-5.5 | 624 | 3488 / 3488 | $268.5109 / $535.7114 / $1070.1124 | 760 | 3724 / 4204 | $307.5880 / $613.5801 / $1225.5642 | $1223.97 |
| C | all | 1248 | 6976 / 6976 | $403.4215 / $804.2223 / $1605.8238 | 1520 | 7448 / 8408 | $462.1801 / $921.1681 / $1839.1443 | $1836.00 |

Power source: `handoff.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 94 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-ref-trap lapse-10 0.083, 94; probe-hunt-trap lapse-10 0.125, 62; probe-ref-trap lapse-20 0.208, 37; probe-hunt-trap lapse-20 0.375, 20; probe-ref-trap lapse-30 0.375, 20; probe-ref-trap lapse-50 0.458, 16; probe-hunt-trap lapse-30 0.625, 11; probe-hunt-trap lapse-50 0.875, 8
- heavy: 62 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-ref-trap lapse-10 0.125, 62; probe-hunt-trap lapse-10 0.167, 46; probe-ref-trap lapse-20 0.208, 37; probe-hunt-trap lapse-20 0.250, 31; probe-hunt-trap lapse-30 0.333, 23; probe-ref-trap lapse-30 0.417, 18; probe-hunt-trap lapse-50 0.542, 13; probe-ref-trap lapse-50 0.542, 13

Task sets (zero-based runner index): 0=A/standard/solo,team-fullrecord; 1=A/heavy/solo,team-fullrecord; 2=B/standard/probe-hunt-control,probe-hunt-trap; 3=B/standard/probe-ref-control,probe-ref-trap; 4=B/heavy/probe-hunt-control,probe-hunt-trap; 5=B/heavy/probe-ref-control,probe-ref-trap; 6=C/standard/team-fullrecord,team,hint,solo-seam; 7=C/heavy/team-fullrecord,team,hint,solo-seam.

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

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high estimate (stage cap on all row) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 20 | 50 / 50 | $5.6342 / $11.2264 / $22.4108 | 20 | 50 / 50 | $5.6342 / $11.2264 / $22.4108 | $22.37 |
| A | anthropic/claude-opus-5.5 | 20 | 50 / 50 | $11.2264 / $22.4108 / $44.7795 | 20 | 50 / 50 | $11.2264 / $22.4108 / $44.7795 | $44.74 |
| A | all | 40 | 100 / 100 | $16.8606 / $33.6371 / $67.1903 | 40 | 100 / 100 | $16.8606 / $33.6371 / $67.1903 | $68.00 |
| C | openai/gpt-6.2-sol | 30 | 120 / 120 | $13.6451 / $27.2272 / $54.3913 | 50 | 170 / 170 | $19.2793 / $38.4535 / $76.8021 | $76.70 |
| C | anthropic/claude-opus-5.5 | 30 | 120 / 120 | $27.2272 / $54.3913 / $108.7196 | 50 | 170 / 170 | $38.4535 / $76.8021 / $153.4991 | $153.39 |
| C | all | 60 | 240 / 240 | $40.8722 / $81.6185 / $163.1109 | 100 | 340 / 340 | $57.7328 / $115.2556 / $230.3012 | $231.00 |

Power source: `unavailable`. Stage B skipped: no probe pair. Stage C envelope (largest eligible card n, capped):

- canary: 10 pairs/model maximum. No probes; descriptive demonstration only.

Task sets (zero-based runner index): 0=A/canary/solo,team-fullrecord; 1=C/canary/team-fullrecord,team,hint.

## Suite: maximum envelope, one harness

| Family | Trials | Model / all seats | Cost low / expected / high | Model cap |
|---|---:|---:|---:|---:|
| commit | 752 | 906 / 1156 | $177.3416 / $353.1040 / $704.6287 | $704.00 |
| digest | 504 | 1428 / 1860 | $65.5630 / $130.0677 / $259.0769 | $259.00 |
| handoff | 1520 | 7448 / 8408 | $462.1801 / $921.1681 / $1839.1443 | $1836.00 |
| week | 100 | 340 / 340 | $57.7328 / $115.2556 / $230.3012 | $231.00 |
| **Total** | 2876 | 10122 / 11764 | $762.8175 / $1519.5954 / $3033.1511 | $3030.00 |
