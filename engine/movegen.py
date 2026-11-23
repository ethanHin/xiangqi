"""Pseudo-legal and legal move generation, plus perft.

Used by the board (``board.legal_moves``), by every bot, and by the perft tests
that check the move generator against published Xiangqi node counts.

Two-stage design, as in most engines:

1. ``generate_pseudo_legal_moves`` ignores check: every move that follows the
   movement rules of its piece.
2. ``generate_legal_moves`` filters those by playing each move and asking whether
   the mover's own general is attacked afterwards. That single test covers
   self-check, pins, and the flying-general rule.

Movement tables for the short-range pieces are precomputed once at import, on
the theory that a 90-entry list lookup is cheaper than arithmetic in the hot
loop. Sliding pieces walk the board with explicit loops instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.types import (
    BOARD_FILES,
    BOARD_RANKS,
    BOARD_SQUARES,
    EMPTY,
    Color,
    Move,
    PieceKind,
    file_of,
    piece_color,
    piece_kind,
    rank_of,
    square,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers only
    from engine.board import Board

# --- Palace and river boundaries -------------------------------------------
# Each palace is files d-f (3..5); Red's spans ranks 0-2, Black's ranks 7-9.
PALACE_FILES = (3, 4, 5)
RED_PALACE_RANKS = (0, 1, 2)
BLACK_PALACE_RANKS = (7, 8, 9)

# The river splits the board in half: Red owns ranks 0-4, Black owns ranks 5-9.
RED_SIDE_MAX_RANK = 4
BLACK_SIDE_MIN_RANK = 5

# A Red soldier may step sideways once it stands on rank 5 or higher; a Black
# soldier once it stands on rank 4 or lower.
RED_SOLDIER_CROSSED_RANK = 5
BLACK_SOLDIER_CROSSED_RANK = 4

ORTHOGONAL_STEPS = ((0, 1), (1, 0), (0, -1), (-1, 0))

# Offsets of the eight horse destinations, as (file step, rank step).
HORSE_OFFSETS = ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2))


# ---------------------------------------------------------------------------
# Precomputed movement tables
# ---------------------------------------------------------------------------


def _inside(file: int, rank: int) -> bool:
    """True if file/rank is on the board."""
    return 0 <= file < BOARD_FILES and 0 <= rank < BOARD_RANKS


def _in_palace(color: Color, file: int, rank: int) -> bool:
    """True if file/rank is inside ``color``'s 3x3 palace."""
    ranks = RED_PALACE_RANKS if color is Color.RED else BLACK_PALACE_RANKS
    return file in PALACE_FILES and rank in ranks


def _own_half(color: Color, rank: int) -> bool:
    """True if ``rank`` is on ``color``'s side of the river."""
    return rank <= RED_SIDE_MAX_RANK if color is Color.RED else rank >= BLACK_SIDE_MIN_RANK


def _build_general_targets() -> dict[Color, list[list[int]]]:
    """Precompute each general's one-step orthogonal destinations inside its palace."""
    tables: dict[Color, list[list[int]]] = {}
    for color in Color:
        table: list[list[int]] = []
        for origin in range(BOARD_SQUARES):
            file, rank = file_of(origin), rank_of(origin)
            targets = [
                square(file + df, rank + dr)
                for df, dr in ORTHOGONAL_STEPS
                if _in_palace(color, file + df, rank + dr)
            ]
            table.append(targets)
        tables[color] = table
    return tables


def _build_advisor_targets() -> dict[Color, list[list[int]]]:
    """Precompute each advisor's one-step diagonal destinations inside its palace."""
    tables: dict[Color, list[list[int]]] = {}
    for color in Color:
        table = []
        for origin in range(BOARD_SQUARES):
            file, rank = file_of(origin), rank_of(origin)
            targets = [
                square(file + df, rank + dr)
                for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1))
                if _in_palace(color, file + df, rank + dr)
            ]
            table.append(targets)
        tables[color] = table
    return tables


