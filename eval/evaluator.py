"""Named evaluators: turn a position into a score from the side to move's view.

Each evaluator is a weighted sum of features, all of which already use the same sign
convention (positive is good for the side to move, units are centipawns). The names
in ``EVALUATORS`` are the closed enum the server registry validates against:

* ``material`` -- piece values only;
* ``material_pst`` -- plus piece-square tables;
* ``material_pst_mobility`` -- plus mobility;
* ``full`` -- plus king safety.

``DEFAULT_WEIGHTS`` is the configuration the experiments vary. Keep the weights
integers: with ``round()`` at the end, integer weights give the exact-negatives
symmetry the acceptance tests require.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.board import Board
from eval.king_safety import king_safety_balance
from eval.material import material_balance
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

# Weight per feature, in centipawns per unit of the feature. Material and PST are
# already centipawns; mobility is a move count, so one move of freedom is worth 2.
DEFAULT_WEIGHTS: dict[str, int] = {
    "material": 1,
    "pst": 1,
    "mobility": 2,
    "king_safety": 1,
}

# Which features each named evaluator uses, in the order M6's experiment compares
# them: each step adds exactly one feature to the previous one.
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "material": ("material",),
    "material_pst": ("material", "pst"),
    "material_pst_mobility": ("material", "pst", "mobility"),
    "full": ("material", "pst", "mobility", "king_safety"),
}


def make_evaluator(features: tuple[str, ...], weights: dict[str, int] | None = None) -> Evaluator:
    """Build an evaluator that sums ``features`` with the given weights.

    ``weights`` overrides ``DEFAULT_WEIGHTS`` for the features it names; unknown
    names raise ``KeyError`` immediately, which is what a typo should do.
    """
    merged = {**DEFAULT_WEIGHTS, **(weights or {})}
    unknown = set(merged) - set(FEATURES)
    if unknown:
        raise KeyError(f"unknown evaluation feature(s): {', '.join(sorted(unknown))}")
    configured = [(name, merged[name]) for name in features]

    def evaluate(board: Board) -> int:
        total = TEMPO_BONUS
        for name, weight in configured:
            total += weight * FEATURES[name](board)
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
