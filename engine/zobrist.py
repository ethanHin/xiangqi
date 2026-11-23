"""Zobrist hashing: one random 64-bit key per (piece, square) and per side to move.

The board XORs the keys of the squares a move touches, so ``board.hash`` is kept
up to date in O(1) by ``make_move`` and restored exactly by ``unmake_move``.
The side-to-move key is part of the hash, which matters twice:

* the repetition rule counts positions with "same pieces, same side to move", and
* the transposition table in M3 must not confuse the two turns of the same placement.

Keys are generated from a fixed seed, so hashes are identical across runs and
processes. That makes saved games and test expectations reproducible.
"""

from __future__ import annotations

import random

from engine.types import BOARD_SQUARES, PIECE_CODE_COUNT

# Fixed seed: Zobrist keys must never change between runs, or hashes recorded in
# tests (and the transposition table's reproducibility) would be meaningless.
ZOBRIST_SEED = 0x5EED_C0DE2123


def _build_keys() -> tuple[tuple[tuple[int, ...], ...], int]:
    """Generate the piece/square key table and the side-to-move key."""
    rng = random.Random(ZOBRIST_SEED)
    piece_keys = tuple(
        tuple(rng.getrandbits(64) for _ in range(BOARD_SQUARES)) for _ in range(PIECE_CODE_COUNT)
    )
    side_key = rng.getrandbits(64)
    return piece_keys, side_key


PIECE_KEYS, SIDE_KEY = _build_keys()
