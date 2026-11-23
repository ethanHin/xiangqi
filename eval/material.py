"""Material values: the one place piece worth is defined.

Used by the greedy bot (M2) and, from M4, by the full weighted evaluator. Values
are in centipawns, so +100 means "one soldier".

The general is worth 0: it cannot be captured in legal play, and a position where
it is missing has already ended. The soldier is worth more once it has crossed
the river, which is why ``piece_value`` takes the square it stands on.
"""

from __future__ import annotations

from engine.types import (
    BOARD_SQUARES,
    Color,
    PieceKind,
    make_piece,
    piece_color,
    piece_kind,
    rank_of,
)

# Named values so the greedy bot, the experiments, and the documentation all
# quote the same numbers. Tunable in M4 by passing a replacement dict.
PIECE_VALUES: dict[PieceKind, int] = {
    PieceKind.GENERAL: 0,
    PieceKind.ADVISOR: 200,
    PieceKind.ELEPHANT: 200,
    PieceKind.HORSE: 400,
    PieceKind.CHARIOT: 900,
    PieceKind.CANNON: 450,
    PieceKind.SOLDIER: 100,
}

# A soldier that has crossed the river may also step sideways, which makes it
# materially more dangerous than one still on its own side.
CROSSED_SOLDIER_VALUE = 200

# Ranks at which each side's soldiers count as "across the river".
RED_RIVER_RANK = 5
BLACK_RIVER_RANK = 4

# Piece codes are 0 (empty), 1..7 (Red) and 9..15 (Black); see engine.types.
_PIECE_CODE_COUNT = 16


def _build_signed_value_table() -> tuple[int, ...]:
    """Flat table of the value of every (piece code, square), signed from Red's view.

    ``SIGNED_VALUE[code * 90 + square]`` is positive for Red pieces and negative
    for Black, with the crossed-soldier bonus already applied, so the leaf
    evaluation is a single add per occupied square and one sign flip.
    """
    table = [0] * (_PIECE_CODE_COUNT * 90)
    for kind, value in PIECE_VALUES.items():
        for color, sign in ((Color.RED, 1), (Color.BLACK, -1)):
            code = make_piece(color, kind)
            for square_index in range(90):
                rank = rank_of(square_index)
                crossed = rank >= RED_RIVER_RANK if color is Color.RED else rank <= BLACK_RIVER_RANK
                piece_worth = (
                    CROSSED_SOLDIER_VALUE if kind is PieceKind.SOLDIER and crossed else value
                )
                table[code * 90 + square_index] = sign * piece_worth
    return tuple(table)


SIGNED_VALUE = _build_signed_value_table()


def piece_value(code: int, square_index: int = -1) -> int:
    """Value of a piece code, with the crossed-river soldier bonus.

    ``square_index`` is only used by soldiers; pass -1 when the square is not
    known (a captured soldier is worth its base value).
    """
    kind = piece_kind(code)
    if kind is None:
        return 0
    if kind is PieceKind.SOLDIER and square_index >= 0:
        rank = rank_of(square_index)
        crossed = (
            rank >= RED_RIVER_RANK if piece_color(code) is Color.RED else rank <= BLACK_RIVER_RANK
        )
        if crossed:
            return CROSSED_SOLDIER_VALUE
    return PIECE_VALUES[kind]


def material_balance(board, values: dict[PieceKind, int] | None = None) -> int:
    """Material score from the side to move's point of view (centipawns).

    Positive means the side to move is ahead. This is the sign convention the
    evaluator uses in M4 (negamax-compatible).

    The default path uses a precomputed table of signed values per (piece code,
    square) -- the crossed-soldier bonus is baked in -- so a leaf costs one add per
    occupied square. That matters: this function runs at every leaf of the search.
    Passing ``values`` falls back to the generic loop, which is fine for
    experiments but slower.
    """
    squares = board.squares
    if values is None:
        total = 0
        for square_index in range(BOARD_SQUARES):
            code = squares[square_index]
            if code:
                total += SIGNED_VALUE[code * BOARD_SQUARES + square_index]
    else:
        total = _generic_balance(squares, values)
    return total if board.side_to_move is Color.RED else -total


def _generic_balance(squares: list[int], values: dict[PieceKind, int]) -> int:
    """Material balance from Red's view with a caller-supplied value table."""
    total = 0
    for square_index, code in enumerate(squares):
        if code == 0:
            continue
        kind = piece_kind(code)
        if kind is None:
            continue
        value = values[kind]
        if kind is PieceKind.SOLDIER and piece_value(code, square_index) > value:
            value = CROSSED_SOLDIER_VALUE
        total += value if piece_color(code) is Color.RED else -value
    return total


def total_material(board, color: Color, values: dict[PieceKind, int] | None = None) -> int:
    """Sum of ``color``'s material, including the crossed-soldier bonus."""
    table = values or PIECE_VALUES
    total = 0
    for square_index, code in enumerate(board.squares):
        if code == 0 or piece_color(code) is not color:
            continue
        kind = piece_kind(code)
        if kind is None:
            continue
        bonus = piece_value(code, square_index) - table[kind] if kind is PieceKind.SOLDIER else 0
        total += table[kind] + bonus
    return total


__all__ = [
    "PIECE_VALUES",
    "CROSSED_SOLDIER_VALUE",
    "piece_value",
    "material_balance",
    "total_material",
]
