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
Include hint, solo-seam and team-implicit when present. Freeze n at the largest eligible positive card alternative, at most 100.
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
Model cap: $705.00. Profiles (uncertainty ×2):

- Claude final result usage (cache creation at miss rate)
- Claude final result usage (cache creation at miss rate); Codex turn.completed usage
- Terminus 2 turn model; 4 chars/token (instruction + briefing); 1 careful world calls/turn, ceil, min 1; schedule 0; overhead 478.25, growth 603.2, cache fraction 0.7008, output/turn 307; linear context growth, no compaction, files/replies in fitted growth; 2679 finished Terminus 2 probe seats (2026-11-25); in-sample WAPE miss 36.36%, hit 39.32%, out 39.67%

| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |
|---|---|---|---|---:|---:|---:|
| A | standard | solo | anthropic/claude-opus-5.5 | 10 | 10 / 10 | $5.3840 / $10.7470 / $21.4731 |
| A | standard | solo | openai/gpt-6.2-sol | 10 | 10 / 10 | $2.7025 / $5.3840 / $10.7470 |
| A | standard | solo | **Total** | 20 | 20 / 20 | $8.0865 / $16.1311 / $32.2201 |
| A | standard | team-fullrecord | anthropic/claude-opus-5.5 | 10 | 40 / 40 | $2.4799 / $4.9387 / $9.8565 |
| A | standard | team-fullrecord | openai/gpt-6.2-sol | 10 | 40 / 40 | $1.2504 / $2.4799 / $4.9387 |
| A | standard | team-fullrecord | **Total** | 20 | 80 / 80 | $3.7303 / $7.4186 / $14.7952 |
| B | standard | probe-hunt-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9233 / $1.8214 / $3.6175 |
| B | standard | probe-hunt-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4742 / $0.9233 / $1.8214 |
| B | standard | probe-hunt-control | **Total** | 24 | 24 / 96 | $1.3975 / $2.7446 / $5.4389 |
| B | standard | probe-hunt-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9233 / $1.8214 / $3.6175 |
| B | standard | probe-hunt-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4742 / $0.9233 / $1.8214 |
| B | standard | probe-hunt-trap | **Total** | 24 | 24 / 96 | $1.3975 / $2.7446 / $5.4389 |
| B | standard | probe-ref-control | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9233 / $1.8214 / $3.6175 |
| B | standard | probe-ref-control | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4742 / $0.9233 / $1.8214 |
| B | standard | probe-ref-control | **Total** | 24 | 24 / 96 | $1.3975 / $2.7446 / $5.4389 |
| B | standard | probe-ref-trap | anthropic/claude-opus-5.5 | 12 | 12 / 48 | $0.9233 / $1.8214 / $3.6175 |
| B | standard | probe-ref-trap | openai/gpt-6.2-sol | 12 | 12 / 48 | $0.4742 / $0.9233 / $1.8214 |
| B | standard | probe-ref-trap | **Total** | 24 | 24 / 96 | $1.3975 / $2.7446 / $5.4389 |
| C | standard | hint | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $18.7024 / $37.2074 / $74.2174 |
| C | standard | hint | openai/gpt-6.2-sol | 94 | 376 / 376 | $9.4499 / $18.7024 / $37.2074 |
| C | standard | hint | **Total** | 188 | 752 / 752 | $28.1523 / $55.9098 / $111.4248 |
| C | standard | solo-seam | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $29.2068 / $58.2161 / $116.2348 |
| C | standard | solo-seam | openai/gpt-6.2-sol | 94 | 376 / 376 | $14.7021 / $29.2068 / $58.2161 |
| C | standard | solo-seam | **Total** | 188 | 752 / 752 | $43.9088 / $87.4229 / $174.4510 |
| C | standard | team | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $18.1347 / $36.0719 / $71.9464 |
| C | standard | team | openai/gpt-6.2-sol | 94 | 376 / 376 | $9.1660 / $18.1347 / $36.0719 |
| C | standard | team | **Total** | 188 | 752 / 752 | $27.3007 / $54.2066 / $108.0184 |
| C | standard | team-fullrecord | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $23.3108 / $46.4242 / $92.6509 |
| C | standard | team-fullrecord | openai/gpt-6.2-sol | 94 | 376 / 376 | $11.7541 / $23.3108 / $46.4242 |
| C | standard | team-fullrecord | **Total** | 188 | 752 / 752 | $35.0649 / $69.7349 / $139.0751 |
| C | standard | team-implicit | anthropic/claude-opus-5.5 | 94 | 376 / 376 | $17.6923 / $35.1871 / $70.1768 |
| C | standard | team-implicit | openai/gpt-6.2-sol | 94 | 376 / 376 | $8.9448 / $17.6923 / $35.1871 |
| C | standard | team-implicit | **Total** | 188 | 752 / 752 | $26.6371 / $52.8794 / $105.3639 |

| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | Cumulative model high estimate (stage cap on all row) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | openai/gpt-6.2-sol | 20 | 50 / 50 | $3.9529 / $7.8639 / $15.6858 | 20 | 50 / 50 | $3.9529 / $7.8639 / $15.6858 | $15.64 |
| A | anthropic/claude-opus-5.5 | 20 | 50 / 50 | $7.8639 / $15.6858 / $31.3296 | 20 | 50 / 50 | $7.8639 / $15.6858 / $31.3296 | $31.29 |
| A | all | 40 | 100 / 100 | $11.8168 / $23.5497 / $47.0153 | 40 | 100 / 100 | $11.8168 / $23.5497 / $47.0153 | $47.00 |
| B | openai/gpt-6.2-sol | 48 | 48 / 192 | $1.8970 / $3.6931 / $7.2854 | 68 | 98 / 242 | $5.8499 / $11.5570 / $22.9712 | $22.83 |
| B | anthropic/claude-opus-5.5 | 48 | 48 / 192 | $3.6931 / $7.2854 / $14.4701 | 68 | 98 / 242 | $11.5570 / $22.9712 / $45.7996 | $45.66 |
| B | all | 96 | 96 / 384 | $5.5901 / $10.9785 / $21.7555 | 136 | 196 / 484 | $17.4069 / $34.5282 / $68.7708 | $69.00 |
| C | openai/gpt-6.2-sol | 470 | 1880 / 1880 | $54.0169 / $107.0469 / $213.1067 | 538 | 1978 / 2122 | $59.8668 / $118.6039 / $236.0779 | $234.95 |
| C | anthropic/claude-opus-5.5 | 470 | 1880 / 1880 | $107.0469 / $213.1067 / $425.2264 | 538 | 1978 / 2122 | $118.6039 / $236.0779 / $471.0260 | $469.90 |
| C | all | 940 | 3760 / 3760 | $161.0638 / $320.1536 / $638.3331 | 1076 | 3956 / 4244 | $178.4707 / $354.6818 / $707.1039 | $705.00 |

Power source: `handoff.card.json`. Stage B exact looks: [3, 6, 12]; no optional non-binding futility. Stage C envelope (largest eligible card n, capped):

- standard: 94 pairs/model maximum. Lapse alternatives (control-only minus trap-only full-success rate, card n): probe-ref-trap lapse-10 0.083, 94; probe-hunt-trap lapse-10 0.167, 46; probe-ref-trap lapse-20 0.292, 26; probe-ref-trap lapse-30 0.292, 26; probe-hunt-trap lapse-20 0.333, 23; probe-hunt-trap lapse-30 0.583, 12; probe-ref-trap lapse-50 0.583, 12; probe-hunt-trap lapse-50 0.875, 8

Task sets (zero-based runner index): 0=A/standard/solo,team-fullrecord; 1=B/standard/probe-hunt-control,probe-hunt-trap; 2=B/standard/probe-ref-control,probe-ref-trap; 3=C/standard/team-fullrecord,team,hint,solo-seam,team-implicit.

## Suite: maximum envelope, one harness

| Family | Trials | Model / all seats | Cost low / expected / high | Model cap |
|---|---:|---:|---:|---:|
| handoff | 1076 | 3956 / 4244 | $178.4707 / $354.6818 / $707.1039 | $705.00 |
| **Total** | 1076 | 3956 / 4244 | $178.4707 / $354.6818 / $707.1039 | $705.00 |
