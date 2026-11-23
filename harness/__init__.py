"""Match harness: play games, run tournaments, and aggregate the results.

* ``harness.match`` plays a single game and produces a Game JSON record.
* ``harness.tournament`` plays many games with swapped colours, in parallel.
* ``harness.results`` turns records into win/draw/loss tables and averages.

Everything above this package (experiments, server) uses these three modules.
"""
