"""Named evaluators: turn a position into a score from the side to move's view.

Each evaluator is a weighted sum of features, all of which already use the same sign
convention (positive is good for the side to move, units are centipawns). The names
in ``EVALUATORS`` are the closed enum the server registry validates against:

* ``material`` -- piece values only;
* ``material_pst`` -- plus piece-square tables;
* ``material_pst_mobility`` -- plus mobility;
* ``full`` -- plus king safety.

Two guarantees keep the positional terms from swamping material:

* every positional feature is **clamped** to a named centipawn budget, so the three
  of them together can never add up to a chariot (PST 150 + mobility 200 +
  king safety 150 = 500 < 900);
* ``MAX_EVALUATION_MAGNITUDE`` (a full army plus those budgets) plus the 300-ply
  move cap stays far below the search's mate score, so a mate always outranks any
  heuristic value. The test in ``tests/test_eval.py`` checks that inequality and
  prints both numbers if it ever fails.

``DEFAULT_WEIGHTS`` is the configuration the experiments vary. Keep the weights
integers: with ``round()`` at the end, integer weights give the exact-negatives
symmetry the acceptance tests require.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.board import Board
from engine.types import Color
from eval.king_safety import king_safety_balance
from eval.material import MAX_ARMY_VALUE, material_balance
from eval.mobility import mobility_balance
from eval.pst import pst_balance

# An evaluator takes a board and returns centipawns for the side to move.
Evaluator = Callable[[Board], int]

# Side-to-move bonus. Off by default: the start position must evaluate to exactly 0,
# and a tempo bonus would break that. Named so an experiment can switch it on.
TEMPO_BONUS = 0

# One function per feature, each already signed for the side to move.
FEATURES: dict[str, Evaluator] = {
    "material": material_balance,
    "pst": pst_balance,
    "mobility": mobility_balance,
    "king_safety": king_safety_balance,
}

# Weight per feature. Material and PST are already centipawns, so weight 1 means
# "use the value as is". Mobility is a *move count*: one extra pseudo-legal move is
# worth 2 centipawns. King safety is a sum of its own centipawn constants, so weight
# 1 uses it as is.
DEFAULT_WEIGHTS: dict[str, int] = {
    "material": 1,
    "pst": 1,
    "mobility": 2,
    "king_safety": 1,
}

# Centipawn budget per positional feature, applied *after* weighting. This is what
# makes the "no combination of positional terms outweighs a chariot" property true
# instead of merely usual: 150 + 200 + 150 = 500 < 900.
POSITIONAL_BUDGET: dict[str, int] = {
    "pst": 150,
    "mobility": 200,
    "king_safety": 150,
}
POSITIONAL_FEATURES: tuple[str, ...] = tuple(POSITIONAL_BUDGET)

# Largest positional total any position can score.
MAX_POSITIONAL_MAGNITUDE = sum(POSITIONAL_BUDGET.values())

# Largest evaluation magnitude: one side with every piece (soldiers across) against
# a bare opponent, plus the positional budget. With the default weights of 1 this is
# 5,300 + 500 = 5,800 centipawns, which leaves room for the 300-ply mate-distance
# adjustment below MATE_SCORE = 100,000.
MAX_EVALUATION_MAGNITUDE = MAX_ARMY_VALUE + MAX_POSITIONAL_MAGNITUDE

# Which features each named evaluator uses, in the order M6's experiment compares
# them: each step adds exactly one feature to the previous one.
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "material": ("material",),
    "material_pst": ("material", "pst"),
    "material_pst_mobility": ("material", "pst", "mobility"),
    "full": ("material", "pst", "mobility", "king_safety"),
}


def _merge_weights(weights: dict[str, int] | None) -> dict[str, int]:
    """Override the default weights, rejecting names that are not features."""
    merged = {**DEFAULT_WEIGHTS, **(weights or {})}
    unknown = set(merged) - set(FEATURES)
    if unknown:
        raise KeyError(f"unknown evaluation feature(s): {', '.join(sorted(unknown))}")
    return merged


def make_evaluator(features: tuple[str, ...], weights: dict[str, int] | None = None) -> Evaluator:
    """Build an evaluator that sums ``features`` with the given weights.

    ``weights`` overrides ``DEFAULT_WEIGHTS`` for the features it names. Positional
    contributions are clamped to ``POSITIONAL_BUDGET`` after weighting, so even a
    deliberately huge weight cannot drown out material.
    """
    merged = _merge_weights(weights)
    configured = [(name, merged[name]) for name in features]

    def evaluate(board: Board) -> int:
        total = TEMPO_BONUS
        for name, weight in configured:
            value = weight * FEATURES[name](board)
            budget = POSITIONAL_BUDGET.get(name)
            if budget is not None:
                # Clamp, so the positional total is bounded by construction.
                if value > budget:
                    value = budget
                elif value < -budget:
                    value = -budget
            total += value
        return round(total)

    evaluate.__doc__ = "Weighted sum of: " + ", ".join(
        f"{name}*{weight}" for name, weight in configured
    )
    return evaluate


EVALUATORS: dict[str, Evaluator] = {
    name: make_evaluator(features) for name, features in FEATURE_SETS.items()
}


def available_evaluators() -> tuple[str, ...]:
    """The evaluator names this build understands, in a stable order."""
    return tuple(EVALUATORS)


def build_evaluator(name: str, weights: dict[str, int] | None = None) -> Evaluator:
    """Look up an evaluator by name, raising ``ValueError`` for unknown names."""
    try:
        features = FEATURE_SETS[name]
    except KeyError:
        known = ", ".join(available_evaluators())
        raise ValueError(f"unknown evaluator {name!r}; known evaluators: {known}") from None
    return make_evaluator(features, weights)


def evaluate_from_red(board: Board, evaluator: Evaluator | str) -> int:
    """Score from **Red's** point of view, whoever is to move.

    Evaluators are side-to-move relative, so a score with Black to move is negated.
    This absolute view is what a UI or a report wants: the same position always gives
    the same number, and a position that is good for Red is positive no matter whose
    turn it is. ``evaluator`` may be a name or an already-built evaluator.
    """
    function = build_evaluator(evaluator) if isinstance(evaluator, str) else evaluator
    score = function(board)
    return score if board.side_to_move is Color.RED else -score


def positional_contributions(board: Board, weights: dict[str, int] | None = None) -> dict[str, int]:
    """The *unclamped* weighted contribution of each positional feature.

    Diagnostics only: ``tools/clamp_stats.py`` uses it to report how often a clamp
    would trigger. Not used by the search.
    """
    merged = _merge_weights(weights)
    return {name: merged[name] * FEATURES[name](board) for name in POSITIONAL_FEATURES}
