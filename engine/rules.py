"""Terminal detection: checkmate, stalemate, repetition, perpetual check, move cap.

Called after every move by the harness, the server, and the tests. It never
changes the board.

The repetition rule is isolated in ``repetition_result`` so it can be swapped
without touching the game loop: pass it a ``PositionHistory`` and it returns a
``Result`` or None. The reading implemented here (documented in the README) is:

* a position that has occurred three times ends the game;
* if one side checked on every one of its moves since the first occurrence of
  that position and the opponent checked on none of theirs, the checking side
  loses (perpetual check);
* otherwise the game is a draw.
"""

from __future__ import annotations

from engine.types import Color, Move, PositionHistory, Reason, Result

# Move cap: 300 plies total (150 per side) is a draw. Counted from the position
# the board was created at, which for a played game is the post-opening position.
MOVE_CAP_PLIES = 300

# A position has to occur this many times (including the current occurrence) for
# the threefold-repetition rule to fire.
REPETITION_COUNT = 3


def game_result(board, legal_moves: list[Move] | None = None) -> Result | None:
    """Return the game's result, or None if it is still in progress.

    Order matters: a mate or stalemate on the same ply the cap is reached is a
    loss, not a draw (see AGENTS.md, "Terminal precedence").

    ``legal_moves`` may be passed by callers that already generated them for the
    position, to avoid generating twice.
    """
    if legal_moves is None:
        legal_moves = board.legal_moves()
    if not legal_moves:
        # No legal moves loses in Xiangqi whether the general is attacked or not.
        loser = board.side_to_move
        reason = Reason.CHECKMATE if board.is_in_check(loser) else Reason.STALEMATE
        return Result(winner=loser.opponent, reason=reason)

    repetition = repetition_result(board.history())
    if repetition is not None:
        return repetition

    if board.ply_count >= MOVE_CAP_PLIES:
        return Result(winner=None, reason=Reason.MOVE_CAP)
    return None


def repetition_result(history: PositionHistory) -> Result | None:
    """Apply the threefold/perpetual-check rule to ``history``.

    Returns None while fewer than three occurrences of the current position have
    been seen. The "repeated cycle" is every move played after the first
    occurrence of the current position, which is the simplest reading of the
    rule and keeps the check flags we already record for each ply.
    """
    hashes = history.hashes()
    current = hashes[-1]
    if hashes.count(current) < REPETITION_COUNT:
        return None

    first_occurrence = hashes.index(current)
    cycle = history.plies[first_occurrence:]

    red_checks = [record.gave_check for record in cycle if record.mover is Color.RED]
    black_checks = [record.gave_check for record in cycle if record.mover is Color.BLACK]

    red_checks_every_move = bool(red_checks) and all(red_checks)
    black_checks_every_move = bool(black_checks) and all(black_checks)

    if red_checks_every_move and not any(black_checks):
        return _perpetual_check_result(Color.BLACK)
    if black_checks_every_move and not any(red_checks):
        return _perpetual_check_result(Color.RED)
    return Result(winner=None, reason=Reason.REPETITION)


def _perpetual_check_result(winner: Color) -> Result:
    """The side that never stopped checking loses; ``winner`` is the side being checked."""
    return Result(winner=winner, reason=Reason.PERPETUAL_CHECK)
