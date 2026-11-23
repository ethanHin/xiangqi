"""Shared plumbing for the M6 experiments.

Nothing here decides an experiment; it handles the mechanics that both scripts need:

* ``--games/--quick/--seed/--workers/--out`` with validation (games must be even,
  because colours are swapped in pairs);
* a run directory ``experiments/results/<run-id>/`` with a ``games/`` subfolder;
* a streaming ``ProcessPoolExecutor`` runner that hands each finished game to a
  callback in the parent process, so a CSV row can be written and flushed
  immediately (an interrupted run keeps its results);
* the per-game and per-pairing CSV schemas, including per-side statistics;
* the Wilson interval, used because 10-game scores are wide;
* plots that are skipped with a warning when matplotlib is missing.

Spawn safety: the pool is created by the experiment's ``main()`` under a
``__main__`` guard, and the worker is ``harness.match.play_job`` (module level,
picklable ``MatchJob`` arguments).
"""

from __future__ import annotations

import csv
import json
import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bots.alphabeta import MATE_THRESHOLD
from harness.match import GameRecord, MatchJob, save_game

Z_95 = 1.96

# One row per game. The two sides are recorded separately, because a pairing can put
# a different bot or a different setting on each side.
GAME_FIELDS = [
    "run_id",
    "experiment",
    "pairing",
    "game_id",
    "seed",
    "red",
    "black",
    "result",
    "reason",
    "plies",
    "red_moves",
    "red_avg_depth",
    "red_avg_nodes",
    "red_avg_ms",
    "red_cap_hits",
    "red_depth_short",
    "black_moves",
    "black_avg_depth",
    "black_avg_nodes",
    "black_avg_ms",
    "black_cap_hits",
    "black_depth_short",
]

# One row per pairing. ``perspective`` names the side the W/D/L columns belong to.
SUMMARY_FIELDS = [
    "run_id",
    "experiment",
    "pairing",
    "perspective",
    "games",
    "wins",
    "draws",
    "losses",
    "win_rate",
    "score",
    "wilson_low",
    "wilson_high",
    "side_a",
    "a_avg_depth",
    "a_avg_nodes",
    "a_avg_ms",
    "a_cap_hits",
    "a_depth_short",
    "side_b",
    "b_avg_depth",
    "b_avg_nodes",
    "b_avg_ms",
    "b_cap_hits",
    "b_depth_short",
    "note",
]


@dataclass(frozen=True)
class RunPaths:
    """Where one experiment run writes its files."""

    run_id: str
    directory: Path
    games_csv: Path
    summary_csv: Path
    games_dir: Path


@dataclass
class SideTotals:
    """Totals for one configuration across the games of a pairing."""

    moves: int = 0
    depth_sum: float = 0.0
    nodes_sum: float = 0.0
    ms_sum: float = 0.0
    cap_hits: int = 0
    depth_short: int = 0
    early_mate_stops: int = 0

    def add(self, stats: SideStats) -> None:
        self.moves += stats.moves
        self.depth_sum += stats.avg_depth * stats.moves
        self.nodes_sum += stats.avg_nodes * stats.moves
        self.ms_sum += stats.avg_ms * stats.moves
        self.cap_hits += stats.cap_hits
        self.depth_short += stats.depth_short
        self.early_mate_stops += stats.early_mate_stops

    @property
    def avg_depth(self) -> float:
        return self.depth_sum / self.moves if self.moves else 0.0

    @property
    def avg_nodes(self) -> float:
        return self.nodes_sum / self.moves if self.moves else 0.0

    @property
    def avg_ms(self) -> float:
        return self.ms_sum / self.moves if self.moves else 0.0


@dataclass(frozen=True)
class SideStats:
    """Per-game statistics for one side of the board."""

    moves: int
    avg_depth: float
    avg_nodes: float
    avg_ms: float
    cap_hits: int
    depth_short: int
    early_mate_stops: int


# ---------------------------------------------------------------------------
# Arguments and run paths
# ---------------------------------------------------------------------------


