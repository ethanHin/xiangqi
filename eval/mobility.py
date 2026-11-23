"""Mobility: how many moves each side has.

The feature is ``own pseudo-legal moves - opponent pseudo-legal moves``, from the
side to move's point of view. A side with more freedom usually has more plans, so
this is a cheap positional tie-breaker on top of material.

Pseudo-legal counts (check ignored) are used because the evaluation runs at every
leaf: filtering for legality would roughly triple the cost for a feature that is
worth only a few centipawns per move. The mobility number is therefore a measure of
"room to manoeuvre", not of "number of legal moves".
"""

from __future__ import annotations

from engine import movegen
from engine.board import Board


def mobility_balance(board: Board) -> int:
    """Pseudo-legal move difference from the side to move's point of view."""
    side = board.side_to_move
    own = movegen.count_pseudo_legal_moves(board, side)
    enemy = movegen.count_pseudo_legal_moves(board, side.opponent)
    return own - enemy


def mobility(board: Board, color: object) -> int:
    """Pseudo-legal move count for one side (used by tests and diagnostics)."""
    return movegen.count_pseudo_legal_moves(board, color)  # type: ignore[arg-type]
