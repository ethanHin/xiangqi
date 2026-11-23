"""Measure what each evaluator costs: evaluation time, search speed, and features.

Prints three tables:

1. cost per evaluation call for every feature and every named evaluator;
2. search speed (nodes per second and ms per move) for each evaluator at a fixed
   depth on the middlegame set;
3. the same nodes count, to show that the evaluators differ in cost, not in how much
   of the tree they search.

Usage, from the repository root::

    python -m tools.eval_cost            # 5 positions, depth 3
    python -m tools.eval_cost --limit 10 --depth 3
"""

from __future__ import annotations

import argparse
import time

from bots.alphabeta import search_at_depth
from engine.board import Board
from eval.evaluator import FEATURES, available_evaluators, build_evaluator
from tests.helpers import load_fens

EVALUATION_SAMPLES = 2000


def us_per_evaluation(evaluator, board: Board) -> float:
    """Average microseconds for one evaluation call on ``board``."""
    started = time.perf_counter()
    for _ in range(EVALUATION_SAMPLES):
        evaluator(board)
    return (time.perf_counter() - started) / EVALUATION_SAMPLES * 1e6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="positions for the search tables")
    parser.add_argument("--depth", type=int, default=3)
    args = parser.parse_args()

    fens = load_fens("positions_middlegame.txt")[: args.limit]
    boards = [Board.from_fen(fen) for fen in fens]
    reference = boards[0]

    print(f"Evaluation cost (start/middlegame position, {EVALUATION_SAMPLES} calls)")
    print(f"{'feature / evaluator':<26}{'us/call':>10}{'calls/s':>12}")
    print("-" * 48)
    for name, feature in FEATURES.items():
        cost = us_per_evaluation(feature, reference)
        print(f"{name:<26}{cost:>10.2f}{1e6 / cost:>12,.0f}")
    evaluators = {name: build_evaluator(name) for name in available_evaluators()}
    for name, evaluator in evaluators.items():
        cost = us_per_evaluation(evaluator, reference)
        print(f"{name:<26}{cost:>10.2f}{1e6 / cost:>12,.0f}")

    print(f"\nSearch speed, depth {args.depth}, {len(boards)} middlegame positions")
    print(f"{'evaluator':<26}{'nodes':>10}{'nodes/s':>12}{'ms/move':>10}")
    print("-" * 58)
    for name in available_evaluators():
        total_nodes = 0
        total_ms = 0.0
        for board in boards:
            result = search_at_depth(board, args.depth, evaluator=name)
            total_nodes += result.nodes
            total_ms += result.elapsed_ms
        per_position = max(len(boards), 1)
        nodes_per_second = total_nodes / (total_ms / 1000.0) if total_ms else 0.0
        print(
            f"{name:<26}{total_nodes:>10,}{nodes_per_second:>12,.0f}"
            f"{total_ms / per_position:>10.1f}"
        )


if __name__ == "__main__":
    main()
