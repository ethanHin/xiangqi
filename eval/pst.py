"""Piece-square tables: a small positional bonus for where each piece stands.

A table is 10 rows of 9 columns for Red, written **bottom-up**: row 0 is Red's back
rank and file 0 is Red's left. ``square = rank * 9 + file`` matches the engine, so a
table entry can be indexed by a square directly.

Black's table is Red's table flipped by rank (``r -> 9 - r``); that is the project's
definition of "mirrored" and it is also what makes the symmetry acceptance test
hold for tables that are not left-right symmetric. Signs are from the side's own
view, so a table entry of ``+30`` is worth 30 centipawns to whoever owns the piece,
and ``pst_balance`` flips the sign once for Black to move.

What the tables encode (they are flat constants, so the comments matter):

* soldier: more valuable the further it has advanced, and slightly more valuable on
  central files;
* horse: central squares are worth more than rim squares, because the horse has
  more destinations there;
* chariot: central files and the second/far ranks are worth a little more. A true
  "open file" bonus needs to look at the position and is not possible in a PST;
* cannon: small central bonus, mild preference for staying on its own side;
* advisor / elephant: small bonuses on the palace and home squares they defend;
* general: prefers the back rank; sitting on the palace's forward rank is a small
  penalty.

The numbers are deliberately small (tens of centipawns): material must still
dominate. They are tunable through the weights in ``eval.evaluator``.
"""

from __future__ import annotations

from engine.types import (
    BOARD_FILES,
    BOARD_RANKS,
    BOARD_SQUARES,
    EMPTY,
    Color,
    PieceKind,
    square,
)

# ---------------------------------------------------------------------------
# Red's tables, one per piece kind: 10 ranks (0 = back rank) x 9 files (0 = a).
# ---------------------------------------------------------------------------