def _build_elephant_targets() -> dict[Color, list[tuple[int, int]]]:
    """Precompute (destination, eye) pairs: two diagonal steps, never across the river."""
    tables: dict[Color, list[list[tuple[int, int]]]] = {}
    for color in Color:
        table = []
        for origin in range(BOARD_SQUARES):
            file, rank = file_of(origin), rank_of(origin)
            entries = []
            for df, dr in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
                target_file, target_rank = file + df, rank + dr
                if _inside(target_file, target_rank) and _own_half(color, target_rank):
                    entries.append(
                        (square(target_file, target_rank), square(file + df // 2, rank + dr // 2))
                    )
            table.append(entries)
        tables[color] = table
    return tables


def _build_horse_targets() -> list[list[tuple[int, int]]]:
    """Precompute (destination, leg) pairs for every square."""
    table = []
    for origin in range(BOARD_SQUARES):
        file, rank = file_of(origin), rank_of(origin)
        entries = []
        for df, dr in HORSE_OFFSETS:
            target_file, target_rank = file + df, rank + dr
            if not _inside(target_file, target_rank):
                continue
            # Horse leg rule: a two-square step means the leg is one rank toward the
            # target; a one-square step means the leg is one file toward it.
            if abs(dr) == 2:
                leg = square(file, rank + (1 if dr > 0 else -1))
            else:
                leg = square(file + (1 if df > 0 else -1), rank)
            entries.append((square(target_file, target_rank), leg))
        table.append(entries)
    return table


def _build_soldier_targets() -> dict[Color, list[list[int]]]:
    """Precompute soldier destinations: always forward, sideways after the river."""
    tables: dict[Color, list[list[int]]] = {}
    for color in Color:
        table = []
        for origin in range(BOARD_SQUARES):
            file, rank = file_of(origin), rank_of(origin)
            targets = []
            forward_rank = rank + color.forward
            if _inside(file, forward_rank):
                targets.append(square(file, forward_rank))
            crossed = (
                rank >= RED_SOLDIER_CROSSED_RANK
                if color is Color.RED
                else rank <= BLACK_SOLDIER_CROSSED_RANK
            )
            if crossed:
                for df in (-1, 1):
                    if _inside(file + df, rank):
                        targets.append(square(file + df, rank))
            table.append(targets)
        tables[color] = table
    return tables


GENERAL_TARGETS = _build_general_targets()
ADVISOR_TARGETS = _build_advisor_targets()
ELEPHANT_TARGETS = _build_elephant_targets()
HORSE_TARGETS = _build_horse_targets()
SOLDIER_TARGETS = _build_soldier_targets()


# ---------------------------------------------------------------------------
# Move generation
# ---------------------------------------------------------------------------


def generate_pseudo_legal_moves(board: Board, color: Color | None = None) -> list[Move]:
    """Every move for ``color`` (default: side to move) that follows piece movement.

    Check is ignored here, so the returned moves may still leave the mover's own
    general attacked. ``generate_legal_moves`` is the filtering wrapper used by
    everything outside the search.
    """
    if color is None:
        color = board.side_to_move
    squares = board.squares
    moves: list[Move] = []

    for origin in range(BOARD_SQUARES):
        code = squares[origin]
        if code == EMPTY or piece_color(code) is not color:
            continue
        kind = piece_kind(code)
        if kind is PieceKind.CHARIOT:
            _append_chariot_moves(moves, squares, origin, color)
        elif kind is PieceKind.CANNON:
            _append_cannon_moves(moves, squares, origin, color)
        elif kind is PieceKind.HORSE:
            for target, leg in HORSE_TARGETS[origin]:
                if squares[leg] != EMPTY:
                    continue
                _append_if_not_own(moves, squares, origin, target, color)
        elif kind is PieceKind.ELEPHANT:
            for target, eye in ELEPHANT_TARGETS[color][origin]:
                if squares[eye] != EMPTY:
                    continue
                _append_if_not_own(moves, squares, origin, target, color)
        elif kind is PieceKind.SOLDIER:
            _append_step_moves(moves, squares, origin, SOLDIER_TARGETS[color][origin], color)
        elif kind is PieceKind.ADVISOR:
            _append_step_moves(moves, squares, origin, ADVISOR_TARGETS[color][origin], color)
        else:  # GENERAL
            _append_step_moves(moves, squares, origin, GENERAL_TARGETS[color][origin], color)

    return moves


def generate_legal_moves(board: Board) -> list[Move]:
    """Legal moves for the side to move: pseudo-legal moves that keep the general safe.

    Naively that means "play it, check my general, undo it" for every move, which is
    the dominant cost of the search. Two observations make it much cheaper:

    * if the mover is in check, every move really does have to be verified;
    * otherwise only a move by the general or by a *pinned* piece can expose the
      general, so everything else is legal by construction.

    ``pinned_squares`` finds the pinned pieces with four ray scans from the general,
    replacing roughly forty attack scans per node with four.
    """
    color = board.side_to_move
    pseudo = generate_pseudo_legal_moves(board, color)

    if board.is_in_check(color):
        # In check: a move may only stand if it resolves the check.
        return _filter_illegal(board, pseudo, color)

    pinned, cannon_danger = guard_squares(board, color)
    legal: list[Move] = []
    for move in pseudo:
        if (
            move.from_square in pinned
            or move.to_square in cannon_danger
            or piece_kind(board.squares[move.from_square]) is PieceKind.GENERAL
        ):
            if _is_legal(board, move, color):
                legal.append(move)
        else:
            legal.append(move)
    return legal


def guard_squares(board: Board, color: Color) -> tuple[set[int], set[int]]:
    """Find the ways a move by a piece other than the general could still expose it.

    Returns ``(pinned, cannon_danger)``. Both come from walking the four orthogonal
    rays out from the general and looking at the first three pieces on each ray,
    which is why a node costs four scans instead of one scan per candidate move.

    * ``pinned``: pieces that must not leave their line, either because an enemy
      chariot (or the enemy general on a file -- the flying-general rule) is directly
      behind them, or because an enemy cannon has exactly two pieces between it and
      the general: moving either one would leave exactly one screen and give check.
    * ``cannon_danger``: empty squares between the general and an enemy cannon that
      has a clear line. A cannon needs exactly one screen, so moving a piece *onto*
      one of those squares would create the screen and expose the general.
    """
    general = board.general_square(color)
    if general < 0:
        return set(), set()
    squares = board.squares
    file, rank = file_of(general), rank_of(general)
    pinned: set[int] = set()
    cannon_danger: set[int] = set()

    for df, dr in ORTHOGONAL_STEPS:
        pieces: list[tuple[int, int]] = []  # (square, code), nearest to the general first
        empty_squares: list[int] = []  # empty squares before the first piece
        target_file, target_rank = file + df, rank + dr
        while _inside(target_file, target_rank) and len(pieces) < 3:
            target = square(target_file, target_rank)
            code = squares[target]
            if code == EMPTY:
                if not pieces:
                    empty_squares.append(target)
            else:
                pieces.append((target, code))
            target_file += df
            target_rank += dr
        if not pieces:
            continue

        first_square, first_code = pieces[0]
        if piece_color(first_code) is not color:
            # An enemy piece closest to the general. A cannon here has no screen yet,
            # so the gap between it and the general is a trap: a piece landing there
            # becomes the screen. (A chariot or general here would be check, which
            # the caller handles before calling this function.)
            if piece_kind(first_code) is PieceKind.CANNON:
                cannon_danger.update(empty_squares)
            continue

        # The closest piece is ours. What sits behind it decides whether it may move.
        if len(pieces) >= 2:
            second_code = pieces[1][1]
            if piece_color(second_code) is not color:
                second_kind = piece_kind(second_code)
                if second_kind is PieceKind.CHARIOT or (
                    second_kind is PieceKind.GENERAL and df == 0
                ):
                    pinned.add(first_square)

        # Enemy cannon with exactly two pieces in between: both are screens, and
        # removing either one leaves a single screen -- check. Pin the friendly ones.
        if len(pieces) >= 3:
            third_code = pieces[2][1]
            if piece_color(third_code) is not color and piece_kind(third_code) is PieceKind.CANNON:
                for square_index, code in pieces[:2]:
                    if piece_color(code) is color:
                        pinned.add(square_index)
    return pinned, cannon_danger


def _filter_illegal(board: Board, moves: list[Move], color: Color) -> list[Move]:
    """Keep only the moves that leave ``color``'s general unattacked."""
    legal: list[Move] = []
    for move in moves:
        if _is_legal(board, move, color):
            legal.append(move)
    return legal


def _is_legal(board: Board, move: Move, color: Color) -> bool:
    """Play ``move``, ask whether ``color``'s general is attacked, and undo."""
    board.make_move(move, record_check=False)
    try:
        return not board.is_in_check(color)
    finally:
        board.unmake_move()


def perft(board: Board, depth: int) -> int:
    """Count leaf nodes of the legal move tree to ``depth`` (reference depths in tests).

    The board must be free of recorded history; perft only needs the position.
    """
    if depth <= 0:
        return 1
    if depth == 1:
        return len(generate_legal_moves(board))
    total = 0
    for move in generate_legal_moves(board):
        board.make_move(move, record_check=False)
        total += perft(board, depth - 1)
        board.unmake_move()
    return total


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _append_if_not_own(
    moves: list[Move], squares: list[int], origin: int, target: int, color: Color
) -> None:
    """Append ``origin -> target`` unless the target holds a friendly piece."""
    code = squares[target]
    if code != EMPTY and piece_color(code) is color:
        return
    moves.append(Move(origin, target, code))


def _append_step_moves(
    moves: list[Move], squares: list[int], origin: int, targets: list[int], color: Color
) -> None:
    """Append every single-step destination in ``targets`` that is not occupied by ``color``."""
    for target in targets:
        _append_if_not_own(moves, squares, origin, target, color)


def _append_chariot_moves(moves: list[Move], squares: list[int], origin: int, color: Color) -> None:
    """Chariot: slide any distance orthogonally until a piece blocks the ray."""
    file, rank = file_of(origin), rank_of(origin)
    for df, dr in ORTHOGONAL_STEPS:
        target_file, target_rank = file + df, rank + dr
        while _inside(target_file, target_rank):
            target = square(target_file, target_rank)
            code = squares[target]
            if code != EMPTY:
                if piece_color(code) is not color:
                    moves.append(Move(origin, target, code))
                break
            moves.append(Move(origin, target, EMPTY))
            target_file += df
            target_rank += dr


def _append_cannon_moves(moves: list[Move], squares: list[int], origin: int, color: Color) -> None:
    """Cannon: slides like a chariot, but captures only by jumping exactly one screen."""
    file, rank = file_of(origin), rank_of(origin)
    for df, dr in ORTHOGONAL_STEPS:
        target_file, target_rank = file + df, rank + dr

        # Phase 1: quiet moves, straight up to (not onto) the first piece.
        while _inside(target_file, target_rank):
            target = square(target_file, target_rank)
            if squares[target] != EMPTY:
                break
            moves.append(Move(origin, target, EMPTY))
            target_file += df
            target_rank += dr
        if not _inside(target_file, target_rank):
            continue

        # Phase 2: that first piece is the screen. The next piece on the ray is
        # capturable if it is an enemy; anything further is out of reach.
        target_file += df
        target_rank += dr
        while _inside(target_file, target_rank):
            target = square(target_file, target_rank)
            code = squares[target]
            if code != EMPTY:
                if piece_color(code) is not color:
                    moves.append(Move(origin, target, code))
                break
            target_file += df
            target_rank += dr
