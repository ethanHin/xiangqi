"""King safety: intact advisors and elephants near the general, exposure penalties.

Xiangqi's general is slow and boxed into a 3x3 palace, so the pieces around it are
its armour: each advisor and elephant is worth a small bonus on top of its material
value, more when it is actually near the general. A general with no guards left is
exposed to chariots and cannons, so it takes a penalty, and standing on the palace's
forward rank (one step closer to the enemy) is a further small penalty.

All constants are named so an experiment can quote them. The feature is signed from
the side's own point of view; ``king_safety_balance`` returns own minus opponent.
"""

from __future__ import annotations

from engine.board import Board
from engine.types import (
    BOARD_SQUARES,
    Color,
    PieceKind,
    file_of,
    piece_color,
    piece_kind,
    rank_of,
)

# Per surviving guard. Small compared with its material value (200): this is the
# "it is still around to defend" bonus, not a second material term.
ADVISOR_BONUS = 20
ELEPHANT_BONUS = 20

# Extra for a guard within this Chebyshev distance of the general.
NEAR_GENERAL_BONUS = 8
NEAR_GENERAL_DISTANCE = 2

# Penalties for a thin defence. They stack with the "fully exposed" penalty.
NO_ADVISOR_PENALTY = 30
NO_ELEPHANT_PENALTY = 20
FULLY_EXPOSED_PENALTY = 30

# The general on the palace's forward rank (rank 2 for Red, 7 for Black) is one
# step closer to danger.
FORWARD_RANK_PENALTY = 15

RED_FORWARD_RANK = 2
BLACK_FORWARD_RANK = 7


def king_safety(board: Board, color: Color) -> int:
    """Safety score for ``color``, in centipawns (higher is safer)."""
    general = board.general_square(color)
    if general < 0:
        return 0

    advisors = 0
    elephants = 0
    score = 0
    for square_index in range(BOARD_SQUARES):
        code = board.squares[square_index]
        if code == 0 or piece_color(code) is not color:
            continue
        kind = piece_kind(code)
        if kind is PieceKind.ADVISOR:
            advisors += 1
            score += ADVISOR_BONUS
        elif kind is PieceKind.ELEPHANT:
            elephants += 1
            score += ELEPHANT_BONUS
        else:
            continue
        if _chebyshev(square_index, general) <= NEAR_GENERAL_DISTANCE:
            score += NEAR_GENERAL_BONUS

    if advisors == 0:
        score -= NO_ADVISOR_PENALTY
    if elephants == 0:
        score -= NO_ELEPHANT_PENALTY
    if advisors == 0 and elephants == 0:
        score -= FULLY_EXPOSED_PENALTY

    forward_rank = RED_FORWARD_RANK if color is Color.RED else BLACK_FORWARD_RANK
    if rank_of(general) == forward_rank:
        score -= FORWARD_RANK_PENALTY
    return score


def king_safety_balance(board: Board) -> int:
    """Own king safety minus the opponent's, from the side to move's point of view."""
    side = board.side_to_move
    return king_safety(board, side) - king_safety(board, side.opponent)


def _chebyshev(first: int, second: int) -> int:
    """Distance in king moves between two squares (max of file and rank difference)."""
    return max(abs(file_of(first) - file_of(second)), abs(rank_of(first) - rank_of(second)))
