# Held-out predictions stored before native collection

The [registered protocol](https://github.com/PeterMakhnatch/harbor-multiagent/blob/5ae95431d01d59a830dedd277e0bdeb7ac34e267/adapters/pettingzoo-seats/communication-experiment/held-out-protocol.md) fixes the 12 seeds, 5 teams and contrasts. `held-out-forecast.json` contains the predictions from the official MPE2 engine. Its SHA-256 is `87caff7d0ff2321a6a227a1b8a5ef9c287ad6895d1bce5b0561ba18164e6c76a`. Two fresh direct-engine processes produced identical bytes. Neither received any native trial path. All 60 native trial directories are absent when this pin is recorded. No task, seed or policy depends on the predictions.

```sh
python forecast.py predict --out fresh.json
shasum -a 256 fresh.json
# Only after collecting the registered native trials:
python reference.py "$ROOT" --cohort held-out --out reference.json
python forecast.py check "$ROOT" --forecast held-out-forecast.json \
  --expect-sha256 87caff7d0ff2321a6a227a1b8a5ef9c287ad6895d1bce5b0561ba18164e6c76a --reference reference.json --out check.json
```

The comparison retains disagreement as a result. Missing observations are unknown. Neither a direct-engine run nor its prediction counts as a native trial. Pairs share baseline trials and steps within a trial are not independent samples.
