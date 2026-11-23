"""Depth scaling: alpha-beta at depth d against depth d+1, fixed depth, no clock.

For each consecutive pair in ``--depths`` the script plays ``--games`` games with the
colours swapped, writes one CSV row per game as it finishes, and reports W/D/L from
the deeper side's view plus per-side average depth, nodes and time per move.

Both sides use the ``full`` evaluator and ``time_limit_ms=None``, which makes the
search a pure fixed-depth search (no deadline, no clock reads -- see
``tests/test_alphabeta.py::test_fixed_depth_search_never_checks_the_clock``). That is
what makes depth the independent variable.

Command (from the repository root)::

    python -m experiments.depth_scaling --depths 1,2,3,4 --games 10 --workers 8
"""

from __future__ import annotations

import argparse
from pathlib import Path

from experiments import common
from harness.match import MatchJob

EXPERIMENT = "depth_scaling"
OPENING_PLIES = 6


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate the command line, including ``--quick`` overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    common.add_common_arguments(parser)
    parser.add_argument(
        "--depths",
        default="1,2,3",
        help="comma-separated depths; each consecutive pair is played (default 1,2,3)",
    )
    args = parser.parse_args(argv)
    common.validate_common(parser, args)

    if args.quick:
        args.games = 2
        args.depths = "1,2"

    try:
        depths = [int(part) for part in args.depths.split(",")]
    except ValueError:
        parser.error("--depths must be a comma-separated list of integers")
    if len(depths) < 2:
        parser.error("--depths needs at least two depths (one pairing)")
    if any(depth < 1 for depth in depths):
        parser.error("depths must be at least 1")
    if depths != sorted(set(depths)):
        parser.error("--depths must be strictly increasing")
    args.depths = depths
    return args


def spec_for(depth: int) -> dict:
    """A fixed-depth alpha-beta player spec with the full evaluator and no clock."""
    return {
        "type": "alphabeta",
        "params": {"depth": depth, "time_limit_ms": None, "evaluator": "full"},
    }


def _configured_depth(spec: dict, shallow: int, deep: int) -> int:
    """Which configured depth a side played, from its player spec."""
    return shallow if common.same_spec(spec, spec_for(shallow)) else deep


def build_jobs(
    shallow: int, deep: int, games: int, seed: int, opening_plies: int
) -> list[MatchJob]:
    """Games for one pairing, swapping colours by index and sharing opening seeds."""
    jobs = []
    for index in range(games):
        shallow_is_red = index % 2 == 0
        jobs.append(
            MatchJob(
                red=spec_for(shallow if shallow_is_red else deep),
                black=spec_for(deep if shallow_is_red else shallow),
                seed=seed + index // 2,
                game_id=f"ds-d{shallow}v{deep}-{index:03d}",
                opening_plies=opening_plies,
                move_time_limit_ms=None,
            )
        )
    return jobs


def run(args: argparse.Namespace) -> None:
    """Play every pairing, streaming rows to CSV, then summarise and plot."""
    run_paths = common.new_run("depth-scaling", args.out)
    pairing_rows: list[dict] = []
    mismatches: list[str] = []

    # Per-depth aggregates for the plots (both sides at the same depth share them).
    depth_nodes: dict[int, list[float]] = {depth: [] for depth in args.depths}
    depth_ms: dict[int, list[float]] = {depth: [] for depth in args.depths}
    depth_moves: dict[int, int] = {depth: 0 for depth in args.depths}

    with (
        common.CsvAppender(run_paths.games_csv, common.GAME_FIELDS) as games_csv,
        common.CsvAppender(run_paths.summary_csv, common.SUMMARY_FIELDS) as summary_csv,
    ):
        for shallow, deep in zip(args.depths, args.depths[1:], strict=False):
            pairing = f"depth {shallow} vs {deep}"
            jobs = build_jobs(shallow, deep, args.games, args.seed, OPENING_PLIES)

            wins = draws = losses = 0
            deep_totals = common.SideTotals()
            shallow_totals = common.SideTotals()

            def on_result(
                record,
                pairing=pairing,
                shallow=shallow,
                deep=deep,
                deep_totals=deep_totals,
                shallow_totals=shallow_totals,
            ) -> None:
                nonlocal wins, draws, losses
                red_configured = _configured_depth(record.red, shallow, deep)
                black_configured = _configured_depth(record.black, shallow, deep)
                red_stats = common.side_stats(record, True, configured_depth=red_configured)
                black_stats = common.side_stats(record, False, configured_depth=black_configured)
                games_csv.write(
                    common.game_row(run_paths, EXPERIMENT, pairing, record, red_stats, black_stats)
                )
                common.save_record(record, run_paths)

                deep_is_red = common.same_spec(record.red, spec_for(deep))
                outcome = common.outcome_for(record, deep_is_red)
                if outcome > 0:
                    wins += 1
                elif outcome < 0:
                    losses += 1
                else:
                    draws += 1

                deep_stats = red_stats if deep_is_red else black_stats
                shallow_stats = black_stats if deep_is_red else red_stats
                deep_totals.add(deep_stats)
                shallow_totals.add(shallow_stats)

                for depth, stats in ((deep, deep_stats), (shallow, shallow_stats)):
                    depth_nodes[depth].append(stats.avg_nodes)
                    depth_ms[depth].append(stats.avg_ms)
                    depth_moves[depth] += stats.moves

                for label, stats, configured in (
                    ("red", red_stats, red_configured),
                    ("black", black_stats, black_configured),
                ):
                    if stats.depth_short:
                        mismatches.append(
                            f"{record.id}: {label} reported depth below {configured} "
                            f"on {stats.depth_short} move(s) without a mate score"
                        )

            print(f"{pairing}: {args.games} games, {args.workers} workers ...", flush=True)
            common.run_streaming(jobs, args.workers, on_result)

            row = common.summary_row(
                run_paths,
                EXPERIMENT,
                pairing,
                f"deeper side (depth {deep})",
                wins,
                draws,
                losses,
                deep_totals,
                shallow_totals,
                f"depth {deep}",
                f"depth {shallow}",
                "10 games gives wide intervals" if args.games <= 10 else "",
            )
            summary_csv.write(row)
            pairing_rows.append(row)

    _print_report(pairing_rows, depth_nodes, depth_ms, depth_moves, args)
    _plot(run_paths, pairing_rows, depth_nodes, depth_ms, args)

    if mismatches:
        raise AssertionError(
            "depth-scaling check failed: these moves reported a depth below the "
            "configured one without a proven mate:\n  " + "\n  ".join(mismatches[:10])
        )


