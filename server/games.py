"""In-memory game store for the live-play server.

One ``Game`` holds the board, the recorded moves, the two player specs, and a lock.
The store keeps games in a dictionary keyed by a random id, caps how many it holds
(evicting finished games first), and writes a finished game to ``games/`` exactly once
(and again only if an undo is followed by a new finish, overwriting the same file).
``list()`` also reports finished games found on disk but no longer in memory, so the
Recent games list survives a server restart.

Everything about the rules is delegated: the random opening is
``harness.match.play_random_opening``, a bot move is ``harness.match.play_bot_move``
(which uses the default ``record_check=True``), and terminal detection is
``engine.rules.game_result``. No rules or search logic lives here.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bots.base import Bot, Budget
from engine import rules
from engine.board import Board
from engine.types import Color, Reason, Result
from harness.match import (
    GameRecord,
    MoveRecord,
    play_bot_move,
    play_random_opening,
    result_name,
    save_game,
)
from server import registry

# Hard cap on any single bot search, whatever the client asked for.
SERVER_MAX_MOVE_MS = 10_000.0

# How many games the in-memory store keeps before evicting (finished games first).
MAX_GAMES = 200

# GET /api/games returns at most this many rows, live and saved together.
LIST_LIMIT = 25

# Results a saved Game JSON may carry; anything else is not a finished game.
_SAVED_RESULTS = ("red_win", "black_win", "draw")

# Where finished games are written (Game JSON), relative to the working directory.
SAVE_DIR = Path("games")

# The project's default random-opening length.
DEFAULT_OPENING_PLIES = 6
MAX_OPENING_PLIES = 8

# Bot seeds are derived from (game seed, ply, side) instead of a shared stream, so a
# game is reproducible regardless of the order in which its requests arrive. The
# multiplier is Knuth's, to spread similar inputs apart.
_SEED_MULTIPLIER = 2_654_435_761


def bot_seed(game_seed: int, ply: int, side: Color) -> int:
    """Deterministic RNG seed for the bot that moves at ``ply`` for ``side``."""
    side_offset = 0 if side is Color.RED else 1
    return (game_seed * _SEED_MULTIPLIER + ply * 97 + side_offset) & 0x7FFFFFFF


def _side_name(color: Color) -> str:
    return "red" if color is Color.RED else "black"


def _side_color(name: str) -> Color:
    if name not in ("red", "black"):
        raise ValueError("color must be 'red' or 'black'")
    return Color.RED if name == "red" else Color.BLACK


class Game:
    """One live game.

    Methods assume the caller holds ``self.lock`` (the endpoints take it around a
    whole request so turn checks and the move that follows cannot interleave).
    """

    def __init__(
        self,
        game_id: str,
        board: Board,
        red: dict[str, Any],
        black: dict[str, Any],
        seed: int,
        record: GameRecord,
        save_dir: Path,
        bot_builder: Callable[[dict[str, Any], int], Bot] | None = None,
    ) -> None:
        self.id = game_id
        self.board = board
        self.red = red
        self.black = black
        self.seed = seed
        self.record = record
        self.save_dir = save_dir
        self.lock = threading.Lock()
        self.created = time.time()
        self.finished_at: float | None = None
        self.result: Result | None = None
        self._bot_builder = bot_builder or registry.build_bot_for_spec

    # -- queries ---------------------------------------------------------

    def spec_for(self, color: Color) -> dict[str, Any]:
        """The player spec of the side to move."""
        return self.red if color is Color.RED else self.black

    def human_to_move(self) -> bool:
        """True if the side to move is a human."""
        return registry.is_human(self.spec_for(self.board.side_to_move))

    def state(self) -> dict[str, Any]:
        """The game-state response documented in AGENTS.md."""
        moves = [move.to_json() for move in self.record.moves]
        return {
            "id": self.id,
            "fen": self.board.to_fen(),
            "side_to_move": _side_name(self.board.side_to_move),
            "players": {"red": self.red, "black": self.black},
            "legal_moves": [move.to_iccs() for move in self.board.legal_moves()],
            "in_check": self.board.is_in_check(),
            "last_move": self.record.moves[-1].move if self.record.moves else None,
            "moves": moves,
            "result": self.record.result,
            "reason": self.record.reason,
            "plies": self.record.plies,
        }

    def export(self) -> dict[str, Any]:
        """The game in the Game JSON schema (loadable by the replay viewer)."""
        return self.record.to_json()

    # -- actions (caller holds the lock) ---------------------------------

    def play_human_move(self, iccs: str) -> None:
        """Play a human move after checking that it is legal and that it is their turn."""
        if self.result is not None:
            raise ValueError("the game is over")
        if not self.human_to_move():
            raise ValueError("it is not a human's turn")
        legal = self.board.legal_moves()
        move = next((candidate for candidate in legal if candidate.to_iccs() == iccs), None)
        if move is None:
            raise ValueError(f"illegal move {iccs!r}")

        # Default record_check=True: human games need the perpetual-check rule too.
        self.board.make_move(move)
        self._append_record(MoveRecord(move=iccs, time_ms=0.0, nodes=0, depth=0, eval=None))
        self._finish_if_over()

    def step(self) -> dict[str, Any]:
        """Let the bot on the side to move play one move; return what the search used."""
        if self.result is not None:
            raise ValueError("the game is over")
        if self.human_to_move():
            raise ValueError("it is a human's turn; use /move")

        spec = self.spec_for(self.board.side_to_move)
        bot = self._bot_builder(
            spec, bot_seed(self.seed, self.board.ply_count, self.board.side_to_move)
        )
        requested_time = spec["params"].get("time_limit_ms")
        applied_time = _clamp_time(requested_time)
        budget = Budget(time_limit_ms=applied_time)

        search = play_bot_move(self.board, bot, budget, self.record)
        self._sync_plies()
        self._finish_if_over()
        return {
            "requested_time_limit_ms": requested_time,
            "applied_time_limit_ms": applied_time,
            "depth": search.depth,
            "nodes": search.nodes,
            # For MCTS the bot's "nodes" are its completed iterations.
            "iterations": search.nodes if spec["type"] == "mcts" else None,
            "elapsed_ms": round(search.elapsed_ms, 2),
        }

    def undo(self) -> None:
        """Undo the last human move and the bot reply, or the last ply for bot games."""
        opening_plies = len(self.record.opening_moves)
        if self.board.ply_count <= opening_plies:
            raise ValueError("nothing to undo: the game is at its starting position")

        human_sides = [
            color for color in (Color.RED, Color.BLACK) if registry.is_human(self.spec_for(color))
        ]
        self._unmake_one_ply()
        if len(human_sides) == 1:
            human = human_sides[0]
            # Keep going until it is the human's turn again, never past the opening.
            while self.board.ply_count > opening_plies and self.board.side_to_move is not human:
                self._unmake_one_ply()

        self.result = None
        self.finished_at = None
        self.record.result = None
        self.record.reason = None
        self._sync_plies()

    def resign(self, color_name: str) -> None:
        """End the game as a win for the other side, with reason ``resign``."""
        if self.result is not None:
            raise ValueError("the game is over")
        color = _side_color(color_name)
        winner = color.opponent
        self.result = Result(winner=winner, reason=Reason.RESIGN)
        self.finished_at = time.time()
        self.record.result = result_name(self.result)
        self.record.reason = Reason.RESIGN.value
        self._sync_plies()
        self._save()

    # -- private ---------------------------------------------------------

    def _append_record(self, move_record: MoveRecord) -> None:
        self.record.moves.append(move_record)
        self._sync_plies()

    def _sync_plies(self) -> None:
        """Keep ``record.plies`` equal to the opening plus the recorded moves."""
        self.record.plies = len(self.record.opening_moves) + len(self.record.moves)

    def _unmake_one_ply(self) -> None:
        self.board.unmake_move()
        self.record.moves.pop()

    def _finish_if_over(self) -> None:
        """Detect a finished game, set the record fields, and autosave once."""
        legal = self.board.legal_moves()
        result = rules.game_result(self.board, legal)
        if result is None:
            return
        self.result = result
        self.finished_at = time.time()
        self.record.result = result_name(result)
        self.record.reason = result.reason.value
        self._sync_plies()
        self._save()

    def _save(self) -> None:
        """Write the Game JSON to ``save_dir/<id>.json`` (overwriting a previous finish)."""
        save_game(self.record, self.save_dir)


def _clamp_time(requested: float | None) -> float:
    """Apply the server's per-move time ceiling to a requested limit (None = ceiling)."""
    if requested is None:
        return SERVER_MAX_MOVE_MS
    return min(float(requested), SERVER_MAX_MOVE_MS)


