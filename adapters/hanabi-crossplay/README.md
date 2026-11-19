# Full Hanabi in native Olam Harbor seats

Use this task to test whether 2 policies still coordinate after you replace one partner. OpenSpiel owns the rules, deal and score. Each Harbor seat has a private endpoint. The world accepts only a legal action tied to that seat's current view. The verifier replays the accepted actions from the registered deck.

Start with the [results](results.md) and the [registered protocol](protocol.md). The 2 example policies interpret hints differently. They are test instruments, not strong Hanabi agents.

## Repeat one deck

Use Python 3.12, Docker, `PyYAML`, and a checkout of [stock Olam](https://github.com/olamlabs/harbor-multiagent/tree/6aeb609e31fd77e5f89ce3bcb018db8045520fb7) with its dependencies installed. Install [SeatLab](https://github.com/PeterMakhnatch/harbor-seatlab) in the analysis environment for episode export. Run this command block from this adapter directory:

```sh
run=$(mktemp -d)
python study.py prepare "$run/native" --decks 1
python run_deck.py "$run/native" 0 --harbor "$STOCK_OLAM/.venv/bin/harbor"
curl -fLsS https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.8.0/hanabi-predictions-64.json -o "$run/forecasts.json"
python collect.py "$run/native" "$run/forecasts.json" --episodes "$run/episodes" > "$run/collected.json"
```

Four fresh trials share the physical deck, not a trajectory. The collector reports missing, invalid or disagreeing cases explicitly. Read its counts; a successful CLI exit is not a measured game. The [Linux replication workflow](https://github.com/PeterMakhnatch/harbor-seatlab/blob/3fcacc195006cabf126198793553223a3dd6e5e5/.github/workflows/native-hanabi.yml) pins the public forecast bytes and runs this path end to end.

For a larger study, prepare once and send disjoint deck IDs to different workers. `run_deck.py` claims each deck once in the shared preparation directory. Use one configured Docker runner per worker. `python study.py simulate NEW.json` computes the full forecast without containers if `open_spiel==2.0.2` is installed. That simulation is not a new native trial.
