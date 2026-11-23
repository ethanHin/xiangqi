"""Named evaluators: turn a position into a score from the side to move's view.

M3 only needs ``material``; M4 adds the piece-square, mobility, and king-safety
combinations. Keeping the names behind one factory means the bot params, the
experiments, and (from M8) the server registry all agree on what ``"full"`` means.

Sign convention: positive is good for the side to move (negamax-compatible), and
the units are centipawns (100 = one soldier).
"""

from __future__ import annotations

from collections.abc import Callable

from eval.material import material_balance

# An evaluator takes a board and returns centipawns for the side to move.
Evaluator = Callable[[object], int]

EVALUATORS: dict[str, Evaluator] = {
    "material": material_balance,
}


def available_evaluators() -> tuple[str, ...]:
    """The evaluator names this build understands, in a stable order."""
    return tuple(EVALUATORS)


def build_evaluator(name: str) -> Evaluator:
    """Look up an evaluator by name, raising ``ValueError`` for unknown names."""
    try:
        return EVALUATORS[name]
    except KeyError:
        known = ", ".join(available_evaluators())
        raise ValueError(f"unknown evaluator {name!r}; known evaluators: {known}") from None
