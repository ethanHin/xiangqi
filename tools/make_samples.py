"""One-off generator for the sample games committed under games/samples/.

Run from the repository root after the harness exists:

    python tools/make_samples.py

Keeping the samples reproducible (fixed seeds, scripted moves for the rule
demonstration) is the point; the files themselves are committed so the replay
viewer has something to load in M7.
"""

from __future__ import annotations

from pathlib import Path

from bots.base import Bot, Budget, SearchResult
from engine.board import Board
from harness.match import MatchJob, play_game, play_job, save_game


class ScriptedBot(Bot):
    """Plays a fixed list of ICCS moves (used for the perpetual-check sample)."""

    type_name = "scripted"

    def __init__(self, move_names: list[str]) -> None:
        super().__init__()
        self._move_names = list(move_names)

    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        name = self._move_names.pop(0)
        for move in board.legal_moves():
            if move.to_iccs() == name:
                return SearchResult(move=move)
        raise AssertionError(f"{name} is not legal in {board.to_fen()}")


def main() -> None:
    out = Path("games/samples")
    out.mkdir(parents=True, exist_ok=True)

    save_game(
        play_job(
            MatchJob(
                red={"type": "greedy", "params": {}},
                black={"type": "random", "params": {}},
                seed=101,
                game_id="sample-greedy-vs-random",
                opening_plies=6,
            )
        ),
        out,
    )
    save_game(
        play_job(
            MatchJob(
                red={"type": "random", "params": {}},
                black={"type": "random", "params": {}},
                seed=102,
                game_id="sample-random-vs-random",
                opening_plies=6,
            )
        ),
        out,
    )

    # A short demonstration of the perpetual-check rule: Red checks on every move
    # and loses, so the replay viewer has an example of each ending type.
    perpetual = MatchJob(
        red={"type": "scripted", "params": {}},
        black={"type": "scripted", "params": {}},
        seed=103,
        game_id="sample-perpetual-check",
        opening_plies=0,
        start_fen="4k4/9/9/9/9/9/9/3R5/9/5K3 w - - 0 1",
    )
    save_game(
        play_game(
            perpetual,
            red_bot=ScriptedBot(["d2e2", "e2d2", "d2e2", "e2d2"]),
            black_bot=ScriptedBot(["e9d9", "d9e9", "e9d9", "d9e9"]),
        ),
        out,
    )
    print(f"wrote samples to {out}")


if __name__ == "__main__":
    main()
