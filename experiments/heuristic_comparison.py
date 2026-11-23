"""Heuristic comparison: the four evaluators round-robin, plus equal-time rows.

Part 1 -- **fixed depth**. Alpha-beta at ``--depth`` with each of the four evaluators,
full round-robin (six pairings), colours swapped, with only a generous safety time cap
(``--safety-ms``). Depth is the fixed variable, so a difference is attributable to the
evaluator rather than to the search reaching a different depth.

Part 2 -- **equal time**. Two extra rows at ``--time-ms`` per move:

* row A: MCTS vs alpha-beta (both ``full``), which is the comparison the proposal asks
  for and the only fair way to compare a depth-bounded and an iteration-bounded bot;
* row B: alpha-beta ``full`` vs alpha-beta ``material``, to see whether the richer
  evaluator pays for the extra evaluation cost.

Command (from the repository root)::

    python -m experiments.heuristic_comparison --games 10 --workers 8
"""

from __future__ import annotations

import argparse
import itertools
import time

from experiments import common
from harness.match import MatchJob

EXPERIMENT = "heuristic_comparison"
OPENING_PLIES = 6
EVALUATORS = ("material", "material_pst", "material_pst_mobility", "full")
# "Very large" iteration cap for the MCTS row: the time budget is meant to decide.
MCTS_ITERATION_CAP = 1_000_000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate the command line, including ``--quick`` overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_common_arguments(parser)
    parser.add_argument("--depth", type=int, default=3, help="fixed depth for part 1")
    parser.add_argument(
        "--safety-ms", type=float, default=5000.0, help="per-move safety cap in part 1"
    )
    parser.add_argument(
        "--time-ms", type=float, default=750.0, help="per-move budget for rows A and B"
    )
    parser.add_argument(
        "--mcts-exploration", type=float, default=1.4, help="UCT constant for row A"
    )
    parser.add_argument(
        "--skip-time-rows",
        action="store_true",
        help="skip rows A and B (fixed-depth round-robin only)",
    )
    args = parser.parse_args(argv)
    common.validate_common(parser, args)

    if args.quick:
        args.games = 2
        args.depth = 1
        args.safety_ms = 1000.0
        args.time_ms = 100.0

    if args.depth < 1:
        parser.error("--depth must be at least 1")
    if args.safety_ms <= 0 or args.time_ms <= 0:
        parser.error("--safety-ms and --time-ms must be positive")
    if not 0 <= args.mcts_exploration <= 10:
        parser.error("--mcts-exploration must be between 0 and 10")
    return args


def fixed_depth_spec(evaluator: str, depth: int, safety_ms: float) -> dict:
    """Alpha-beta at a fixed depth with only a safety cap."""
    return {
        "type": "alphabeta",
        "params": {"depth": depth, "time_limit_ms": safety_ms, "evaluator": evaluator},
    }


def timed_alphabeta_spec(evaluator: str, time_ms: float) -> dict:
    """Alpha-beta with iterative deepening; the time budget decides the depth."""
    return {
        "type": "alphabeta",
        "params": {"depth": None, "time_limit_ms": time_ms, "evaluator": evaluator},
    }


def timed_mcts_spec(time_ms: float, exploration: float) -> dict:
    """MCTS bounded by time, with an iteration cap far above what fits."""
    return {
        "type": "mcts",
        "params": {
            "iterations": MCTS_ITERATION_CAP,
            "time_limit_ms": time_ms,
            "exploration": exploration,
            "evaluator": "full",
        },
    }


def build_jobs(
    spec_a: dict,
    spec_b: dict,
    pairing: str,
    games: int,
    seed: int,
    opening_plies: int,
    move_time_limit_ms: float | None,
) -> list[MatchJob]:
    """Games for one pairing: A is Red in even games, B in odd ones."""
    jobs = []
    for index in range(games):
        a_is_red = index % 2 == 0
        jobs.append(
            MatchJob(
                red=spec_a if a_is_red else spec_b,
                black=spec_b if a_is_red else spec_a,
                seed=seed + index // 2,
                game_id=f"hc-{pairing.replace(' ', '_')}-{index:03d}",
                opening_plies=opening_plies,
                move_time_limit_ms=move_time_limit_ms,
            )
        )
    return jobs


def play_pairing(
    args,
    run_paths: common.RunPaths,
    games_csv: common.CsvAppender,
    spec_a: dict,
    spec_b: dict,
    pairing: str,
    *,
    move_time_limit_ms: float | None,
    cap_ms: float | None,
    configured_depth: int | None,
):
    """Play one pairing and return ``(W, D, L, totals_a, totals_b)`` for side A."""
    jobs = build_jobs(
        spec_a, spec_b, pairing, args.games, args.seed, OPENING_PLIES, move_time_limit_ms
    )
    wins = draws = losses = 0
    totals_a = common.SideTotals()
    totals_b = common.SideTotals()

    def on_result(record) -> None:
        nonlocal wins, draws, losses
        a_is_red = common.same_spec(record.red, spec_a)
        red_stats = common.side_stats(
            record, True, cap_ms=cap_ms, configured_depth=configured_depth
        )
        black_stats = common.side_stats(
            record, False, cap_ms=cap_ms, configured_depth=configured_depth
        )
        games_csv.write(
            common.game_row(run_paths, EXPERIMENT, pairing, record, red_stats, black_stats)
        )
        common.save_record(record, run_paths)

        outcome = common.outcome_for(record, a_is_red)
        if outcome > 0:
            wins += 1
        elif outcome < 0:
            losses += 1
        else:
            draws += 1

        totals_a.add(red_stats if a_is_red else black_stats)
        totals_b.add(black_stats if a_is_red else red_stats)

    print(f"{pairing}: {args.games} games ...", flush=True)
    common.run_streaming(jobs, args.workers, on_result)
    return wins, draws, losses, totals_a, totals_b


