"""The bot interface plus the budget/result dataclasses every bot shares.

A bot only has to answer one question: given a board and a budget, which move do
you play? It answers with a ``SearchResult``, which carries the move *and* the
statistics the harness, the experiment reports, and the play UI need: score in
centipawns, depth reached, nodes searched, and elapsed time.

``Bot.spec`` turns a bot back into the player-spec JSON used by ``POST /api/games``
and by the Game JSON schema, so a saved game records exactly what played it.

``build_bot`` is the single construction point used by the harness, the
experiment scripts, and (from M8) the server registry.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from engine.board import Board
from engine.types import Move


@dataclass(frozen=True)
class Budget:
    """How much work a bot may spend on one move.

    Bots use whichever field they understand: alpha-beta uses ``depth`` and
    ``time_limit_ms`` together with iterative deepening, MCTS uses
    ``max_iterations`` and/or ``time_limit_ms``. ``None`` means "no limit from
    this field"; the server always supplies a time limit so a request cannot hang.
    """

    depth: int | None = None
    time_limit_ms: float | None = None
    max_iterations: int | None = None


@dataclass(frozen=True)
class SearchResult:
    """What a bot returns for one move.

    ``score`` is in centipawns from the mover's point of view (positive = good
    for the side that just moved), which is what the API exposes as ``eval``.
    Simple bots return ``depth=0`` and ``nodes=0``.
    """

    move: Move
    score: int = 0
    depth: int = 0
    nodes: int = 0
    elapsed_ms: float = 0.0


class Bot(ABC):
    """Base class for every bot.

    Subclasses set ``type_name`` (the registry key, e.g. ``"greedy"``), store
    their settings in ``params``, and implement ``choose_move``.
    """

    type_name: ClassVar[str] = "bot"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = dict(params or {})

    @property
    def spec(self) -> dict[str, Any]:
        """The bot as a player spec, e.g. ``{"type": "greedy", "params": {}}``."""
        return {"type": self.type_name, "params": dict(self.params)}

    @abstractmethod
    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        """Pick a legal move for ``board.side_to_move`` within ``budget``.

        The board must not be left modified. Raising ``ValueError`` is correct
        when there are no legal moves; the harness checks for a finished game
        first, so that only happens through misuse.
        """

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.params})"


# Bot types that ``build_bot`` knows about, with the parameters each one accepts.
# This is the seed of the M8 server registry: the UI will render these defaults.
# ``human`` is deliberately absent: a human is a player in the server, not a bot.
BOT_DEFAULTS: dict[str, dict[str, Any]] = {
    "random": {},
    "greedy": {},
    "alphabeta": {
        "depth": None,  # None = time-only; iterative deepening stops on time
        "time_limit_ms": 2000.0,
        "evaluator": "material",
        "quiescence": False,
    },
    "mcts": {
        "iterations": 1000,
        "time_limit_ms": None,
        "exploration": 1.4,
        "rollout_depth": 30,
        "evaluator": "full",
        "capture_bias": 0.0,
    },
}

BOT_TYPES: tuple[str, ...] = tuple(BOT_DEFAULTS)


def build_bot(spec: Mapping[str, Any], seed: int | None = None) -> Bot:
    """Build a bot from a player spec.

    ``spec`` is ``{"type": "random", "params": {...}}``. ``seed`` seeds any
    randomness the bot uses (the random and greedy bots); it is separate from
    ``params`` so the harness can make every game reproducible without changing the
    bot's serialised spec.

    Imports are done inside the function so that bot modules can import
    ``bots.base`` without a cycle.
    """
    bot_type = spec.get("type")
    params = dict(spec.get("params") or {})

    if bot_type == "random":
        from bots.random_bot import RandomBot

        return RandomBot(seed=seed, **params)
    if bot_type == "greedy":
        from bots.greedy_bot import GreedyBot

        return GreedyBot(seed=seed, **params)
    if bot_type == "alphabeta":
        from bots.alphabeta import AlphaBetaBot

        return AlphaBetaBot(**params)
    if bot_type == "mcts":
        from bots.mcts import MCTSBot

        return MCTSBot(seed=seed, **params)
    if bot_type == "human":
        raise ValueError("'human' is a player, not a bot; the server handles human moves")
    raise ValueError(f"unknown bot type {bot_type!r}; known types: {', '.join(BOT_TYPES)}")


def new_rng(seed: int | None) -> random.Random:
    """A ``random.Random`` from an explicit seed (``None`` means "not reproducible")."""
    return random.Random(seed)
