"""Helpers shared by the engine tests.

``board_from`` builds a position from named squares, which keeps the quirk tests
readable: a horse-leg test says exactly where the horse and the blocker are
instead of hiding them inside a 10-field FEN string.
"""

from __future__ import annotations

from pathlib import Path

from engine.board import Board
from engine.types import (
    BOARD_FILES,
    BOARD_RANKS,
    BOARD_SQUARES,
    EMPTY,
    FILE_LETTERS,
    Color,
    Move,
    file_of,
    make_piece,
    piece_color,
    piece_kind,
    rank_of,
    square,
)

# Fixed position sets live next to the tests; generate them with
# ``python -m tools.make_positions``.
POSITIONS_DIR = Path(__file__).parent


def load_fens(name: str) -> list[str]:
    """Read a FEN list file (one FEN per line, ``#`` comments ignored)."""
    text = (POSITIONS_DIR / name).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def board_from(pieces: dict[str, str], side_to_move: str = "w") -> Board:
    """Build a board from ``{"e1": "N", "e2": "P"}``.

    Uppercase letters are Red pieces, lowercase are Black (k a b n r c p).
    ``side_to_move`` is ``"w"`` for Red or ``"b"`` for Black.
    """
    rows: list[str] = []
    for rank in range(BOARD_RANKS - 1, -1, -1):
        row: list[str] = []
        empty_run = 0
        for file in range(BOARD_FILES):
            char = pieces.get(f"{FILE_LETTERS[file]}{rank}")
            if char is None:
                empty_run += 1
                continue
            if empty_run:
                row.append(str(empty_run))
                empty_run = 0
            row.append(char)
        if empty_run:
            row.append(str(empty_run))
        rows.append("".join(row))
    return Board.from_fen("/".join(rows) + f" {side_to_move} - - 0 1")


def move_names(moves: list[Move]) -> set[str]:
    """ICCS names of a list of moves, for set comparisons."""
    return {move.to_iccs() for move in moves}


def find_move(board: Board, name: str) -> Move:
    """Return the legal move with ICCS name ``name``, or fail with a useful message."""
    for move in board.legal_moves():
        if move.to_iccs() == name:
            return move
    raise AssertionError(f"{name} is not legal in {board.to_fen()}")


def with_side_to_move(board: Board, color: Color) -> Board:
    """A copy of ``board``'s placement with ``color`` to move (history dropped).

    Used by the evaluation symmetry tests: the same position seen from each side.
    """
    fields = board.to_fen().split()
    fields[1] = "w" if color is Color.RED else "b"
    return Board.from_fen(" ".join(fields))


def mirror_board(board: Board) -> Board:
    """Flip the board vertically (rank r -> 9-r), swap colours, and swap the side to move.

    This is the project's definition of "mirrored" (see AGENTS.md): the rank flip,
    not a left-right flip, which is what makes Black's piece-square tables Red's
    tables flipped by rank.
    """
    squares = [EMPTY] * BOARD_SQUARES
    for square_index in range(BOARD_SQUARES):
        code = board.piece_at(square_index)
        if code == EMPTY:
            continue
        color = piece_color(code)
        mirrored_color = Color.BLACK if color is Color.RED else Color.RED
        mirrored_square = square(file_of(square_index), BOARD_RANKS - 1 - rank_of(square_index))
        squares[mirrored_square] = make_piece(mirrored_color, piece_kind(code))
    return Board(squares, board.side_to_move.opponent)


def play(board: Board, names: str) -> None:
    """Play a space-separated list of ICCS moves, asserting each one is legal."""
    for name in names.split():
        board.make_move(find_move(board, name))
