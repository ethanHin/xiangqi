"""Generate the fixed position sets used by the search tests and stats tools.

Writes three files under ``tests/``:

* ``positions_middlegame.txt`` -- 60 FENs from seeded random playouts. These are
  the "typical position" set for node-count comparisons.
* ``positions_transpositions.txt`` -- sparse endgames where the pieces can be
  played in many orders, so different lines reach the same position. This is what
  the transposition-table test measures.
* ``positions_quiet.txt`` -- sparse positions in which neither side can capture
  anything, so quiescence has nothing to search and must not change the value.

Every generated position is checked before it is written: at least one legal move
exists, the side to move is not already in check (for the sparse sets), and (for
the quiet set) no capture is available at the root or after any legal move.

Run from the repository root::

    python -m tools.make_positions
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from pathlib import Path

from engine.board import Board
from engine.types import BOARD_FILES, BOARD_RANKS, FILE_LETTERS

MIDDLEGAME_COUNT = 60
MIDDLEGAME_MIN_PLIES = 16
MIDDLEGAME_MAX_PLIES = 30

# Sparse endgames with generals plus two or three other pieces. Few pieces and free
# space mean many move orders converge on the same position.
TRANSPOSITION_PIECES: tuple[dict[str, str], ...] = (
    {"d0": "K", "e9": "k", "a0": "R", "i0": "R"},
    {"d0": "K", "e9": "k", "a0": "R", "a9": "r"},
    {"d0": "K", "e9": "k", "b0": "N", "h9": "n"},
    {"d0": "K", "e9": "k", "c0": "C", "g9": "c"},
    {"d0": "K", "e9": "k", "a0": "R", "i0": "N", "a9": "r"},
    {"e0": "K", "d9": "k", "b0": "R", "h0": "R"},
    {"e0": "K", "d9": "k", "b2": "C", "h2": "C", "e6": "p"},
    {"d0": "K", "e9": "k", "c0": "N", "g0": "N", "e5": "r"},
    {"d0": "K", "e9": "k", "a0": "R", "b9": "n"},
    {"e0": "K", "d9": "k", "a1": "C", "i1": "R", "e8": "a"},
)

# Quiet positions: each side's soldiers start on opposite sides of the river and far
# across the board, so nothing can reach anything within a shallow search.
QUIET_PIECES: tuple[dict[str, str], ...] = (
    {"d0": "K", "e9": "k", "a3": "P", "i6": "p"},
    {"d0": "K", "e9": "k", "a4": "P", "i5": "p"},
    {"e0": "K", "d9": "k", "c3": "P", "g6": "p"},
    {"e0": "K", "d9": "k", "i3": "P", "a6": "p"},
    {"d0": "K", "e9": "k", "d2": "A", "a3": "P", "i6": "p"},
    {"e0": "K", "d9": "k", "f2": "A", "c3": "P", "g6": "p"},
)


def fen_from_pieces(pieces: dict[str, str], side_to_move: str = "w") -> str:
    """Build a FEN from ``{"e0": "K", "a3": "P"}`` (uppercase Red, lowercase Black)."""
    rows = []
    for rank in range(BOARD_RANKS - 1, -1, -1):
        row: list[str] = []
        empty = 0
        for file in range(BOARD_FILES):
            char = pieces.get(f"{FILE_LETTERS[file]}{rank}")
            if char is None:
                empty += 1
                continue
            if empty:
                row.append(str(empty))
                empty = 0
            row.append(char)
        if empty:
            row.append(str(empty))
        rows.append("".join(row))
    return "/".join(rows) + f" {side_to_move} - - 0 1"


def middlegame_positions() -> list[str]:
    """Seeded random playouts, keeping one position per game to vary the material."""
    positions: list[str] = []
    seen: set[str] = set()
    seed = 0
    while len(positions) < MIDDLEGAME_COUNT:
        seed += 1
        rng = random.Random(seed)
        board = Board.start()
        plies = MIDDLEGAME_MIN_PLIES + (seed % (MIDDLEGAME_MAX_PLIES - MIDDLEGAME_MIN_PLIES + 1))
        for _ in range(plies):
            legal = board.legal_moves()
            if not legal:
                break
            board.make_move(rng.choice(legal))
        fen = board.to_fen()
        if fen in seen:
            continue
        if not _is_playable(board):
            continue
        seen.add(fen)
        positions.append(fen)
    return positions


def _is_playable(board: Board) -> bool:
    """A usable test position: moves exist and the side to move is not in check."""
    return bool(board.legal_moves()) and not board.is_in_check()


def _has_capture(moves: Iterable) -> bool:
    return any(move.captured for move in moves)


def _is_quiet(fen: str) -> bool:
    """True if no capture is available now or after any legal move."""
    board = Board.from_fen(fen)
    if _has_capture(board.legal_moves()):
        return False
    for move in board.legal_moves():
        board.make_move(move, record_check=False)
        try:
            if _has_capture(board.legal_moves()):
                return False
        finally:
            board.unmake_move()
    return True


def _validate(fen: str, *, quiet: bool = False) -> str:
    board = Board.from_fen(fen)
    if not board.legal_moves():
        raise ValueError(f"position has no legal moves: {fen}")
    if board.is_in_check():
        raise ValueError(f"side to move is in check: {fen}")
    if quiet and not _is_quiet(fen):
        raise ValueError(f"position contains a capture: {fen}")
    return fen


def _write(path: Path, comment: str, fens: Iterable[str]) -> None:
    lines = [f"# {comment}", "# One FEN per line; generated by python -m tools.make_positions."]
    lines.extend(fens)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    tests = Path("tests")

    middlegame = [_validate(fen) for fen in middlegame_positions()]
    _write(
        tests / "positions_middlegame.txt",
        "Mixed middlegame positions from seeded random playouts.",
        middlegame,
    )

    transpositions = [_validate(fen_from_pieces(pieces)) for pieces in TRANSPOSITION_PIECES]
    _write(
        tests / "positions_transpositions.txt",
        "Sparse endgames with many move-order transpositions.",
        transpositions,
    )

    quiet = [_validate(fen_from_pieces(pieces), quiet=True) for pieces in QUIET_PIECES]
    _write(
        tests / "positions_quiet.txt",
        "Positions with no captures available: quiescence must not change the value.",
        quiet,
    )

    print(f"middlegame {len(middlegame)}, transpositions {len(transpositions)}, quiet {len(quiet)}")


if __name__ == "__main__":
    main()