def run(args: argparse.Namespace) -> None:
    """Run the round-robin, rows A and B, write CSVs, then summarise and plot."""
    run_paths = common.new_run("heuristic-comparison", args.out)
    summary_rows: list[dict] = []
    matrix: dict[tuple[str, str], tuple[int, int, int]] = {}
    evaluator_cost: dict[str, float] = {}

    with (
        common.CsvAppender(run_paths.games_csv, common.GAME_FIELDS) as games_csv,
        common.CsvAppender(run_paths.summary_csv, common.SUMMARY_FIELDS) as summary_csv,
    ):
        # Part 1: fixed-depth round-robin.
        for first, second in itertools.combinations(EVALUATORS, 2):
            pairing = f"{first} vs {second}"
            wins, draws, losses, totals_first, totals_second = play_pairing(
                args,
                run_paths,
                games_csv,
                fixed_depth_spec(first, args.depth, args.safety_ms),
                fixed_depth_spec(second, args.depth, args.safety_ms),
                pairing,
                move_time_limit_ms=args.safety_ms,
                cap_ms=args.safety_ms,
                configured_depth=args.depth,
            )
            matrix[(first, second)] = (wins, draws, losses)
            matrix[(second, first)] = (losses, draws, wins)
            row = common.summary_row(
                run_paths,
                EXPERIMENT,
                pairing,
                first,
                wins,
                draws,
                losses,
                totals_first,
                totals_second,
                f"{first} (depth {args.depth})",
                f"{second} (depth {args.depth})",
                f"fixed depth, safety cap {args.safety_ms:g} ms; 10 games gives wide intervals",
            )
            summary_csv.write(row)
            summary_rows.append(row)

        if not args.skip_time_rows:
            # Row A: MCTS vs alpha-beta at equal time per move.
            alpha_spec_a = timed_alphabeta_spec("full", args.time_ms)
            mcts_spec = timed_mcts_spec(args.time_ms, args.mcts_exploration)
            wins, draws, losses, totals_alpha, totals_mcts = play_pairing(
                args,
                run_paths,
                games_csv,
                alpha_spec_a,
                mcts_spec,
                f"alphabeta vs mcts @{args.time_ms:g}ms",
                move_time_limit_ms=args.time_ms,
                cap_ms=None,
                configured_depth=None,
            )
            row = common.summary_row(
                run_paths,
                EXPERIMENT,
                f"alphabeta vs mcts @{args.time_ms:g}ms",
                "alphabeta (full)",
                wins,
                draws,
                losses,
                totals_alpha,
                totals_mcts,
                f"alphabeta(full) @{args.time_ms:g} ms",
                f"mcts(full, c={args.mcts_exploration:g}) @{args.time_ms:g} ms",
                "row A: equal time per move. MCTS depth is its most-visited line; "
                "MCTS nodes are iterations",
            )
            summary_csv.write(row)
            summary_rows.append(row)

            # Row B: full vs material, both alpha-beta, same time per move.
            wins, draws, losses, totals_full, totals_material = play_pairing(
                args,
                run_paths,
                games_csv,
                timed_alphabeta_spec("full", args.time_ms),
                timed_alphabeta_spec("material", args.time_ms),
                f"full vs material @{args.time_ms:g}ms",
                move_time_limit_ms=args.time_ms,
                cap_ms=None,
                configured_depth=None,
            )
            row = common.summary_row(
                run_paths,
                EXPERIMENT,
                f"full vs material @{args.time_ms:g}ms",
                "full",
                wins,
                draws,
                losses,
                totals_full,
                totals_material,
                f"full @{args.time_ms:g} ms",
                f"material @{args.time_ms:g} ms",
                "row B: equal time per move, iterative deepening on both sides",
            )
            summary_csv.write(row)
            summary_rows.append(row)

    evaluator_cost = _evaluator_costs()
    _print_report(summary_rows, matrix, evaluator_cost, args)
    _plot(run_paths, summary_rows, matrix, evaluator_cost)


def _evaluator_costs() -> dict[str, float]:
    """Microseconds per evaluation for each evaluator, for the cost plot."""
    from engine.board import Board
    from eval.evaluator import build_evaluator

    board = Board.start()
    samples = 1000
    costs = {}
    for name in EVALUATORS:
        evaluator = build_evaluator(name)
        started = time.perf_counter()
        for _ in range(samples):
            evaluator(board)
        costs[name] = (time.perf_counter() - started) / samples * 1e6
    return costs


