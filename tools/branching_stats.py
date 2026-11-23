"""Game-tree statistics from the saved games: branching factor and game length.

Replays every game JSON under ``games/`` (including ``games/samples/``) from its
``start_fen`` and asks the engine for the legal moves at each position. That gives
the *measured* branching factor the search actually faces, rather than an estimate.

The random opening plies are not replayed: ``start_fen`` is already the position
after them, so they are not part of the counts.

Usage, from the repository root::

    python -m tools.branching_stats
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from engine.board import Board

# Ply bands for the per-phase table. Bands are inclusive of the lower bound.
PLY_BANDS = ((0, 19), (20, 39), (40, 59), (60, 10_000))


def game_files(directory: Path) -> list[Path]:
    """All game JSON files under ``directory``, sorted for reproducible output."""
    return sorted(path for path in directory.rglob("*.json"))


def position_move_counts(path: Path) -> tuple[list[int], str | None]:
    """Legal-move count before every recorded move in one saved game.

    Returns the counts and the game result, or ``([], None)`` if the file does not
    look like a game record.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if "start_fen" not in data or "moves" not in data:
        return [], None
    board = Board.from_fen(data["start_fen"])
    counts: list[int] = []
    for entry in data["moves"]:
        legal = board.legal_moves()
        counts.append(len(legal))
        wanted = entry["move"]
        for move in legal:
            if move.to_iccs() == wanted:
                board.make_move(move)
                break
        else:
            raise ValueError(f"{path.name}: recorded move {wanted} is not legal")
    return counts, data.get("result")


def band_label(ply_index: int) -> str:
    """Which ply band a position belongs to, for the per-phase table."""
    for low, high in PLY_BANDS:
        if low <= ply_index <= high:
            return f"{low}-{high}" if high < 10_000 else f"{low}+"
    return "?"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="games")
    args = parser.parse_args()

    files = game_files(Path(args.directory))
    if not files:
        raise SystemExit(f"no game JSON files under {args.directory}")

    all_counts: list[int] = []
    game_lengths: list[int] = []
    results: dict[str, int] = {}
    by_band: dict[str, list[int]] = {band_label(low): [] for low, _ in PLY_BANDS}

    for path in files:
        counts, result = position_move_counts(path)
        if not counts:
            continue
        all_counts.extend(counts)
        game_lengths.append(len(counts))
        results[result or "unfinished"] = results.get(result or "unfinished", 0) + 1
        for index, count in enumerate(counts):
            by_band[band_label(index)].append(count)

    print(f"games: {len(game_lengths)}")
    print(
        f"positions: {len(all_counts)}   "
        f"average legal moves: {statistics.mean(all_counts):.2f}   "
        f"median: {statistics.median(all_counts):.0f}   "
        f"min {min(all_counts)}   max {max(all_counts)}"
    )
    print(
        f"game length: average {statistics.mean(game_lengths):.1f} plies   "
        f"median {statistics.median(game_lengths):.0f}   "
        f"min {min(game_lengths)}   max {max(game_lengths)}"
    )
    print("results:", ", ".join(f"{name} {count}" for name, count in sorted(results.items())))
    print("\nply band   positions   avg legal moves")
    for label, counts in by_band.items():
        if counts:
            print(f"{label:<10} {len(counts):>10}   {statistics.mean(counts):>15.2f}")


if __name__ == "__main__":
    main()
