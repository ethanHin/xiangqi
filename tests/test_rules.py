"""Tests for terminal detection: mate, stalemate, repetition, perpetual check, cap.

The repetition tests play real move sequences where possible, so they also
exercise the check flags recorded by ``make_move``. Two tests build synthetic
histories to cover the check-flag combinations that are awkward to reach in a
real game.
"""

from __future__ import annotations

from helpers import board_from, find_move, play

from engine import rules
from engine.board import Board
from engine.rules import MOVE_CAP_PLIES
from engine.types import EMPTY, Color, Move, PlyRecord, PositionHistory, Reason, Result


def test_start_position_is_not_terminal() -> None:
    assert rules.game_result(Board.start()) is None


def test_checkmate_is_a_loss_for_the_side_to_move() -> None:
    # Black general on e9 is checked by both chariots: the a9 chariot along the
    # back rank and the e0 chariot up the e-file. Its three destinations are all
    # covered, so it is mate.
    board = board_from({"e9": "k", "a9": "R", "e0": "R", "d0": "K"}, side_to_move="b")
    assert board.is_in_check() is True
    assert board.legal_moves() == []
    assert rules.game_result(board) == Result(winner=Color.RED, reason=Reason.CHECKMATE)


def test_checkmate_mirrored_is_a_loss_for_red() -> None:
    # The same mate from the other side: Red's general on e0 is boxed in by a
    # chariot on the back rank and one on the e-file. "Side to move loses" must
    # not be colour-specific.
    board = board_from({"e0": "K", "a0": "r", "e9": "r", "d9": "k"}, side_to_move="w")
    assert board.is_in_check() is True
    assert board.legal_moves() == []
    assert rules.game_result(board) == Result(winner=Color.BLACK, reason=Reason.CHECKMATE)


def test_stalemate_is_also_a_loss() -> None:
    # Black is not in check but has no legal move: d9 and f9 are covered by the
    # chariots on the d- and f-files, e8 by the soldier on e7.
    board = board_from({"e9": "k", "d0": "R", "f0": "R", "e7": "P", "f1": "K"}, side_to_move="b")
    assert board.is_in_check() is False
    assert board.legal_moves() == []
    assert rules.game_result(board) == Result(winner=Color.RED, reason=Reason.STALEMATE)


def test_threefold_repetition_is_a_draw() -> None:
    # Two chariots shuffle between two squares each; the position after ply 4 is
    # the start position again, and after ply 8 it has occurred three times.
    start = {"a0": "R", "a9": "r", "e0": "K", "d9": "k"}
    board = board_from(start)
    play(board, "a0a1 a9a8 a1a0 a8a9")
    assert board.to_fen() == board_from(start).to_fen()
    assert rules.game_result(board) is None  # only two occurrences so far
    play(board, "a0a1 a9a8 a1a0 a8a9")
    assert rules.game_result(board) == Result(winner=None, reason=Reason.REPETITION)


def test_perpetual_check_loses_for_the_checking_side() -> None:
    # Red chariot checks alternately from the e- and d-files while the Black
    # general shuffles between e9 and d9. Every Red move is a check and no Black
    # move is, so Red loses by perpetual check on the third repetition.
    board = board_from({"f0": "K", "e9": "k", "d2": "R"})
    play(board, "d2e2 e9d9 e2d2 d9e9")
    # The position after ply 4 is the initial position again: two occurrences.
    assert rules.game_result(board) is None
    play(board, "d2e2 e9d9 e2d2 d9e9")
    assert rules.game_result(board) == Result(winner=Color.BLACK, reason=Reason.PERPETUAL_CHECK)


def test_synthetic_one_sided_perpetual_check() -> None:
    history = _synthetic_history(red_checks=(True, True), black_checks=(False, False))
    assert rules.repetition_result(history) == Result(
        winner=Color.BLACK, reason=Reason.PERPETUAL_CHECK
    )


def test_synthetic_one_sided_perpetual_check_by_black() -> None:
    # Mirror image: Black is the only side checking, so Red wins.
    history = _synthetic_history(red_checks=(False, False), black_checks=(True, True))
    assert rules.repetition_result(history) == Result(
        winner=Color.RED, reason=Reason.PERPETUAL_CHECK
    )


