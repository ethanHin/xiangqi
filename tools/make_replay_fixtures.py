"""Write the replay fixtures: the expected FEN after every ply of every sample game.

The browser viewer builds its timeline in JavaScript; these fixtures are produced by
the Python engine from the same files, so the Node test can prove that the two agree
ply by ply. The timeline rule is the viewer's:

* if the game has ``opening_moves`` and replaying them from the standard start
  position lands exactly on ``start_fen``, the opening plies are part of the timeline;
* otherwise the timeline starts at ``start_fen``.

Usage, from the repository root::

    python -m tools.make_replay_fixtures
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.board import START_FEN, Board

SAMPLES_DIR = Path("games/samples")
FIXTURES_PATH = Path("tests/fixtures/replay/replay_fixtures.json")


def _move(board: Board, iccs: str):
    """The legal move with this ICCS name (the file is trusted to contain legal moves)."""
    for move in board.legal_moves():
        if move.to_iccs() == iccs:
            return move
    raise RuntimeError(f"{iccs} is not legal in {board.to_fen()}")


def timeline(game: dict) -> tuple[list[str], int]:
    """FEN after each ply in the viewer's timeline, plus the number of opening plies."""
    opening = game.get("opening_moves") or []
    states: list[str] = []
    opening_count = 0

    if opening:
        board = Board.from_fen(START_FEN)
        states = [board.to_fen()]
        for iccs in opening:
            board.make_move(_move(board, iccs))
            states.append(board.to_fen())
        if board.to_fen() != Board.from_fen(game["start_fen"]).to_fen():
            states = []
        else:
            opening_count = len(opening)

    if not states:
        board = Board.from_fen(game["start_fen"])
        states = [board.to_fen()]

    for record in game["moves"]:
        iccs = record["move"] if isinstance(record, dict) else record
        board.make_move(_move(board, iccs))
        states.append(board.to_fen())

    return states, opening_count


def main() -> None:
    samples = []
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        game = json.loads(path.read_text(encoding="utf-8"))
        states, opening_count = timeline(game)
        samples.append(
            {
                "file": path.as_posix(),
                "id": game.get("id"),
                "result": game.get("result"),
                "reason": game.get("reason"),
                "plies": game.get("plies"),
                "opening_count": opening_count,
                "timeline": states,
            }
        )

    FIXTURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES_PATH.write_text(
        json.dumps(
            {
                "generated_by": "python -m tools.make_replay_fixtures",
                "samples": samples,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURES_PATH} with {len(samples)} samples")


if __name__ == "__main__":
    main()
