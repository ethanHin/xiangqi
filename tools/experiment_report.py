"""Read a finished experiment run and print the tables needed for the report.

The run's own CSVs hold the raw numbers. This tool adds the two derived views the
report quotes but the scripts do not compute:

* **opening-clustered Wilson interval** -- every opening is played twice (once per
  colour) and fixed-depth games are deterministic, so the two results are not
  independent samples. Each opening pair is scored as one unit and the interval is
  computed over ``games / 2`` openings.
* **same-game node and time ratios** between the two sides of a pairing, for the
  depth-scaling experiment (marginal averages across different pairings would mix
  different games).

Usage, from the repository root::

    python -m tools.experiment_report --run experiments/results/depth-scaling-20261123-053517
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

Z_95 = 1.96


def wilson(score: float, games: int) -> tuple[float, float]:
    """Approximate 95% Wilson interval for a proportion."""
    if games <= 0:
        return (0.0, 1.0)
    denominator = 1.0 + Z_95 * Z_95 / games
    centre = (score + Z_95 * Z_95 / (2 * games)) / denominator
    half = (
        Z_95
        * math.sqrt(score * (1 - score) / games + Z_95 * Z_95 / (4 * games * games))
        / denominator
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def parse_spec(label: str) -> dict:
    """Turn ``alphabeta{"depth":2,...}`` back into a type and a params dict."""
    type_name, _, params_text = label.partition("{")
    params = json.loads("{" + params_text) if params_text else {}
    return {"type": type_name, "params": params}


def side_a_is_red(row: dict, experiment: str, pairing: str) -> bool:
    """Whether the pairing's 'side A' was Red in this game."""
    red = parse_spec(row["red"])
    black = parse_spec(row["black"])
    if experiment == "depth_scaling":
        red_depth = red["params"].get("depth") or 0
        black_depth = black["params"].get("depth") or 0
        return red_depth >= black_depth
    first = pairing.split(" vs ")[0].strip()

    def matches(spec: dict) -> bool:
        if first == "alphabeta":
            return spec["type"] == "alphabeta"
        return spec["type"] == "alphabeta" and spec["params"].get("evaluator") == first

    return matches(red)


def outcome_for(row: dict, is_red: bool) -> float:
    """1.0 win, 0.5 draw, 0.0 loss for the side selected by ``is_red``."""
    if row["result"] == "draw":
        return 0.5
    red_won = row["result"] == "red_win"
    return 1.0 if red_won == is_red else 0.0


def totals_by_side(rows: list[dict], by_depth: bool) -> dict:
    """Total moves, nodes and ms per side, keyed by depth when ``by_depth``."""
    totals: dict = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row in rows:
        for side in ("red", "black"):
            moves = int(row[side + "_moves"])
            if by_depth:
                key = round(float(row[side + "_avg_depth"]))
            else:
                key = side
            bucket = totals[key]
            bucket[0] += moves
            bucket[1] += float(row[side + "_avg_nodes"]) * moves
            bucket[2] += float(row[side + "_avg_ms"]) * moves
    return totals


