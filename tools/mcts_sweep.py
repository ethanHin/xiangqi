"""Informational MCTS sweeps: iteration count and exploration constant.

Runs 10-game matches against the greedy bot, colours swapped, and reports per-arm
W/D/L plus the statistics the report needs: iterations per second, average tree
depth, and average time per move, all measured on MCTS moves only (the greedy bot
reports ``nodes=0``).

These runs are informational: 10 games cannot separate the settings, and the
defaults were chosen by reasoning, not by these tables.

Usage, from the repository root::

    python -m tools.mcts_sweep --sweep iterations
    python -m tools.mcts_sweep --sweep exploration
"""

from __future__ import annotations

import argparse

from harness.match import GameRecord
from harness.results import score_for
from harness.tournament import TournamentConfig, run_match

SEED = 1
OPENING_PLIES = 6
# Large enough that the iteration budget always decides; the bot clamps it to its
# own 10 s ceiling, which is far more than any arm needs.
TIME_LIMIT_MS = 10_000.0

ITERATION_ARMS = (100, 400, 1600)
EXPLORATION_ARMS = (0.7, 1.4, 2.0)
EXPLORATION_ITERATIONS = 400


def mcts_moves(records: list[GameRecord]) -> list:
    """Moves made by the MCTS side (greedy reports ``nodes=0``)."""
    return [move for record in records for move in record.moves if move.nodes > 0]


def report(arm_label: str, records: list[GameRecord], bot_type: str, params: dict) -> None:
    """Print one row's worth of statistics for a sweep arm."""
    score = score_for(records, bot_type, params)
    moves = mcts_moves(records)
    total_time_ms = sum(move.time_ms for move in moves)
    iterations = sum(move.nodes for move in moves)
    iterations_per_second = iterations / (total_time_ms / 1000.0) if total_time_ms else 0.0
    avg_depth = sum(move.depth for move in moves) / len(moves) if moves else 0.0
    avg_ms = total_time_ms / len(moves) if moves else 0.0
    avg_plies = sum(record.plies for record in records) / len(records) if records else 0.0
    print(
        f"{arm_label:<14}{score.wins:>3}{score.draws:>3}{score.losses:>3}"
        f"{score.win_rate:>9.0%}{iterations_per_second:>10,.0f}"
        f"{avg_depth:>10.2f}{avg_ms:>10.1f}{avg_plies:>10.1f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", choices=("iterations", "exploration"), required=True)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    if args.games % 2:
        raise SystemExit("--games must be even so colours are swapped evenly")

    print(
        f"MCTS vs greedy, {args.games} games per arm, seed {SEED}, "
        f"{OPENING_PLIES}-ply openings, {args.workers} workers"
    )
    print(
        f"{'arm':<14}{'W':>3}{'D':>3}{'L':>3}{'win rate':>9}"
        f"{'iters/s':>10}{'avg depth':>10}{'avg ms':>10}{'avg plies':>10}"
    )
    print("-" * 72)

    if args.sweep == "iterations":
        for iterations in ITERATION_ARMS:
            params = {"iterations": iterations}
            records = run_match(
                {"type": "mcts", "params": params},
                {"type": "greedy", "params": {}},
                TournamentConfig(
                    games=args.games,
                    seed=SEED,
                    opening_plies=OPENING_PLIES,
                    move_time_limit_ms=TIME_LIMIT_MS,
                    workers=args.workers,
                ),
            )
            report(f"{iterations} iters", records, "mcts", params)
    else:
        for exploration in EXPLORATION_ARMS:
            params = {"iterations": EXPLORATION_ITERATIONS, "exploration": exploration}
            records = run_match(
                {"type": "mcts", "params": params},
                {"type": "greedy", "params": {}},
                TournamentConfig(
                    games=args.games,
                    seed=SEED,
                    opening_plies=OPENING_PLIES,
                    move_time_limit_ms=TIME_LIMIT_MS,
                    workers=args.workers,
                ),
            )
            report(f"c={exploration}", records, "mcts", params)


if __name__ == "__main__":
    main()
