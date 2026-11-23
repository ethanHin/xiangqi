"""The harness: play one game between two bots and return a GameRecord.

The match runner is where the engine, the bots, and the rules meet. One game is:

1. start from the standard position (or a given FEN), play N random opening plies
   from a seeded RNG, and remember the resulting FEN;
2. loop: generate legal moves, ask ``rules.game_result`` whether the game is over,
   ask the bot on the side to move for a move, validate it, play it, record it;
3. return a ``GameRecord`` that serialises to the Game JSON schema.

Two details the rest of the project depends on:

* ``board.make_move`` is called with the **default** ``record_check=True``. The
  perpetual-check rule needs the check flag for every ply, so the harness must not
  take the search's shortcut here.
* ``play_job`` is a module-level function taking only a picklable ``MatchJob``, so
  the tournament can run games in a ``ProcessPoolExecutor``. On Windows (spawn)
  that requires the worker to be importable and the pool to be created under an
  ``if __name__ == "__main__"`` guard; see ``harness.tournament``.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bots.base import Bot, Budget, build_bot
from engine import rules
from engine.board import Board
from engine.types import Color, Move, Result

# Per-move time cap used by the acceptance matches and the default tournament
# setting. Simple bots ignore it; search bots must respect it.
DEFAULT_MOVE_TIME_LIMIT_MS = 200.0

# Opening: N random legal plies from a seeded RNG (project decision: default 6,
# configurable 4-8; 0 is allowed for tests and for the server's start_fen path).
DEFAULT_OPENING_PLIES = 6
MAX_OPENING_PLIES = 8


# ---------------------------------------------------------------------------
# Game JSON records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoveRecord:
    """One ply as it appears in the game JSON ``moves`` list."""

    move: str
    time_ms: float
    nodes: int
    depth: int
    eval: int | None

    def to_json(self) -> dict[str, Any]:
        """Serialise to the Game JSON move shape."""
        return {
            "move": self.move,
            "time_ms": round(self.time_ms, 2),
            "nodes": self.nodes,
            "depth": self.depth,
            "eval": self.eval,
        }


@dataclass
class GameRecord:
    """A whole game, matching the Game JSON schema in AGENTS.md."""

    id: str
    red: dict[str, Any]
    black: dict[str, Any]
    seed: int
    start_fen: str
    opening_moves: list[str]
    moves: list[MoveRecord] = field(default_factory=list)
    result: str | None = None
    reason: str | None = None
    plies: int = 0

    def to_json(self) -> dict[str, Any]:
        """Serialise to the Game JSON schema.

        ``plies`` counts every ply of the game, including the random opening; the
        replay screen only needs ``start_fen`` plus ``moves``.
        """
        return {
            "id": self.id,
            "red": self.red,
            "black": self.black,
            "seed": self.seed,
            "start_fen": self.start_fen,
            "opening_moves": self.opening_moves,
            "moves": [move.to_json() for move in self.moves],
            "result": self.result,
            "reason": self.reason,
            "plies": self.plies,
        }


@dataclass(frozen=True)
class MatchJob:
    """Everything needed to play one game, as plain picklable values."""

    red: Mapping[str, Any]
    black: Mapping[str, Any]
    seed: int
    game_id: str
    opening_plies: int = DEFAULT_OPENING_PLIES
    move_time_limit_ms: float = DEFAULT_MOVE_TIME_LIMIT_MS
    start_fen: str | None = None


# ---------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------


def play_game(
    job: MatchJob,
    red_bot: Bot | None = None,
    black_bot: Bot | None = None,
) -> GameRecord:
    """Play one game and return its record.

    ``red_bot`` and ``black_bot`` may be supplied to inject specific bots (tests
    use scripted ones); otherwise they are built from the job's player specs. The
    seed is handed to ``build_bot`` so the random bot is reproducible.
    """
    board = Board.from_fen(job.start_fen) if job.start_fen else Board.start()
    if red_bot is None:
        red_bot = build_bot(job.red, seed=job.seed)
    if black_bot is None:
        black_bot = build_bot(job.black, seed=job.seed)

    opening = play_random_opening(board, job.opening_plies, job.seed)
    record = GameRecord(
        id=job.game_id,
        red=dict(job.red),
        black=dict(job.black),
        seed=job.seed,
        start_fen=board.to_fen(),
        opening_moves=opening,
    )

    budget = Budget(time_limit_ms=job.move_time_limit_ms)
    result = play_moves(board, red_bot, black_bot, budget, record)
    record.result = result_name(result)
    record.reason = result.reason.value
    record.plies = len(record.opening_moves) + len(record.moves)
    return record


def play_job(job: MatchJob) -> GameRecord:
    """Process-pool worker: build the bots from the job and play the game."""
    return play_game(job)


def play_moves(
    board: Board,
    red_bot: Bot,
    black_bot: Bot,
    budget: Budget,
    record: GameRecord,
) -> Result:
    """Play out the position on ``board`` until the game ends, filling ``record.moves``.

    Returns the terminal ``Result``. The board is left in the final position.
    """
    while True:
        legal = board.legal_moves()
        result = rules.game_result(board, legal)
        if result is not None:
            return result

        bot = red_bot if board.side_to_move is Color.RED else black_bot

        started = time.perf_counter()
        search = bot.choose_move(board, budget)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        move = _resolve_move(legal, search.move, bot)

        # Default record_check=True: the perpetual-check rule needs the check flag
        # of every ply, so the game loop must never use the search's shortcut.
        board.make_move(move)
        record.moves.append(
            MoveRecord(
                move=move.to_iccs(),
                time_ms=elapsed_ms,
                nodes=search.nodes,
                depth=search.depth,
                eval=search.score,
            )
        )


def play_random_opening(board: Board, plies: int, seed: int) -> list[str]:
    """Play ``plies`` random legal plies on ``board`` and return them as ICCS names.

    A candidate ply is only accepted if it does not immediately end the game
    (third repetition or mate), per the project decision; candidates are tried in
    a shuffled order so the refusal is still seeded. The moves stay in the board's
    history, so the 300-ply cap counts them.
    """
    if plies <= 0:
        return []
    rng = random.Random(seed)
    opening: list[str] = []
    for _ in range(plies):
        legal = board.legal_moves()
        if not legal:
            break
        rng.shuffle(legal)
        for candidate in legal:
            board.make_move(candidate)
            if rules.game_result(board, board.legal_moves()) is None:
                opening.append(candidate.to_iccs())
                break
            board.unmake_move()
        else:
            # Every legal move ends the game: nothing sensible to add.
            break
    return opening


def build_rng(seed: int) -> random.Random:
    """Seeded RNG for opening selection (kept separate from any bot's RNG)."""
    return random.Random(seed)


def result_name(result: Result) -> str:
    """``"red_win"``, ``"black_win"``, or ``"draw"``, as in the Game JSON schema."""
    if result.winner is None:
        return "draw"
    return "red_win" if result.winner is Color.RED else "black_win"


def save_game(record: GameRecord, directory: Path | str = "games") -> Path:
    """Write ``record`` to ``directory/<id>.json`` and return the path."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{record.id}.json"
    path.write_text(json.dumps(record.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_move(legal: list[Move], candidate: Move, bot: Bot) -> Move:
    """Match the bot's move against the legal list, or raise a clear error.

    Only the origin and destination are compared: bots may build a ``Move``
    without knowing what it captures, and the harness owns the authoritative
    ``Move`` object (with the correct ``captured`` code) used by ``make_move``.
    """
    for move in legal:
        if move.from_square == candidate.from_square and move.to_square == candidate.to_square:
            return move
    raise RuntimeError(f"{bot.spec['type']} returned the illegal move {candidate.to_iccs()}")
