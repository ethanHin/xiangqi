"""Generator for the sample games committed under games/samples/.

Run from the repository root:

    python -m tools.make_samples

The generated games are deterministic (fixed seeds, scripted moves for the
perpetual-check demonstration), so the committed files can be reproduced. The two
alpha-beta samples are copied from the M6 depth-scaling run, because that run carries
the per-move depth/nodes/eval the replay viewer is meant to display; the run folder is
gitignored, so those copies are the committed evidence. Regenerate the run with
``python -m experiments.depth_scaling --depths 1,2,3,4 --games 50`` if it is missing.

Sample set (one file per ending the viewer has to render):

* ``sample-checkmate``        -- 3 recorded moves ending in checkmate (M6, depth 2 vs 1)
* ``sample-stalemate``        -- stalemate is a loss in Xiangqi (M6, depth 1 vs 2)
* ``sample-alpha-beta``       -- alpha-beta self-play with depth/nodes/eval per move
* ``sample-perpetual-check``  -- scripted 8-ply perpetual check
* ``sample-move-cap-draw``    -- random vs random, 300 plies, draw by the cap
* ``sample-greedy-vs-random`` -- a long greedy game with zero depth/nodes/eval
"""

from __future__ import annotations

import json
from pathlib import Path

from bots.base import Bot, Budget, SearchResult
from engine.board import Board
from harness.match import MatchJob, play_game, play_job, save_game

# Games copied from the M6 depth-scaling run, with the id they get as samples.
M6_RUN_GAMES = Path("experiments/results/depth-scaling-20261123-053517/games")
M6_SAMPLES = {
    "ds-d1v2-025.json": "sample-checkmate",  # 9 plies, checkmate
    "ds-d1v2-000.json": "sample-stalemate",  # 44 plies, stalemate
    "ds-d2v3-023.json": "sample-alpha-beta",  # 54 plies, alpha-beta 3 vs 2
}


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


def copy_m6_samples(out: Path) -> None:
    """Copy selected M6 games into ``out`` under their sample ids."""
    if not M6_RUN_GAMES.is_dir():
        print(
            f"warning: {M6_RUN_GAMES} is missing; alpha-beta samples left as committed. "
            "Regenerate it with python -m experiments.depth_scaling --depths 1,2,3,4 --games 50"
        )
        return
    for source_name, sample_id in M6_SAMPLES.items():
        source = M6_RUN_GAMES / source_name
        if not source.is_file():
            print(f"warning: {source} is missing; skipped")
            continue
        game = json.loads(source.read_text(encoding="utf-8"))
        game["id"] = sample_id
        (out / f"{sample_id}.json").write_text(json.dumps(game, indent=2) + "\n", encoding="utf-8")
        print(f"copied {source_name} -> {sample_id}.json")


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
                game_id="sample-move-cap-draw",
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

    copy_m6_samples(out)
    print(f"wrote samples to {out}")


if __name__ == "__main__":
    main()
