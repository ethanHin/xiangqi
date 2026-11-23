"""FastAPI application for live play.

A thin HTTP layer over the engine, the bots, and the harness:

* game creation, moves, stepping, undo, resignation and export are implemented in
  ``server.games`` (which reuses the harness's game loop and the engine's rules);
* bot types and parameter validation come from ``server.registry``;
* the replay viewer in ``web/`` is served at ``/`` so ``python -m server`` runs the
  whole app, and the interactive API docs stay at ``/docs``.

All endpoints are plain ``def`` functions, so FastAPI runs them in its threadpool and
a bot search does not block the event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.board import Board
from server import games, registry

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="Xiangqi live play",
    version="1.0.0",
    description=(
        "Local single-user server for bot-vs-bot and human-vs-bot Xiangqi games. "
        "The rules, the search and the game loop come from the engine and the harness; "
        "this layer only validates input and keeps game state."
    ),
)


# ---------------------------------------------------------------------------
# Request models (these also drive the /docs forms)
# ---------------------------------------------------------------------------


class PlayerSpecModel(BaseModel):
    """A player: ``{"type": "human"}`` or a bot spec with ``params``."""

    type: str = Field(..., examples=["human", "alphabeta", "mcts"])
    params: dict[str, Any] = Field(default_factory=dict)


class CreateGameRequest(BaseModel):
    """Body of ``POST /api/games``."""

    red: PlayerSpecModel
    black: PlayerSpecModel
    seed: int | None = Field(
        default=None, description="Seed for the random opening and bot randomness"
    )
    opening_plies: int | None = Field(
        default=None, ge=0, le=8, description="Random opening plies (default 6)"
    )
    start_fen: str | None = Field(
        default=None, description="Start from this position (after the opening)"
    )


class MoveRequest(BaseModel):
    """Body of ``POST /api/games/{id}/move``."""

    move: str = Field(..., examples=["h2e2"], description="ICCS move, for example h2e2")


class ResignRequest(BaseModel):
    """Body of ``POST /api/games/{id}/resign``."""

    color: str = Field(..., examples=["red", "black"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_game(game_id: str) -> games.Game:
    """Look up a game or raise 404."""
    try:
        return games.STORE.get(game_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown game id {game_id!r}") from None


def _validated_players(request: CreateGameRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate both player specs, returning them with defaults filled in."""
    try:
        red = registry.validate_player_spec(request.red.model_dump(), "red")
        black = registry.validate_player_spec(request.black.model_dump(), "black")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    if registry.is_human(red) and registry.is_human(black):
        raise HTTPException(
            status_code=400,
            detail="a game needs at least one bot: both sides cannot be human",
        )
    return red, black


# ---------------------------------------------------------------------------
# Registry and game list
# ---------------------------------------------------------------------------


@app.get("/api/bots")
def list_bots() -> dict[str, Any]:
    """Every bot type the API can build, with parameter schemas and defaults."""
    return {"bots": registry.bot_schema()}


@app.get("/api/games")
def list_games() -> dict[str, Any]:
    """The newest live and saved games (at most 25), newest first."""
    return {"games": games.STORE.list()}


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------


@app.post("/api/games", status_code=201)
def create_game(request: CreateGameRequest) -> dict[str, Any]:
    """Create a game, applying the seeded random opening, and return its state."""
    red, black = _validated_players(request)
    if request.start_fen is not None:
        try:
            Board.from_fen(request.start_fen)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"start_fen: {error}") from None
    game = games.STORE.create(
        red,
        black,
        seed=request.seed,
        opening_plies=request.opening_plies,
        start_fen=request.start_fen,
    )
    return game.state()


@app.get("/api/games/{game_id}")
def get_game(game_id: str) -> dict[str, Any]:
    """Current state of one game."""
    game = _get_game(game_id)
    with game.lock:
        return game.state()


@app.post("/api/games/{game_id}/move")
def play_move(game_id: str, request: MoveRequest) -> dict[str, Any]:
    """Play a human move. Illegal, wrong-turn and finished-game requests are 400."""
    game = _get_game(game_id)
    with game.lock:
        try:
            game.play_human_move(request.move)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return game.state()


@app.post("/api/games/{game_id}/step")
def step_game(game_id: str) -> dict[str, Any]:
    """Let the bot on the side to move play one move; also reports what the search used."""
    game = _get_game(game_id)
    with game.lock:
        try:
            search = game.step()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        state = game.state()
    state["search"] = search
    return state


@app.post("/api/games/{game_id}/undo")
def undo_move(game_id: str) -> dict[str, Any]:
    """Undo the last human move and the bot reply (or the last ply), clearing a result."""
    game = _get_game(game_id)
    with game.lock:
        try:
            game.undo()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return game.state()


@app.post("/api/games/{game_id}/resign")
def resign_game(game_id: str, request: ResignRequest) -> dict[str, Any]:
    """Resign: the other side wins with reason ``resign``."""
    game = _get_game(game_id)
    with game.lock:
        try:
            game.resign(request.color)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return game.state()


@app.get("/api/games/{game_id}/export")
def export_game(game_id: str) -> dict[str, Any]:
    """The game in the Game JSON schema, ready for the replay viewer."""
    game = _get_game(game_id)
    with game.lock:
        return game.export()


# Finished games are also reachable as static files, so the replay viewer served at /
# can open them with ?game=games/<id>.json after a game ends.
GAMES_DIR = Path("games")
if GAMES_DIR.is_dir():
    app.mount("/games", StaticFiles(directory=str(GAMES_DIR), html=False), name="games")

# The replay viewer is a static page; mounting it last leaves /api and /docs intact.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
