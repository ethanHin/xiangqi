"""Xiangqi engine package.

Layout (bottom-up dependency order):

* ``engine.types``   -- colors, piece codes, squares, moves, results, history records
* ``engine.zobrist`` -- Zobrist keys used for incremental hashing
* ``engine.board``   -- board state, FEN, make/unmake, attack detection
* ``engine.movegen`` -- pseudo-legal and legal move generation, perft
* ``engine.rules``   -- terminal detection (mate, stalemate, repetition, move cap)

Everything above this package (bots, harness, server) uses the API documented in
``AGENTS.md`` and never touches the square array directly.
"""