def test_mutual_checks_are_a_draw() -> None:
    # Both sides check on every move of the cycle, so neither is "the" perpetual
    # checker and the rule falls back to a draw.
    history = _synthetic_history(red_checks=(True, True), black_checks=(True, True))
    assert rules.repetition_result(history) == Result(winner=None, reason=Reason.REPETITION)


def test_one_sided_checks_must_cover_every_move() -> None:
    # Red checks on only one of its two moves, so this is not a perpetual check.
    history = _synthetic_history(red_checks=(True, False), black_checks=(False, False))
    assert rules.repetition_result(history) == Result(winner=None, reason=Reason.REPETITION)


def test_checker_faces_a_counter_check() -> None:
    # Red checks on every move, but Black checks once: the opponent did not check
    # on *none* of its moves, so the perpetual-check exception does not apply.
    history = _synthetic_history(red_checks=(True, True), black_checks=(True, False))
    assert rules.repetition_result(history) == Result(winner=None, reason=Reason.REPETITION)


def test_no_result_without_three_occurrences() -> None:
    assert rules.repetition_result(Board.start().history()) is None


def test_move_cap_is_a_draw() -> None:
    # The fabricated records only exercise the cap branch: all hashes are distinct
    # so the repetition rule stays quiet, and the start position has legal moves.
    board = Board.start()
    board.records.extend(_fake_records(MOVE_CAP_PLIES - 1))
    assert board.ply_count == MOVE_CAP_PLIES - 1
    assert rules.game_result(board) is None
    board.records.extend(_fake_records(1, first_hash=10**6))
    assert rules.game_result(board) == Result(winner=None, reason=Reason.MOVE_CAP)


def test_checkmate_on_the_cap_ply_beats_the_cap() -> None:
    # Terminal precedence: mate is checked before the move cap.
    board = board_from({"e9": "k", "a9": "R", "e0": "R", "d0": "K"}, side_to_move="b")
    board.records.extend(_fake_records(MOVE_CAP_PLIES))
    assert rules.game_result(board) == Result(winner=Color.RED, reason=Reason.CHECKMATE)


def test_utility_convention() -> None:
    win = Result(winner=Color.RED, reason=Reason.CHECKMATE)
    draw = Result(winner=None, reason=Reason.MOVE_CAP)
    assert (win.utility(Color.RED), win.utility(Color.BLACK)) == (1, -1)
    assert draw.is_draw and draw.utility(Color.RED) == 0


# ---------------------------------------------------------------------------
# Builders for synthetic histories
# ---------------------------------------------------------------------------


def _synthetic_history(
    red_checks: tuple[bool, bool], black_checks: tuple[bool, bool]
) -> PositionHistory:
    """A history whose cycle-start position occurs three times.

    Each cycle is two plies (Red, then Black) and returns to the same hash, so the
    hashes read ``[start, a, start, b, start]``. The cycle is therefore all four
    plies, and the check flags can be chosen freely.
    """
    start = 4242
    plies: list[PlyRecord] = []
    for cycle in range(2):
        red_hash = start + 10 * (cycle + 1)
        plies.append(
            PlyRecord(
                move=Move(0, 1, EMPTY),
                mover=Color.RED,
                gave_check=red_checks[cycle],
                hash_before=start,
                hash_after=red_hash,
            )
        )
        plies.append(
            PlyRecord(
                move=Move(0, 1, EMPTY),
                mover=Color.BLACK,
                gave_check=black_checks[cycle],
                hash_before=red_hash,
                hash_after=start,
            )
        )
    return PositionHistory(initial_hash=start, plies=plies)


def _fake_records(count: int, first_hash: int = 0) -> list[PlyRecord]:
    """Ply records with distinct hashes, used only to drive the move-cap branch."""
    return [
        PlyRecord(
            move=Move(0, 1, EMPTY),
            mover=Color.RED if index % 2 == 0 else Color.BLACK,
            gave_check=False,
            hash_before=first_hash + index,
            hash_after=first_hash + index + 1,
        )
        for index in range(count)
    ]


def test_find_move_helper_rejects_illegal_moves() -> None:
    try:
        find_move(Board.start(), "a0a9")
    except AssertionError:
        return
    raise AssertionError("find_move accepted an illegal move")