def _print_report(summary_rows, matrix, evaluator_cost, args) -> None:
    print("\nHeuristic comparison")
    print(
        f"{'pairing':<42}{'W':>4}{'D':>4}{'L':>4}{'score':>7}{'interval':>14}{'depth':>7}{'ms':>8}"
    )
    print("-" * 90)
    for row in summary_rows:
        print(
            f"{row['pairing']:<42}{row['wins']:>4}{row['draws']:>4}{row['losses']:>4}"
            f"{row['score']:>7.2f}{row['wilson_low']:>7.2f}-{row['wilson_high']:<6.2f}"
            f"{row['a_avg_depth']:>7.2f}{row['a_avg_ms']:>8.1f}"
        )
    print("\nScore matrix (row = first evaluator, fixed depth)")
    print(f"{'':<24}" + "".join(f"{name:<22}" for name in EVALUATORS))
    for first in EVALUATORS:
        cells = []
        for second in EVALUATORS:
            if first == second:
                cells.append(f"{'-':<22}")
            else:
                wins, draws, losses = matrix[(first, second)]
                games = wins + draws + losses
                score = (wins + 0.5 * draws) / games if games else 0.0
                cells.append(f"{score:<7.2f} {wins}-{draws}-{losses}".ljust(22))
        print(f"{first:<24}" + "".join(cells))
    print(
        "\nEvaluation cost (us per call): "
        + ", ".join(f"{name} {cost:.2f}" for name, cost in evaluator_cost.items())
    )
    print("Note: 10 games gives wide intervals; this is not a ranking.")


def _plot(run_paths, summary_rows, matrix, evaluator_cost) -> None:
    plt = common.import_pyplot()
    if plt is None:
        return

    # Score matrix: rows and columns in the fixed evaluator order.
    grid = [[0.0] * len(EVALUATORS) for _ in EVALUATORS]
    for row_index, first in enumerate(EVALUATORS):
        for column_index, second in enumerate(EVALUATORS):
            if first == second:
                grid[row_index][column_index] = 0.5
                continue
            wins, draws, losses = matrix.get((first, second), (0, 0, 0))
            games = wins + draws + losses
            grid[row_index][column_index] = (wins + 0.5 * draws) / games if games else 0.0

    figure, axes = plt.subplots(1, 3, figsize=(16, 4.4))
    image = axes[0].imshow(grid, vmin=0.0, vmax=1.0, cmap="RdYlGn")
    axes[0].set_xticks(range(len(EVALUATORS)))
    axes[0].set_xticklabels(EVALUATORS, rotation=30, ha="right", fontsize=8)
    axes[0].set_yticks(range(len(EVALUATORS)))
    axes[0].set_yticklabels(EVALUATORS, fontsize=8)
    axes[0].set_title("Score matrix (row vs column)")
    for row_index in range(len(EVALUATORS)):
        for column_index in range(len(EVALUATORS)):
            axes[0].text(
                column_index,
                row_index,
                f"{grid[row_index][column_index]:.2f}",
                ha="center",
                va="center",
                fontsize=8,
            )
    figure.colorbar(image, ax=axes[0], fraction=0.046, pad=0.04)

    axes[1].bar(
        range(len(EVALUATORS)), [evaluator_cost[name] for name in EVALUATORS], color="#4c72b0"
    )
    axes[1].set_yscale("log")
    axes[1].set_xticks(range(len(EVALUATORS)))
    axes[1].set_xticklabels(EVALUATORS, rotation=30, ha="right", fontsize=8)
    axes[1].set_ylabel("us per evaluation (log)")
    axes[1].set_title("Evaluation cost")

    time_rows = [row for row in summary_rows if "@" in row["pairing"]]
    labels = [row["pairing"] for row in time_rows]
    scores = [row["score"] for row in time_rows]
    errors = [
        [row["score"] - row["wilson_low"] for row in time_rows],
        [row["wilson_high"] - row["score"] for row in time_rows],
    ]
    axes[2].bar(range(len(scores)), scores, yerr=errors, capsize=5, color="#dd8452")
    axes[2].set_ylim(0, 1)
    axes[2].set_xticks(range(len(scores)))
    axes[2].set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    axes[2].set_title("Equal-time rows (score of the first side)")

    figure.suptitle("Heuristic comparison (10 games per pairing: wide intervals)")
    figure.tight_layout()
    path = run_paths.directory / f"{run_paths.run_id}-heuristic-comparison.png"
    figure.savefig(path, dpi=130)
    plt.close(figure)
    print(f"plot: {path}")


def main(argv: list[str] | None = None) -> None:
    """Entry point; keeps the process pool under the __main__ guard."""
    args = parse_args(argv)
    print(
        f"heuristic comparison: depth {args.depth}, safety {args.safety_ms:g} ms, "
        f"{args.games} games per pairing, seed {args.seed}, {args.workers} workers"
    )
    run(args)


if __name__ == "__main__":
    main()
