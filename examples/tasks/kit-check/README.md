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
