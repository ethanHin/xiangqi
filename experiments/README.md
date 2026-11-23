# Experiments

Two scripts, both driving the stock tournament harness (no separate rules or search
code), with the output meant to be quoted in the report.

```
experiments/
  depth_scaling.py         # depth d vs depth d+1, fixed depth
  heuristic_comparison.py  # four evaluators round-robin + two equal-time rows
  common.py                # flags, streaming CSV, Wilson interval, plots
  results/<run-id>/        # one folder per run
    <run-id>-games.csv     # one row per finished game, flushed as it finishes
    <run-id>-summary.csv   # one row per pairing
    *.png                  # plots (skipped if matplotlib is missing)
    games/*.json           # Game JSON per game (gitignored)
```

## Reproducing

Everything runs from the repository root, with `.venv` active (`pip install -e ".[dev]"`
plus `matplotlib` if you want plots).

### Depth scaling

```bash
# the run recorded in the log (3 pairings, 30 games)
python -m experiments.depth_scaling --depths 1,2,3,4 --games 10 --workers 8

# smoke test only: 2 games, depths 1 and 2, deleted afterwards
python -m experiments.depth_scaling --quick
```

Alpha-beta with the `full` evaluator at a fixed depth and `time_limit_ms=None` on
both sides, so there is no clock at all and depth is the only variable. Each
consecutive pair in `--depths` is a pairing; colours are swapped within the pairing
and both games of a pair share their opening seed. The script asserts that every
reported depth equals the configured one, except when the search stopped early on a
proven mate (counted, not treated as an error).

### Heuristic comparison

```bash
# the run recorded in the log (6 round-robin pairings + rows A and B, 80 games)
python -m experiments.heuristic_comparison --games 10 --workers 8

# smoke test only: 2 games, depth 1, 100 ms time rows
python -m experiments.heuristic_comparison --quick
```

Part 1 is the four evaluators round-robin at `--depth 3` with a `--safety-ms 5000`
cap (never approached in practice; the cap-hit counts are in the summary). Part 2
adds row A (MCTS vs alpha-beta at `--time-ms 750`, MCTS iteration cap set far above
what fits so time decides) and row B (alpha-beta `full` vs `material` at the same
time budget). `--skip-time-rows` runs only the round-robin.

### Flags shared by both scripts

| flag | default | meaning |
|---|---|---|
| `--games` | 10 | games per pairing; must be even (colours are swapped) |
| `--quick` | off | 2 games at low settings, smoke test only |
| `--seed` | 1 | base seed; the two games of a colour-swapped pair share an opening |
| `--workers` | 8 | worker processes; time-limited rows are sensitive to CPU contention |
| `--out` | `experiments/results` | results folder; a timestamped run folder is created inside |

## How long a run takes

Measured on Windows 11, CPython 3.15.0, 16 logical CPUs, 8 workers (the same machine
as the log entries). `--games N` means **N games per pairing**, so the total is N x the
number of pairings (3 for depth 1-4, 8 for the heuristic script).

| script | default run | `--games 10` | `--games 50` | `--games 100` |
|---|---|---|---|---|
| `depth_scaling --depths 1,2,3,4` | 30 games | 132 s (4.4 s/game) | **495 s = 8:15** (150 games, 3.3 s/game) | ~17 min (300 games, estimate) |
| `heuristic_comparison` | 80 games | 472 s (5.9 s/game) | ~25 min (400 games, estimate) | **3,028 s = 50:27** (800 games, 3.8 s/game) |

The measured 50- and 100-game runs are marked in bold; the others are linear
extrapolations of the measured ones. The per-game average hides a wide spread: a
depth-1-vs-2 game is ~1 s, a depth-3-vs-4 game is ~40 s, and the equal-time rows are
~45 s each because both bots spend the full 750 ms. `--quick` takes a few seconds for
either script.

## Reading the CSVs

`<run-id>-games.csv` -- one row per game. `red`/`black` hold the full player spec;
the per-side columns are averages over that side's moves (`avg_depth`, `avg_nodes`,
`avg_ms`), `cap_hits` counts moves whose wall time reached the per-move cap, and
`depth_short` counts moves that reported less than the configured depth (excluding
early stops on a proven mate).

`<run-id>-summary.csv` -- one row per pairing. `perspective` names the side the
W/D/L and score columns belong to. `score` counts a draw as half a win;
`wilson_low`/`wilson_high` is the approximate 95% Wilson interval on that score,
which is wide at 10 games and printed in every report. `side_a`/`side_b` name the
two configurations, and the `a_*`/`b_*` columns are their per-side averages.

## Caveats

* **10 games per pairing cannot rank anything.** The default run exists to check the
  pipeline and show the shape of the effect; every entry in `docs/experiment_log.md`
  carries that warning. Use `--games 50` or `--games 100` for a claim.
* Time-limited rows (A and B) depend on CPU contention: run them with the same
  `--workers` and without other heavy work if the numbers must be comparable.
* Nothing here tunes parameters. The defaults in the code were fixed before these
  runs, and the summaries are reported as measured.
