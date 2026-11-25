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
Include hint, solo-seam, team-implicit and team-implicit-all when present. Freeze n at the largest eligible positive
card alternative, at most 100.
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

## handoff

Plan: `handoff.json`. Price source: OpenRouter public catalog (https://openrouter.ai/api/v1/models), snapshot `frontier-prices.json`, 2026-11-25 ET.
Model cap: $575.00. Profiles (uncertainty ×2):

- Claude final result usage (cache creation at miss rate)
- Claude final result usage (cache creation at miss rate); Codex turn.completed usage
- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $5.3829 / $10.7447 / $21.4685 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $2.7019 / $5.3829 / $10.7447 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $8.0848 / $16.1276 / $32.2132 |
| A | standard | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $2.4799 / $4.9387 / $9.8565 |
| A | standard | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $1.2504 / $2.4799 / $4.9387 |
| A | standard | team-fullrecord | **Total** | 20 | 80 / 80 | $3.7303 / $7.4186 / $14.7952 |
| B | standard | probe-hunt-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9229 / $1.8206 / $3.6160 |
| B | standard | probe-hunt-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4740 / $0.9229 / $1.8206 |
| B | standard | probe-hunt-control | **Total** | 24 | 24 / 96 | $1.3969 / $2.7435 / $5.4366 |
| B | standard | probe-hunt-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9229 / $1.8206 / $3.6160 |
| B | standard | probe-hunt-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4740 / $0.9229 / $1.8206 |
| B | standard | probe-hunt-trap | **Total** | 24 | 24 / 96 | $1.3969 / $2.7435 / $5.4366 |
| B | standard | probe-ref-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9229 / $1.8206 / $3.6160 |
| B | standard | probe-ref-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4740 / $0.9229 / $1.8206 |
| B | standard | probe-ref-control | **Total** | 24 | 24 / 96 | $1.3969 / $2.7435 / $5.4366 |
| B | standard | probe-ref-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9229 / $1.8206 / $3.6160 |
| B | standard | probe-ref-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4740 / $0.9229 / $1.8206 |
| B | standard | probe-ref-trap | **Total** | 24 | 24 / 96 | $1.3969 / $2.7435 / $5.4366 |
| C | standard | hint | anthropic/claude-opus-5.5 | 62 | 248 / 248 | $12.3356 / $24.5410 / $48.9519 |
| C | standard | hint | openai/gpt-6.2-sol | 62 | 248 / 248 | $6.2329 / $12.3356 / $24.5410 |
| C | standard | hint | **Total** | 124 | 496 / 496 | $18.5685 / $36.8767 / $73.4929 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 62 | 248 / 248 | $19.2559 / $38.3816 / $76.6330 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 62 | 248 / 248 | $9.6931 / $19.2559 / $38.3816 |
| C | standard | solo-seam | **Total** | 124 | 496 / 496 | $28.9490 / $57.6375 / $115.0147 |
| C | standard | team | anthropic/claude-opus-5.5 | 62 | 248 / 248 | $13.1555 / $26.1808 / $52.2313 |
| C | standard | team | openai/gpt-6.2-sol | 62 | 248 / 248 | $6.6428 / $13.1555 / $26.1808 |
| C | standard | team | **Total** | 124 | 496 / 496 | $19.7983 / $39.3362 / $78.4121 |
| C | standard | team-fullrecord | anthropic/claude-opus-5.5 | 62 | 248 / 248 | $15.3752 / $30.6202 / $61.1102 |
| C | standard | team-fullrecord | openai/gpt-6.2-sol | 62 | 248 / 248 | $7.7527 / $15.3752 / $30.6202 |
| C | standard | team-fullrecord | **Total** | 124 | 496 / 496 | $23.1279 / $45.9954 / $91.7304 |
| C | standard | team-implicit | anthropic/claude-opus-5.5 | 62 | 248 / 248 | $11.9789 / $23.8275 / $47.5249 |
| C | standard | team-implicit | openai/gpt-6.2-sol | 62 | 248 / 248 | $6.0545 / $11.9789 / $23.8275 |
| C | standard | team-implicit | **Total** | 124 | 496 / 496 | $18.0334 / $35.8064 / $71.3524 |
| C | standard | team-implicit-all | anthropic/claude-opus-5.5 | 62 | 248 / 248 | $12.9852 / $25.8401 / $51.5500 |
| C | standard | team-implicit-all | openai/gpt-6.2-sol | 62 | 248 / 248 | $6.5577 / $12.9852 / $25.8401 |
| C | standard | team-implicit-all | **Total** | 124 | 496 / 496 | $19.5428 / $38.8253 / $77.3901 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high estimate (stage cap on all row) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 20 | 50 / 50 | $3.9524 / $7.8627 / $15.6835 | 20 | 50 / 50 | $3.9524 / $7.8627 / $15.6835 | $15.64 |
| A | anthropic/claude-opus-5.5 | 20 | 50 / 50 | $7.8627 / $15.6835 / $31.3250 | 20 | 50 / 50 | $7.8627 / $15.6835 / $31.3250 | $31.28 |
| A | all | 40 | 100 / 100 | $11.8151 / $23.5462 / $47.0084 | 40 | 100 / 100 | $11.8151 / $23.5462 / $47.0084 | $47.00 |
| B | openai/gpt-6.2-sol | 48 | 48 / 192 | $1.8962 / $3.6916 / $7.2823 | 68 | 98 / 242 | $5.8486 / $11.5543 / $22.9658 | $22.82 |
| B | anthropic/claude-opus-5.5 | 48 | 48 / 192 | $3.6916 / $7.2823 / $14.4639 | 68 | 98 / 242 | $11.5543 / $22.9658 / $45.7889 | $45.65 |
| B | all | 96 | 96 / 384 | $5.5878 / $10.9739 / $21.7462 | 136 | 196 / 484 | $17.4029 / $34.5201 / $68.7547 | $69.00 |
| C | openai/gpt-6.2-sol | 372 | 1488 / 1488 | $42.9337 / $85.0862 / $169.3913 | 440 | 1586 / 1730 | $48.7823 / $96.6405 / $192.3571 | $191.43 |
| C | anthropic/claude-opus-5.5 | 372 | 1488 / 1488 | $85.0862 / $169.3913 / $338.0013 | 440 | 1586 / 1730 | $96.6405 / $192.3571 / $383.7902 | $382.87 |
| C | all | 744 | 2976 / 2976 | $128.0199 / $254.4775 / $507.3926 | 880 | 3172 / 3460 | $145.4228 / $288.9976 / $576.1472 | $575.00 |

Power source: `handoff.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 62 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-ref-trap lapse-10 0.125, 62; probe-hunt-trap lapse-10 0.167, 46; probe-ref-trap lapse-30 0.250, 31; probe-ref-trap lapse-20 0.292, 26; probe-hunt-trap lapse-20 0.417, 18; probe-hunt-trap lapse-30 0.500, 15; probe-ref-trap lapse-50 0.583, 12; probe-hunt-trap lapse-50 0.750, 9

Task sets (zero-based runner index): 0=A/standard/solo,team-fullrecord; 1=B/standard/probe-hunt-control,probe-hunt-trap; 2=B/standard/probe-ref-control,probe-ref-trap; 3=C/standard/team-fullrecord,team,hint,solo-seam,team-implicit,team-implicit-all.

## Suite: maximum envelope, one harness

| Family | Trials | Model / all seats | Cost low / expected / high | Model cap |
|---|---:|---:|---:|---:|
| handoff | 880 | 3172 / 3460 | $145.4228 / $288.9976 / $576.1472 | $575.00 |
| **Total** | 880 | 3172 / 3460 | $145.4228 / $288.9976 / $576.1472 | $575.00 |
