"""How often the positional clamps trigger, on the fixed position sets.

The evaluator clamps each positional feature to a centipawn budget (PST 150,
mobility 200, king safety 150) so that positional terms can never outweigh a
chariot. A clamp that fires constantly would mean the budget is doing the tuning
instead of the table values, so this reports the trigger rate. It measures; it does
not tune anything.

All three features are side-to-move relative, so the contributions with Black to move
are the negatives of those with Red to move; counting one orientation covers both.

Usage, from the repository root::

    python -m tools.clamp_stats
"""

from __future__ import annotations

from engine.board import Board
from eval.evaluator import POSITIONAL_BUDGET, positional_contributions
from tests.helpers import load_fens

POSITION_SETS = (
    ("middlegame", "positions_middlegame.txt"),
    ("transpositions", "positions_transpositions.txt"),
    ("quiet", "positions_quiet.txt"),
)


def main() -> None:
    print(f"feature budgets: {POSITIONAL_BUDGET}")
    print(f"{'set':<16}{'positions':>10}{'feature':>14}{'clamped':>10}{'rate':>9}")
    print("-" * 59)
    for set_name, filename in POSITION_SETS:
        fens = load_fens(filename)
        hits = {feature: 0 for feature in POSITIONAL_BUDGET}
        for fen in fens:
            for feature, value in positional_contributions(Board.from_fen(fen)).items():
                if abs(value) > POSITIONAL_BUDGET[feature]:
                    hits[feature] += 1
        for feature, count in hits.items():
            rate = count / len(fens) if fens else 0.0
            print(f"{set_name:<16}{len(fens):>10}{feature:>14}{count:>10}{rate:>8.1%}")


if __name__ == "__main__":
    main()
