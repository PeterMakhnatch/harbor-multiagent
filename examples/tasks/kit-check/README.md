# Kit Check: a model-free coordination control

Two reviewers see different approval lists. One builder chooses a kit. Each world has one kit on both lists.

This example runs 3 isolated Harbor seats in one Docker Compose project. The reviewers submit their lists to `main`. The builder then requests its observation and submits a fixed batch of choices. The verifier checks the choices against the original lists, not against agreement or the world’s recorded score.

## Run and replay

Use this fork in a separate checkout. Do not replace an installed Harbor CLI. Start Docker and use `uv >= 0.13.5`.

```bash
uv sync --locked
uv run --no-sync python examples/configs/features/kit_check_controls.py run /tmp/kit-check-study
python3 examples/configs/features/kit_check_controls.py replay /tmp/kit-check-study
```

For `run`, use a new directory. The runner starts 4 local Docker jobs in order: visible, silent, silent, visible. It uses the built-in oracle in all seats; these are fixed policies with no model calls. The Docker build uses a pinned multi-architecture Python base image. `replay` uses only retained files and the checked-in mode overlays. It needs no Docker daemon or Harbor import.

## What stays fixed

- Each trial covers the same 24 ordered triples of distinct kits. The reviewers and builder use the same scripts in both modes. Only a Compose overlay on `main` changes the visibility of both reviewer lists in the builder’s observation.
- In visible mode the builder chooses the intersection. Expect `24/24`. In silent mode both lists in its observation are `null`. The policy always chooses `K1`; expect `6/24` because the correct kits are evenly distributed.
- The world records the reviews, observation, and choice for every seed in `artifacts/logs/world/kit-check.json`. The replay checks both modes against the protocol, matches the native locks, and requires byte-identical world artifacts for repeated modes.

This establishes a coordination and evidence control for the supplied scripts. It does not estimate model performance, a team advantage over one model, or security against adversarial participants. The world endpoints are an observation protocol for these scripts, not access control for untrusted players.

## Run with real seat models

Use a current Codex CLI signed in through ChatGPT. The runner refuses an API-key login. It runs one Codex turn per seat on the host. The model receives only its instructions and game observation. It has no task shell or Docker credentials. The host submits only validated kit labels to that seat's endpoint.

Choose a native Codex model ID and use a new output directory for each trial:

```bash
uv run --no-sync python examples/configs/features/kit_check_subscription.py \
  visible /tmp/kit-check-real-visible-1 --model "${CODEX_MODEL:?Set a native Codex model ID}"
```

Repeat in the order `visible`, `silent`, `silent`, `visible`. On Peter's workstation, run each command through `/tmp/crucible-docker-slot.sh`. A trial uses the same model and role instructions in both modes. Only the world reports change. The builder prompt has no scripted `K1` fallback. A model may still choose `K1` for every hidden case. The 24 cases in one trial are not independent model calls.

The receipt field `builder_choices` separates those policies. A reward of `0.25` can come from always choosing `K1` or from repeating `K1K2K3K4`. Both can score 6 of 24 because the answer key is balanced. Do not read that reward as partial use of the hidden reviews.

Use `--require-freeze` with a Harbor build that records seat freezes. This checks that all seats report a successful freeze after play and before the verifier starts. It does not certify the shared world or an isolated verifier.
