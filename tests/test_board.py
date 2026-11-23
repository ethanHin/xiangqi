"""Tests for board state: FEN, squares, make/unmake, hashing.

The make/unmake and hash tests are the M1 guarantee that search can rely on:
after ``unmake_move`` the board must be byte-for-byte the position it was,
including the Zobrist hash and the history stack.
"""

from __future__ import annotations

import random

from helpers import board_from, find_move, play

from engine import rules
from engine.board import START_FEN, Board
from engine.types import (
    BOARD_SQUARES,
    EMPTY,
    Color,
    Move,
    PieceKind,
    file_of,
    parse_square,
    piece_color,
    piece_kind,
    rank_of,
    square,
    square_name,
)


def test_start_position_round_trips_through_fen() -> None:
    board = Board.start()
    assert board.to_fen() == START_FEN
    assert board.side_to_move is Color.RED


def test_custom_fen_round_trips() -> None:
    fen = "4k4/9/9/9/9/4P4/9/9/9/4K4 b - - 0 1"
    board = Board.from_fen(fen)
    assert board.to_fen() == fen
    assert board.side_to_move is Color.BLACK
    assert piece_kind(board.piece_at(square(4, 9))) is PieceKind.GENERAL
    assert piece_color(board.piece_at(square(4, 9))) is Color.BLACK


def test_fen_rejects_broken_input() -> None:
    for bad in ("", "9/9/9", "9/9/9/9/9/9/9/9/9/9/9 w - - 0 1", "9/9/9/9/9/9/9/9/9/x8 w - - 0 1"):
        try:
            Board.from_fen(bad)
        except ValueError:
            continue
        raise AssertionError(f"from_fen accepted {bad!r}")


def test_square_helpers() -> None:
    assert square(0, 0) == 0
    assert square(8, 9) == BOARD_SQUARES - 1
    assert square_name(0) == "a0"
    assert square_name(BOARD_SQUARES - 1) == "i9"
    assert parse_square("h2") == square(7, 2)
    assert (file_of(parse_square("e5")), rank_of(parse_square("e5"))) == (4, 5)


def test_move_iccs_round_trip() -> None:
    move = Move.from_iccs("h2e2")
    assert move.to_iccs() == "h2e2"
    assert move.captured == EMPTY
    try:
        Move.from_iccs("h2e")
    except ValueError:
        pass
    else:
        raise AssertionError("short ICCS string was accepted")


def test_start_position_has_44_legal_moves() -> None:
    board = Board.start()
    moves = board.legal_moves()
    assert len(moves) == 44
    # The two cannons have four central-file moves between them; a quick spot check
    # that generation reaches the right squares.
    assert "h2e2" in {move.to_iccs() for move in moves}


def test_piece_helpers() -> None:
    red_horse = board_from({"e0": "N"}).piece_at(square(4, 0))
    assert piece_color(red_horse) is Color.RED
    assert piece_kind(red_horse) is PieceKind.HORSE
    black_horse = board_from({"e0": "n"}).piece_at(square(4, 0))
    assert piece_color(black_horse) is Color.BLACK
    assert piece_kind(black_horse) is PieceKind.HORSE
    assert board_from({}).piece_at(square(4, 4)) == EMPTY


def test_make_and_unmake_restore_fen_hash_and_side() -> None:
    rng = random.Random(20240607)
    board = Board.start()
    start_fen, start_hash = board.to_fen(), board.hash
    snapshots = [(start_fen, start_hash, board.side_to_move)]

    for _ in range(120):
        moves = board.legal_moves()
        if not moves:
            break
        move = rng.choice(moves)

        # A single make/unmake pair must be invisible.
        before = (board.to_fen(), board.hash, board.side_to_move, board.ply_count)
        board.make_move(move)
        board.unmake_move()
        assert (board.to_fen(), board.hash, board.side_to_move, board.ply_count) == before

        board.make_move(move)
        snapshots.append((board.to_fen(), board.hash, board.side_to_move))
        if rules.game_result(board, board.legal_moves()) is not None:
            break

    # Unwind the whole game and compare every intermediate position.
    while board.ply_count:
        board.unmake_move()
        assert board.to_fen() == snapshots[board.ply_count][0]
        assert board.hash == snapshots[board.ply_count][1]
        assert board.side_to_move is snapshots[board.ply_count][2]
    assert (board.to_fen(), board.hash) == (start_fen, start_hash)


def test_hash_ignores_move_order() -> None:
    # Two different orders of the same four independent moves transpose into the
    # same position, so the incremental hashes must agree.
    first = Board.start()
    play(first, "b0c2 b9c7 h2e2 h7e7")
    second = Board.start()
    play(second, "h2e2 h7e7 b0c2 b9c7")
    assert first.to_fen() == second.to_fen()
    assert first.hash == second.hash


def test_hash_includes_side_to_move() -> None:
    red_to_move = Board.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
    black_to_move = Board.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 b - - 0 1")
    assert red_to_move.hash != black_to_move.hash


def test_history_records_check_flags() -> None:
    board = board_from({"f0": "K", "e9": "k", "d2": "R"})
    move = find_move(board, "d2e2")  # rook checks the black general along the e-file
    board.make_move(move)
    assert board.records[-1].gave_check is True
    assert board.records[-1].hash_after == board.hash
    board.unmake_move()
    assert board.records == []

    # record_check=False is the search's shortcut; the flag is then simply False.
    board.make_move(move, record_check=False)
    assert board.records[-1].gave_check is False
    board.unmake_move()


def test_gives_check_does_not_change_the_board() -> None:
    board = board_from({"f0": "K", "e9": "k", "d2": "R"})
    before = (board.to_fen(), board.hash, board.ply_count)
    assert board.gives_check(find_move(board, "d2e2")) is True
    assert board.gives_check(find_move(board, "d2d3")) is False
    assert (board.to_fen(), board.hash, board.ply_count) == before


def test_general_square_is_tracked_across_moves() -> None:
    board = board_from({"e0": "K", "a9": "k"})
    red_general = board.general_square(Color.RED)
    board.make_move(find_move(board, "e0d0"))
    assert board.general_square(Color.RED) == square(3, 0)
    board.unmake_move()
    assert board.general_square(Color.RED) == red_general
