"""Random legal-move bot: the weakest baseline.

It picks uniformly from the legal moves with a seeded RNG, so a tournament with
fixed seeds is reproducible. Used as the floor that greedy, alpha-beta, and MCTS
have to beat (M2, M3, M5 acceptance criteria).
"""

from __future__ import annotations

import random

from bots.base import Bot, Budget, SearchResult
from engine.board import Board


class RandomBot(Bot):
    """Plays a uniformly random legal move."""

    type_name = "random"

    def __init__(self, seed: int | None = None, **params: object) -> None:
        super().__init__(params)
        # Every random choice in this project takes an explicit seed; passing None
        # gives a fresh generator, which is what interactive play wants.
        self._rng = random.Random(seed)
        self.seed = seed

    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        """Return a uniformly random legal move; the budget is irrelevant here."""
        legal = board.legal_moves()
        if not legal:
            raise ValueError("no legal moves: the game is already over")
        return SearchResult(move=self._rng.choice(legal))