def add_common_arguments(parser) -> None:
    """Add the flags both experiment scripts share."""
    parser.add_argument("--games", type=int, default=10, help="games per pairing (even)")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="2 games at low settings, to smoke-test the script only",
    )
    parser.add_argument("--seed", type=int, default=1, help="base seed for openings")
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="worker processes; time-limited rows are sensitive to CPU contention",
    )
    parser.add_argument(
        "--out",
        default="experiments/results",
        help="results folder (one subfolder per run)",
    )


def validate_common(parser, args) -> None:
    """Reject impossible settings before any game starts."""
    if args.games % 2:
        parser.error("--games must be even so colours are swapped evenly")
    if args.games < 2:
        parser.error("--games must be at least 2")
    if args.workers < 1:
        parser.error("--workers must be at least 1")


def new_run(script: str, out: str) -> RunPaths:
    """Create ``<out>/<script>-<timestamp>/`` and return its paths."""
    run_id = f"{script}-{time.strftime('%Y%m%d-%H%M%S')}"
    directory = Path(out) / run_id
    games_dir = directory / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        run_id=run_id,
        directory=directory,
        games_csv=directory / f"{run_id}-games.csv",
        summary_csv=directory / f"{run_id}-summary.csv",
        games_dir=games_dir,
    )


class CsvAppender:
    """Write rows one at a time and flush, so an interrupted run keeps its evidence."""

    def __init__(self, path: Path, fieldnames: Sequence[str]) -> None:
        self.path = path
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=list(fieldnames))
        self._writer.writeheader()
        self._file.flush()

    def write(self, row: dict[str, Any]) -> None:
        """Append one row and flush it to disk."""
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> CsvAppender:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Running jobs
# ---------------------------------------------------------------------------


def run_streaming(
    jobs: Sequence[MatchJob], workers: int, on_result: Callable[[GameRecord], None]
) -> None:
    """Play every job, calling ``on_result`` in this process as each game finishes.

    Results arrive out of order, which is the point: the callback writes a CSV row and
    flushes immediately, so a long run that is interrupted still has every finished
    game on disk.
    """
    from harness.match import play_job

    if workers <= 1:
        for job in jobs:
            on_result(play_job(job))
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(play_job, job) for job in jobs]
        for future in as_completed(futures):
            on_result(future.result())


def save_record(record: GameRecord, run: RunPaths) -> None:
    """Write the game JSON into the run's (gitignored) games folder."""
    save_game(record, run.games_dir)


# ---------------------------------------------------------------------------
# Per-game statistics
# ---------------------------------------------------------------------------


def spec_label(spec: dict[str, Any]) -> str:
    """Short, unambiguous label for a player spec, e.g. ``alphabeta{"depth":3,...}``."""
    params = json.dumps(spec.get("params") or {}, sort_keys=True, separators=(",", ":"))
    return f"{spec.get('type')}{params}"


def side_stats(
    record: GameRecord,
    is_red: bool,
    *,
    cap_ms: float | None = None,
    configured_depth: int | None = None,
) -> SideStats:
    """Average depth, nodes, and time for one side's moves in one game.

    ``cap_ms`` counts moves whose measured time reached the per-move cap.
    ``configured_depth`` counts moves that reported a *lower* depth than configured
    (excluding early stops on a proven mate, which are correct alpha-beta behaviour).
    """
    start_is_red = len(record.opening_moves) % 2 == 0
    moves = [
        move
        for index, move in enumerate(record.moves)
        if (index % 2 == 0) == (start_is_red == is_red)
    ]
    if not moves:
        return SideStats(0, 0.0, 0.0, 0.0, 0, 0, 0)

    cap_hits = sum(1 for move in moves if cap_ms is not None and move.time_ms >= cap_ms)
    depth_short = 0
    early_mate_stops = 0
    if configured_depth is not None:
        for move in moves:
            if move.depth >= configured_depth:
                continue
            if move.eval is not None and abs(move.eval) >= MATE_THRESHOLD:
                early_mate_stops += 1
            else:
                depth_short += 1
    return SideStats(
        moves=len(moves),
        avg_depth=sum(move.depth for move in moves) / len(moves),
        avg_nodes=sum(move.nodes for move in moves) / len(moves),
        avg_ms=sum(move.time_ms for move in moves) / len(moves),
        cap_hits=cap_hits,
        depth_short=depth_short,
        early_mate_stops=early_mate_stops,
    )


