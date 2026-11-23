"""Measure node counts for each M3 search enhancement on the fixed position sets.

Prints one table per position set. The point is to show, on identical positions,
that each enhancement reduces work without changing the score:

    alpha-beta      vs plain minimax        -> same value, far fewer nodes
    + move ordering vs alpha-beta           -> same value, fewer nodes
    + transposition table                   -> same value, fewer nodes (sparse sets)
    + quiescence                            -> extra nodes, reported for reference

Typical use (from the repository root)::

    python -m tools.search_stats            # sample of 20 positions, depth 3
    python -m tools.search_stats --limit 60 --depth 3 --skip-minimax

The full 60-position minimax run takes a few minutes in pure Python, which is why
``--limit`` exists.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field

from bots.alphabeta import reference_minimax, search_at_depth
from engine.board import Board
from tests.helpers import load_fens


@dataclass
class Row:
    """One configuration's totals across a position set."""

    label: str
    nodes: int = 0
    elapsed_ms: float = 0.0
    values: list[int] = field(default_factory=list)


def measure(fens: list[str], depth: int, skip_minimax: bool) -> list[Row]:
    """Run every configuration over ``fens`` and collect totals."""
    rows: list[Row] = []
    if not skip_minimax:
        rows.append(Row("plain minimax", values=[]))
    rows.append(Row("alpha-beta", values=[]))
    rows.append(Row("+ ordering", values=[]))
    rows.append(Row("+ ordering + TT", values=[]))
    rows.append(Row("+ ordering + TT + quiescence", values=[]))

    for fen in fens:
        board = Board.from_fen(fen)
        for row in rows:
            if row.label == "plain minimax":
                counter = [0]
                started = time.perf_counter()
                value = reference_minimax(board, depth, counter=counter)
                row.elapsed_ms += (time.perf_counter() - started) * 1000.0
                row.nodes += counter[0]
                row.values.append(value)
                continue
            result = search_at_depth(
                board,
                depth,
                use_ordering="ordering" in row.label,
                use_tt="TT" in row.label,
                quiescence="quiescence" in row.label,
            )
            row.nodes += result.nodes
            row.elapsed_ms += result.elapsed_ms
            row.values.append(result.value)
    return rows


def print_table(title: str, rows: list[Row], positions: int, depth: int) -> None:
    """Print a fixed-width table with node totals and value agreement."""
    reference = rows[0].values or []
    print(f"\n{title}: {positions} positions, depth {depth}")
    print(f"{'configuration':<32}{'nodes':>12}{'avg nodes':>12}{'time s':>10}{'same value':>12}")
    print("-" * 78)
    for row in rows:
        agrees = "yes" if row.values == reference else "NO"
        print(
            f"{row.label:<32}{row.nodes:>12,}{row.nodes / max(positions, 1):>12,.0f}"
            f"{row.elapsed_ms / 1000.0:>10.2f}{agrees:>12}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="positions per set (0 = all)")
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--transposition-depth", type=int, default=4)
    parser.add_argument("--skip-minimax", action="store_true")
    args = parser.parse_args()

    middlegame = load_fens("positions_middlegame.txt")
    transpositions = load_fens("positions_transpositions.txt")
    if args.limit:
        middlegame = middlegame[: args.limit]

    rows = measure(middlegame, args.depth, args.skip_minimax)
    print_table("Middlegame set", rows, len(middlegame), args.depth)

    # The transposition set exists to show the table working, so only compare the
    # two table configurations there; minimax and quiescence add nothing.
    transposition_rows = measure(transpositions, args.transposition_depth, skip_minimax=True)
    transposition_rows = [
        row
        for row in transposition_rows
        if row.label in {"alpha-beta", "+ ordering", "+ ordering + TT"}
    ]
    print_table(
        "Transposition set",
        transposition_rows,
        len(transpositions),
        args.transposition_depth,
    )


if __name__ == "__main__":
    main()
