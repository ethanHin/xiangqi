"""The M6 experiments: depth scaling and heuristic comparison.

Both scripts drive the existing tournament harness (they do not reimplement any rules
or search) and write their evidence into ``experiments/results/<run-id>/``:

* ``<run-id>-games.csv`` -- one row per finished game, flushed as each game finishes,
  so an interrupted long run keeps what it already played;
* ``<run-id>-summary.csv`` -- one row per pairing with W/D/L, score, a Wilson interval
  and per-side averages;
* ``games/`` -- the Game JSON for every game (gitignored);
* ``*.png`` -- plots, if matplotlib is installed.

``common`` holds the argument parsing, the streaming runner, the CSV schema, the
Wilson interval, and the optional plotting, so the two experiment scripts read as
just their experimental design.
"""