RED_TABLES: dict[PieceKind, tuple[tuple[int, ...], ...]] = {
    # Soldiers are worth more as they advance; the centre files are worth slightly
    # more because a soldier there controls more of the enemy palace.
    PieceKind.SOLDIER: (
        (0, 0, 0, 0, 0, 0, 0, 0, 0),  # rank 0
        (0, 0, 0, 0, 0, 0, 0, 0, 0),  # rank 1
        (0, 0, 0, 0, 0, 0, 0, 0, 0),  # rank 2
        (0, 0, 0, 0, 0, 0, 0, 0, 0),  # rank 3: starting rank, no bonus
        (5, 5, 8, 12, 15, 12, 8, 5, 5),  # rank 4: one step from the river
        (10, 12, 16, 22, 26, 22, 16, 12, 10),  # rank 5: crossed the river
        (14, 18, 24, 32, 38, 32, 24, 18, 14),
        (18, 24, 32, 44, 52, 44, 32, 24, 18),
        (22, 30, 42, 56, 66, 56, 42, 30, 22),
        (26, 36, 50, 66, 78, 66, 50, 36, 26),  # rank 9: deep in enemy territory
    ),
    # Horses prefer the centre: more destinations, fewer blocked legs.
    PieceKind.HORSE: (
        (-6, -3, 0, 3, 4, 3, 0, -3, -6),
        (-3, 0, 3, 6, 7, 6, 3, 0, -3),
        (0, 3, 6, 9, 10, 9, 6, 3, 0),
        (1, 4, 7, 10, 11, 10, 7, 4, 1),
        (2, 5, 8, 11, 12, 11, 8, 5, 2),
        (2, 5, 8, 11, 12, 11, 8, 5, 2),
        (1, 4, 7, 10, 11, 10, 7, 4, 1),
        (0, 3, 6, 9, 10, 9, 6, 3, 0),
        (-3, 0, 3, 6, 7, 6, 3, 0, -3),
        (-6, -3, 0, 3, 4, 3, 0, -3, -6),
    ),
    # Chariots: central files and the advanced ranks are worth a little more.
    PieceKind.CHARIOT: (
        (0, 2, 4, 6, 8, 6, 4, 2, 0),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (2, 4, 6, 8, 10, 8, 6, 4, 2),
        (0, 2, 4, 6, 8, 6, 4, 2, 0),
    ),
    # Cannons: small central bonus, and a slight preference for the back half where
    # they have more screens to work with.
    PieceKind.CANNON: (
        (0, 0, 2, 4, 4, 4, 2, 0, 0),
        (0, 2, 4, 6, 6, 6, 4, 2, 0),
        (0, 2, 4, 6, 8, 6, 4, 2, 0),
        (0, 2, 4, 6, 8, 6, 4, 2, 0),
        (0, 2, 4, 6, 8, 6, 4, 2, 0),
        (0, 2, 4, 6, 8, 6, 4, 2, 0),
        (0, 2, 4, 6, 6, 6, 4, 2, 0),
        (0, 0, 2, 4, 4, 4, 2, 0, 0),
        (0, 0, 0, 2, 2, 2, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
    # Advisors: the palace points, with a small preference for the centre.
    PieceKind.ADVISOR: (
        (0, 0, 0, 5, 0, 5, 0, 0, 0),
        (0, 0, 0, 0, 10, 0, 0, 0, 0),
        (0, 0, 0, 3, 0, 3, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
    # Elephants: home squares and the centre of one's own half.
    PieceKind.ELEPHANT: (
        (0, 0, 5, 0, 0, 0, 5, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (3, 0, 0, 0, 10, 0, 0, 0, 3),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 5, 0, 0, 0, 5, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
    # General: the back rank is home; stepping up the palace is a small risk.
    PieceKind.GENERAL: (
        (0, 0, 0, 5, 8, 5, 0, 0, 0),
        (0, 0, 0, 0, 4, 0, 0, 0, 0),
        (0, 0, 0, -8, -12, -8, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 0, 0, 0, 0),
    ),
}


def _flatten(table: tuple[tuple[int, ...], ...]) -> tuple[int, ...]:
    """Turn 10 rows of 9 into one 90-entry tuple indexed by square."""
    return tuple(value for row in table for value in row)


def _mirror_by_rank(flat: tuple[int, ...]) -> tuple[int, ...]:
    """Flip a flat table vertically: the value for rank r goes to rank 9 - r."""
    mirrored = [0] * BOARD_SQUARES
    for rank in range(BOARD_RANKS):
        for file in range(BOARD_FILES):
            mirrored[square(file, BOARD_RANKS - 1 - rank)] = flat[square(file, rank)]
    return tuple(mirrored)


def _build_signed_tables() -> tuple[tuple[int, ...], ...]:
    """Per-code signed tables: +value for Red, -value for Black, indexed by square.

    Indexed as ``SIGNED_BY_CODE[piece_code][square]``. Building it once means the
    evaluation is a table read per occupied square instead of a table read plus a
    sign decision.
    """
    tables: list[tuple[int, ...]] = [()] * 16
    for kind, rows in RED_TABLES.items():
        red = _flatten(rows)
        black = _mirror_by_rank(red)
        tables[int(kind)] = red  # Red piece codes are 1..7
        tables[int(kind) + 8] = tuple(-value for value in black)  # Black is 9..15
    return tuple(tables)


SIGNED_BY_CODE = _build_signed_tables()


def pst_value(code: int, square_index: int) -> int:
    """Positional value of a piece, signed from its owner's point of view."""
    if code == EMPTY:
        return 0
    return SIGNED_BY_CODE[code][square_index]


def pst_balance(board) -> int:
    """Sum of piece-square values from the side to move's point of view (centipawns)."""
    squares = board.squares
    total = 0
    for square_index in range(BOARD_SQUARES):
        code = squares[square_index]
        if code:
            total += SIGNED_BY_CODE[code][square_index]
    return total if board.side_to_move is Color.RED else -total


def red_table(kind: PieceKind) -> tuple[int, ...]:
    """Flat Red table for one piece kind (exposed for tests and tools)."""
    return _flatten(RED_TABLES[kind])


def black_table(kind: PieceKind) -> tuple[int, ...]:
    """Flat Black table: Red's table flipped by rank."""
    return _mirror_by_rank(red_table(kind))


__all__ = [
    "RED_TABLES",
    "SIGNED_BY_CODE",
    "pst_value",
    "pst_balance",
    "red_table",
    "black_table",
]