def short_label(spec: dict) -> str:
    """Compact description of a player spec for the per-side table."""
    params = spec["params"]
    bits = []
    if params.get("depth") is not None:
        bits.append(f"d={params['depth']}")
    if params.get("evaluator"):
        bits.append(str(params["evaluator"]))
    if params.get("time_limit_ms") is not None:
        bits.append(f"t={params['time_limit_ms']:.0f}")
    if params.get("iterations") is not None:
        bits.append(f"it={params['iterations']}")
    if spec["type"] == "mcts":
        bits.append(f"c={params.get('exploration')}")
    return f"{spec['type']}({', '.join(bits)})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="path to a run folder")
    args = parser.parse_args()

    run_dir = Path(args.run)
    games_csv = next(run_dir.glob("*-games.csv"))
    with games_csv.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"no games in {games_csv}")

    experiment = rows[0]["experiment"]
    print(f"run {rows[0]['run_id']}  experiment {experiment}  games {len(rows)}")

    by_pairing: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_pairing[row["pairing"]].append(row)

    print(
        f"\n{'pairing':<42}{'games':>6}{'seeds':>7}{'W':>4}{'D':>4}{'L':>4}"
        f"{'score':>7}{'95% games':>16}{'95% openings':>16}{'cap':>5}"
    )
    print("-" * 111)
    for pairing, pairing_rows in by_pairing.items():
        outcomes = []
        by_seed: dict[int, list[float]] = defaultdict(list)
        cap_hits = 0
        for row in pairing_rows:
            is_red = side_a_is_red(row, experiment, pairing)
            outcome = outcome_for(row, is_red)
            outcomes.append(outcome)
            by_seed[int(row["seed"])].append(outcome)
            cap_hits += int(row["red_cap_hits"]) + int(row["black_cap_hits"])
        wins = sum(1 for value in outcomes if value == 1.0)
        draws = sum(1 for value in outcomes if value == 0.5)
        losses = sum(1 for value in outcomes if value == 0.0)
        games = len(outcomes)
        score = sum(outcomes) / games
        low, high = wilson(score, games)
        pair_means = [sum(values) / len(values) for values in by_seed.values()]
        clustered = sum(pair_means) / len(pair_means)
        clustered_low, clustered_high = wilson(clustered, len(pair_means))
        print(
            f"{pairing:<42}{games:>6}{len(by_seed):>7}{wins:>4}{draws:>4}{losses:>4}"
            f"{score:>7.2f}{f'{low:.2f}-{high:.2f}':>16}"
            f"{f'{clustered_low:.2f}-{clustered_high:.2f}':>16}{cap_hits:>5}"
        )

    if experiment == "depth_scaling":
        print("\nSame-game ratios between the two depths of each pairing")
        header = (
            f"{'pairing':<24}{'depth':>7}{'moves':>8}{'nodes':>13}"
            f"{'ms':>11}{'node ratio':>12}{'time ratio':>12}"
        )
        print(header)
        print("-" * len(header))
        for pairing, pairing_rows in by_pairing.items():
            totals = totals_by_side(pairing_rows, by_depth=True)
            depths = sorted(totals)
            shallow, deep = depths[0], depths[-1]
            shallow_nodes = totals[shallow][1]
            deep_nodes = totals[deep][1]
            shallow_ms = totals[shallow][2]
            deep_ms = totals[deep][2]
            for depth in depths:
                moves, nodes, ms = totals[depth]
                ratio_nodes = f"{deep_nodes / shallow_nodes:.2f}x" if depth == deep else ""
                ratio_ms = f"{deep_ms / shallow_ms:.2f}x" if depth == deep else ""
                print(
                    f"{pairing:<24}{depth:>7}{moves:>8.0f}{nodes:>13,.0f}{ms:>11,.0f}"
                    f"{ratio_nodes:>12}{ratio_ms:>12}"
                )

    print("\nPer-side totals")
    print(f"{'pairing':<42}{'side':>30}{'moves':>8}{'nodes/move':>12}{'ms/move':>10}")
    print("-" * 102)
    for pairing, pairing_rows in by_pairing.items():
        for side in ("red", "black"):
            moves = sum(int(row[side + "_moves"]) for row in pairing_rows)
            nodes = sum(
                float(row[side + "_avg_nodes"]) * int(row[side + "_moves"]) for row in pairing_rows
            )
            ms = sum(
                float(row[side + "_avg_ms"]) * int(row[side + "_moves"]) for row in pairing_rows
            )
            label = short_label(parse_spec(pairing_rows[0][side]))
            print(
                f"{pairing:<42}{side + ': ' + label:>30}{moves:>8}"
                f"{nodes / moves:>12,.1f}{ms / moves:>10.1f}"
            )


if __name__ == "__main__":
    main()
