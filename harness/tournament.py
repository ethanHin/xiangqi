"""Tournament: play N games between two bots, swap colours, and aggregate the results.

Every pair of games shares an opening seed and swaps colours, so an even number
of games compares the bots on the same set of positions. Results are reproducible
from ``(bot_a, bot_b, games, seed, opening_plies)``.

Games run in separate processes when ``workers > 1``. That matters twice: the
experiments are CPU-bound (the GIL makes threads useless), and each game is a
pure function of its ``MatchJob``, so the pool needs no shared state. On Windows
the default start method is ``spawn``, which requires:

* ``play_job`` to be a module-level function (it is, in ``harness.match``),
* ``MatchJob`` to hold only picklable values (it does: dicts, ints, strings),
* the pool to be created under ``if __name__ == "__main__"``, which the CLI below
  does.

Run it from the command line::

    python -m harness.tournament --bot-a greedy --bot-b random --games 100 --workers 8
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

from bots.base import BOT_TYPES, build_bot
from harness.match import (
    DEFAULT_MOVE_TIME_LIMIT_MS,
    DEFAULT_OPENING_PLIES,
    GameRecord,
    MatchJob,
    play_job,
    save_game,
)
from harness.results import SideScore, format_summary, score_for, summarize


@dataclass(frozen=True)
class TournamentConfig:
    """How many games to play and under what conditions."""

    games: int = 10
    seed: int = 1
    opening_plies: int = DEFAULT_OPENING_PLIES
    move_time_limit_ms: float = DEFAULT_MOVE_TIME_LIMIT_MS
    workers: int = 1

    def __post_init__(self) -> None:
        if self.games <= 0:
            raise ValueError("games must be positive")
        if self.games % 2:
            raise ValueError("games must be even so both bots get the same number of Red games")
        if not 0 <= self.opening_plies <= 8:
            raise ValueError("opening_plies must be between 0 and 8")


def build_jobs(
    bot_a: dict[str, Any], bot_b: dict[str, Any], config: TournamentConfig
) -> list[MatchJob]:
    """Create one job per game, swapping colours and sharing opening seeds by pair.

    Game 0: A is Red, game 1: B is Red, both with ``seed + 0``. Game 2 and 3 use
    ``seed + 1``, and so on. Sharing the seed inside a pair is what makes the
    colour comparison meaningful.
    """
    jobs: list[MatchJob] = []
    for index in range(config.games):
        red, black = (bot_a, bot_b) if index % 2 == 0 else (bot_b, bot_a)
        seed = config.seed + index // 2
        jobs.append(
            MatchJob(
                red=red,
                black=black,
                seed=seed,
                game_id=f"g{seed}-{index:03d}",
                opening_plies=config.opening_plies,
                move_time_limit_ms=config.move_time_limit_ms,
            )
        )
    return jobs


def run_tournament(jobs: Sequence[MatchJob], workers: int = 1) -> list[GameRecord]:
    """Play every job, in-process for ``workers == 1`` and in a process pool otherwise.

    Results come back in job order, so output is deterministic regardless of the
    number of workers.
    """
    if workers <= 1:
        return [play_job(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(play_job, jobs))


def default_workers(games: int) -> int:
    """Use one process per game, capped by the CPU count."""
    return max(1, min(games, os.cpu_count() or 1))


def run_match(
    bot_a: dict[str, Any],
    bot_b: dict[str, Any],
    config: TournamentConfig,
) -> list[GameRecord]:
    """Convenience wrapper: build the jobs, play them, return the records."""
    return run_tournament(build_jobs(bot_a, bot_b, config), workers=config.workers)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: play a tournament, save the games, print the summary."""
    parser = argparse.ArgumentParser(
        prog="python -m harness.tournament",
        description="Play a bot-vs-bot tournament and report win/draw/loss.",
    )
    parser.add_argument("--bot-a", default="greedy", choices=BOT_TYPES)
    parser.add_argument("--bot-b", default="random", choices=BOT_TYPES)
    parser.add_argument("--games", type=int, default=10, help="even number of games")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="parallel processes (default: one per game, capped at the CPU count)",
    )
    parser.add_argument("--opening-plies", type=int, default=DEFAULT_OPENING_PLIES)
    parser.add_argument("--time-limit-ms", type=float, default=DEFAULT_MOVE_TIME_LIMIT_MS)
    parser.add_argument("--save-dir", default="games", help="directory for game JSON files")
    parser.add_argument("--no-save", action="store_true", help="do not write game files")
    args = parser.parse_args(argv)

    if args.games % 2:
        parser.error("--games must be even so both bots play half their games as Red")
    if not 0 <= args.opening_plies <= 8:
        parser.error("--opening-plies must be between 0 and 8")

    bot_a = {"type": args.bot_a, "params": {}}
    bot_b = {"type": args.bot_b, "params": {}}
    workers = args.workers if args.workers is not None else default_workers(args.games)
    config = TournamentConfig(
        games=args.games,
        seed=args.seed,
        opening_plies=args.opening_plies,
        move_time_limit_ms=args.time_limit_ms,
        workers=workers,
    )

    # Fail on an unknown bot name before starting any game.
    for spec in (bot_a, bot_b):
        build_bot(spec, seed=config.seed)

    jobs = build_jobs(bot_a, bot_b, config)
    records = run_tournament(jobs, workers=workers)

    if not args.no_save:
        for record in records:
            save_game(record, args.save_dir)

    summary = summarize(records)
    scores: list[SideScore] = [score_for(records, args.bot_a)]
    if args.bot_b != args.bot_a:
        scores.append(score_for(records, args.bot_b))
    print(
        format_summary(
            summary,
            scores,
            title=f"{args.bot_a} vs {args.bot_b} ({config.games} games, {workers} workers)",
        )
    )
    if not args.no_save:
        print(f"saved {len(records)} game files to {args.save_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover - spawn guard
    raise SystemExit(main())