def game_row(
    run: RunPaths,
    experiment: str,
    pairing: str,
    record: GameRecord,
    red_stats: SideStats,
    black_stats: SideStats,
) -> dict[str, Any]:
    """Build one row for the per-game CSV."""
    return {
        "run_id": run.run_id,
        "experiment": experiment,
        "pairing": pairing,
        "game_id": record.id,
        "seed": record.seed,
        "red": spec_label(record.red),
        "black": spec_label(record.black),
        "result": record.result,
        "reason": record.reason,
        "plies": record.plies,
        "red_moves": red_stats.moves,
        "red_avg_depth": round(red_stats.avg_depth, 3),
        "red_avg_nodes": round(red_stats.avg_nodes, 1),
        "red_avg_ms": round(red_stats.avg_ms, 2),
        "red_cap_hits": red_stats.cap_hits,
        "red_depth_short": red_stats.depth_short,
        "black_moves": black_stats.moves,
        "black_avg_depth": round(black_stats.avg_depth, 3),
        "black_avg_nodes": round(black_stats.avg_nodes, 1),
        "black_avg_ms": round(black_stats.avg_ms, 2),
        "black_cap_hits": black_stats.cap_hits,
        "black_depth_short": black_stats.depth_short,
    }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def wilson_interval(score: float, games: int, z: float = Z_95) -> tuple[float, float]:
    """Approximate 95% Wilson interval for a score in [0, 1] over ``games`` games.

    The score counts a draw as half a win, so it is a proportion; Wilson is used
    instead of the normal approximation because it behaves at 0, 1, and small n.
    """
    if games <= 0:
        return (0.0, 1.0)
    denominator = 1.0 + z * z / games
    centre = (score + z * z / (2 * games)) / denominator
    half = z * math.sqrt(score * (1 - score) / games + z * z / (4 * games * games)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def same_spec(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """True if two player specs describe the same bot with the same parameters."""
    return first.get("type") == second.get("type") and (first.get("params") or {}) == (
        second.get("params") or {}
    )


def outcome_for(record: GameRecord, is_red: bool) -> int:
    """+1 if ``record`` was won by the side ``is_red`` selects, 0 for a draw, -1 loss."""
    if record.result == "draw":
        return 0
    red_won = record.result == "red_win"
    return 1 if red_won == is_red else -1


def summary_row(
    run: RunPaths,
    experiment: str,
    pairing: str,
    perspective: str,
    wins: int,
    draws: int,
    losses: int,
    a: SideTotals,
    b: SideTotals,
    side_a: str,
    side_b: str,
    note: str = "",
) -> dict[str, Any]:
    """Build one row for the summary CSV."""
    games = wins + draws + losses
    score = (wins + 0.5 * draws) / games if games else 0.0
    low, high = wilson_interval(score, games)
    return {
        "run_id": run.run_id,
        "experiment": experiment,
        "pairing": pairing,
        "perspective": perspective,
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "win_rate": round(wins / games, 4) if games else 0.0,
        "score": round(score, 4),
        "wilson_low": round(low, 4),
        "wilson_high": round(high, 4),
        "side_a": side_a,
        "a_avg_depth": round(a.avg_depth, 3),
        "a_avg_nodes": round(a.avg_nodes, 1),
        "a_avg_ms": round(a.avg_ms, 2),
        "a_cap_hits": a.cap_hits,
        "a_depth_short": a.depth_short,
        "side_b": side_b,
        "b_avg_depth": round(b.avg_depth, 3),
        "b_avg_nodes": round(b.avg_nodes, 1),
        "b_avg_ms": round(b.avg_ms, 2),
        "b_cap_hits": b.cap_hits,
        "b_depth_short": b.depth_short,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def import_pyplot():
    """Return ``matplotlib.pyplot`` or None (with a warning) when it is missing."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed: CSV files were written, plots skipped.")
        return None
    return plt
