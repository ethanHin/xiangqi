"""Perft: count the legal move tree and compare it with published Xiangqi values.

These numbers are the project's ground truth for move generation. If a value
disagrees, the engine is wrong; the expected numbers are never adjusted (see
AGENTS.md).

Depth 4 visits 3.29 million positions and takes roughly 25 seconds in pure
Python, so it is marked ``slow`` and deselected by default. Run it with
``pytest -m slow``.
"""

from __future__ import annotations

import pytest

from engine import movegen
from engine.board import Board

# Published Xiangqi perft values from the standard starting position.
PERFT_REFERENCE = {1: 44, 2: 1920, 3: 79666, 4: 3290240}


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_perft_matches_reference(depth: int) -> None:
    board = Board.start()
    assert movegen.perft(board, depth) == PERFT_REFERENCE[depth]
    # Perft must leave the board exactly as it found it.
    assert board.to_fen() == Board.start().to_fen()
    assert board.ply_count == 0


@pytest.mark.slow
def test_perft_depth_4_matches_reference() -> None:
    board = Board.start()
    assert movegen.perft(board, 4) == PERFT_REFERENCE[4]
    assert board.to_fen() == Board.start().to_fen()