def _saved_game_summaries(save_dir: Path) -> list[dict[str, Any]]:
    """Rows for finished games under ``save_dir``, for GET /api/games.

    Only the fields the list endpoint needs are read. A file that is not a valid
    game JSON (or cannot be read) is skipped rather than breaking the list, since
    the directory is runtime data. ``created`` comes from the file's modification
    time: the Game JSON schema stores no creation timestamp.
    """
    summaries: list[dict[str, Any]] = []
    for path in sorted(Path(save_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            modified = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        red, black = data.get("red"), data.get("black")
        result, reason, plies = data.get("result"), data.get("reason"), data.get("plies")
        if not (isinstance(red, dict) and isinstance(black, dict)):
            continue
        if not (isinstance(red.get("type"), str) and isinstance(black.get("type"), str)):
            continue
        if result not in _SAVED_RESULTS or not isinstance(reason, str) or not reason:
            continue
        if not isinstance(plies, int) or isinstance(plies, bool) or plies < 0:
            continue
        summaries.append(
            {
                "id": path.stem,
                "players": {"red": red, "black": black},
                "result": result,
                "reason": reason,
                "plies": plies,
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(modified)),
                "saved": True,
            }
        )
    return summaries


class GameStore:
    """A thread-safe dictionary of live games with a size cap."""

    def __init__(self, save_dir: Path | str = SAVE_DIR, max_games: int = MAX_GAMES) -> None:
        self._games: dict[str, Game] = {}
        self._lock = threading.Lock()
        self.save_dir = Path(save_dir)
        self.max_games = max_games

    def create(
        self,
        red: dict[str, Any],
        black: dict[str, Any],
        *,
        seed: int | None = None,
        opening_plies: int | None = None,
        start_fen: str | None = None,
        bot_builder: Callable[[dict[str, Any], int], Bot] | None = None,
    ) -> Game:
        """Create a game, applying the harness's seeded random opening.

        ``bot_builder`` is a test seam: production always uses the registry factory,
        while tests can inject scripted bots to drive a specific rule sequence.
        """
        game_seed = seed if seed is not None else int.from_bytes(uuid.uuid4().bytes[:4], "big")
        plies = DEFAULT_OPENING_PLIES if opening_plies is None else opening_plies
        board = Board.from_fen(start_fen) if start_fen else Board.start()
        opening = play_random_opening(board, plies, game_seed)

        game_id = uuid.uuid4().hex[:12]
        record = GameRecord(
            id=game_id,
            red=red,
            black=black,
            seed=game_seed,
            start_fen=board.to_fen(),
            opening_moves=opening,
            plies=len(opening),
        )
        game = Game(
            game_id,
            board,
            red,
            black,
            game_seed,
            record,
            self.save_dir,
            bot_builder=bot_builder,
        )
        game._finish_if_over()

        with self._lock:
            self._evict_if_needed()
            self._games[game_id] = game
        return game

    def get(self, game_id: str) -> Game:
        """The game with this id, or ``KeyError``."""
        with self._lock:
            return self._games[game_id]

    def list(self) -> list[dict[str, Any]]:
        """The newest live and saved games, as summary rows.

        Live games are marked ``saved: False``; a finished game found only on disk is
        marked ``saved: True`` and appears once even when a live copy also exists.
        Saved-only rows use the file's modification time as ``created``. At most
        ``LIST_LIMIT`` rows are returned, newest first.
        """
        with self._lock:
            live = sorted(self._games.values(), key=lambda game: game.created, reverse=True)
        rows = [
            {
                "id": game.id,
                "players": {"red": game.red, "black": game.black},
                "result": game.record.result,
                "reason": game.record.reason,
                "plies": game.record.plies,
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(game.created)),
                "saved": False,
            }
            for game in live
        ]
        live_ids = {row["id"] for row in rows}
        rows.extend(
            row for row in _saved_game_summaries(self.save_dir) if row["id"] not in live_ids
        )
        rows.sort(key=lambda row: row["created"], reverse=True)
        return rows[:LIST_LIMIT]

    def _evict_if_needed(self) -> None:
        """Under the store lock: drop finished games first, then the oldest (see class doc)."""
        while len(self._games) >= self.max_games:
            finished = [game for game in self._games.values() if game.finished_at is not None]
            if finished:
                victim = min(finished, key=lambda game: game.finished_at)
            else:
                victim = min(self._games.values(), key=lambda game: game.created)
            del self._games[victim.id]


# The live store used by the API. Tests replace this with a temporary one.
STORE = GameStore()
