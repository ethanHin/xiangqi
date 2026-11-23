"""Greedy material bot: plays a capture worth the most, otherwise any move.

This is the M2 baseline that alpha-beta has to beat (M3 acceptance). It is
deliberately shallow: one ply, material only. It does not look at what the
opponent does next, so it will happily walk into a recapture -- that weakness is
exactly what the search bots demonstrate they fix.

Ties between equally valuable moves are broken with a seeded RNG. That matters:
picking a fixed move from the generator's order makes the bot shuffle between the
same two squares forever, which produces endless repetition draws and would make
the baseline look far weaker than it is. Seeding keeps runs reproducible.
"""

from __future__ import annotations

import random

from bots.base import Bot, Budget, SearchResult
from engine.board import Board
from engine.types import Move
from eval.material import piece_value

# Score reported when the chosen move ends the game outright. Bigger than any
# material gain, so the reported eval reads "this wins" rather than "this wins a horse".
MATE_SCORE = 100_000


class GreedyBot(Bot):
    """Picks a move with the largest immediate material gain, ties at random."""

    type_name = "greedy"

    def __init__(self, seed: int | None = None, **params: object) -> None:
        super().__init__(params)
        self._rng = random.Random(seed)
        self.seed = seed

    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        """Return one of the highest-value captures, or a random quiet move.

        ``score`` is the material gained in centipawns (0 for a quiet move), which
        keeps the reported eval honest about how narrow this bot's view is.

        Ties are broken by MVV-LVA: prefer the capture that gives up the least
        valuable attacker, so a horse takes a cannon rather than a chariot doing it.
        Anything still tied is chosen at random with the seeded RNG, because always
        picking the same square from the generator's order makes the bot shuffle
        forever and turns every game into a repetition draw.
        """
        legal = board.legal_moves()
        if not legal:
            raise ValueError("no legal moves: the game is already over")

        best_gain = 0
        best_attacker = 0
        best_moves = []
        for move in legal:
            # Only the captured piece matters; a crossed-river soldier is worth 200.
            gain = piece_value(move.captured, move.to_square) if move.captured else 0
            attacker = (
                piece_value(board.piece_at(move.from_square), move.from_square) if gain else 0
            )
            if gain > best_gain or (gain == best_gain and gain and attacker < best_attacker):
                best_gain, best_attacker, best_moves = gain, attacker, [move]
            elif gain == best_gain:
                best_moves.append(move)

        # A move that leaves the opponent with no legal move wins on the spot (in
        # Xiangqi a stalemate is a loss too). Only checking moves can do that, so
        # the expensive test runs on the handful of checks, not on all 40 moves.
        for move in best_moves:
            if board.gives_check(move) and _leaves_no_reply(board, move):
                return SearchResult(move=move, score=MATE_SCORE)

        return SearchResult(move=self._rng.choice(best_moves), score=best_gain)


def _leaves_no_reply(board: Board, move: Move) -> bool:
    """True if playing ``move`` leaves the opponent without a legal move."""
    board.make_move(move, record_check=False)
    try:
        return not board.legal_moves()
    finally:
        board.unmake_move()
