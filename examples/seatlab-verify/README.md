# Verify a Harbor team's recorded decisions in CI

This is a consumer repository. It uses [SeatLab's public action](https://github.com/PeterMakhnatch/harbor-seatlab) by immutable commit, not a script copied into Harbor. The [workflow](../../.github/workflows/verify-seatlab-study.yml) imports no code from this repository. It needs no model, Docker service, or new Harbor run. GitHub downloads the pinned action and checkout. The verification itself runs offline.

The example uses 2 native trials from one [published Harbor job](https://github.com/PeterMakhnatch/harbor-seatlab/blob/v0.11.0/examples/eval-lab/measured.md). There are 2 participants per trial. A single registered alignment check compares both roles. Three local input files suffice. `recorded-job/` does not contain raw chat, logs, authentication data, or executables. The [release](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/v0.11.0) also contains small native result projections for stock Eval Lab's loader.

The correct trusted study pin is `f289c8c89a12bcb8c25753a8b3f5a5ac389825baceba874921e6e2587556110d`. The positive action emits a JSON summary and offline HTML. A separate negative control supplies another pin for the **same data**. The workflow requires that the second call fail and produce no outputs. Do not treat that expected refusal as a participant or task failure.

To adopt the check, copy the positive workflow step, commit your sealed study with its inputs, and supply an independently trusted canonical study pin. Verification does not prove who generated the data. Protect your workflow and pin separately from unreviewed study updates.
