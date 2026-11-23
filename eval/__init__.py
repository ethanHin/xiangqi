"""Evaluation features and the combined evaluator.

Each feature is its own module and returns centipawns from the side to move's point
of view (positive is good for the side to move), so they can be summed:

* ``material``    -- piece values, including the crossed-river soldier bonus;
* ``pst``         -- piece-square tables, mirrored for Black by flipping ranks;
* ``mobility``    -- difference in pseudo-legal move counts;
* ``king_safety`` -- advisors and elephants near the general, exposure penalties.

``evaluator`` combines them with configurable weights and exposes the named
evaluators the bot registry and the experiments use: ``material``,
``material_pst``, ``material_pst_mobility`` and ``full``.
"""