def _print_report(pairing_rows, depth_nodes, depth_ms, depth_moves, args) -> None:
    print("\nDepth scaling (W/D/L from the deeper side's view)")
    print(f"{'pairing':<22}{'W':>4}{'D':>4}{'L':>4}{'score':>8}{'95% interval':>18}")
    print("-" * 60)
    for row in pairing_rows:
        interval = f"{row['wilson_low']:.2f}-{row['wilson_high']:.2f}"
        print(
            f"{row['pairing']:<22}{row['wins']:>4}{row['draws']:>4}{row['losses']:>4}"
            f"{row['score']:>8.2f}{interval:>18}"
        )
    print("\nBy depth (averages per move, both sides)")
    print(f"{'depth':<8}{'moves':>8}{'nodes':>14}{'ms':>10}")
    print("-" * 40)
    for depth in args.depths:
        nodes = sum(depth_nodes[depth]) / len(depth_nodes[depth]) if depth_nodes[depth] else 0.0
        ms = sum(depth_ms[depth]) / len(depth_ms[depth]) if depth_ms[depth] else 0.0
        print(f"{depth:<8}{depth_moves[depth]:>8}{nodes:>14,.0f}{ms:>10.1f}")
    print(f"\nCSV: {Path(args.out)}/<run>/...")
    print(
        f"Note: {args.games} games per pairing; intervals are wide and fixed-depth "
        "games from one opening are correlated."
    )


def _plot(run_paths, pairing_rows, depth_nodes, depth_ms, args) -> None:
    plt = common.import_pyplot()
    if plt is None:
        return
    depths = args.depths
    nodes_avg = [
        sum(depth_nodes[d]) / len(depth_nodes[d]) if depth_nodes[d] else 0.0 for d in depths
    ]
    ms_avg = [sum(depth_ms[d]) / len(depth_ms[d]) if depth_ms[d] else 0.0 for d in depths]

    figure, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    scores = [row["score"] for row in pairing_rows]
    errors = [
        [row["score"] - row["wilson_low"] for row in pairing_rows],
        [row["wilson_high"] - row["score"] for row in pairing_rows],
    ]
    axes[0].bar(range(len(scores)), scores, yerr=errors, capsize=5, color="#4c72b0")
    axes[0].set_ylim(0, 1)
    axes[0].set_ylabel("score of the deeper side")
    axes[0].set_xticks(range(len(scores)))
    axes[0].set_xticklabels([row["pairing"].replace("depth ", "d") for row in pairing_rows])
    axes[0].set_title("Score per pairing (95% Wilson)")

    axes[1].plot(depths, nodes_avg, marker="o", color="#dd8452")
    axes[1].set_yscale("log")
    axes[1].set_xlabel("configured depth")
    axes[1].set_ylabel("average nodes per move (log)")
    axes[1].set_title("Nodes by depth")

    axes[2].plot(depths, ms_avg, marker="o", color="#55a868")
    axes[2].set_yscale("log")
    axes[2].set_xlabel("configured depth")
    axes[2].set_ylabel("average ms per move (log)")
    axes[2].set_title("Time by depth")

    figure.suptitle("Depth scaling (alpha-beta, full evaluator, fixed depth)")
    figure.tight_layout()
    path = run_paths.directory / f"{run_paths.run_id}-depth-scaling.png"
    figure.savefig(path, dpi=130)
    plt.close(figure)
    print(f"plot: {path}")


def main(argv: list[str] | None = None) -> None:
    """Entry point; keeps the process pool under the __main__ guard."""
    args = parse_args(argv)
    print(
        f"depth scaling: depths {args.depths}, {args.games} games per pairing, "
        f"seed {args.seed}, {args.workers} workers, output {args.out}"
    )
    run(args)


if __name__ == "__main__":
    main()
