"""Bots: anything that can choose a move for the side to move.

``bots.base`` defines the interface and the two dataclasses everything else
uses. Concrete bots live in their own modules: ``random_bot``, ``greedy_bot``
(M2), then ``alphabeta`` and ``mcts`` (M3, M5). The harness, the experiments,
and the server all build bots through ``bots.base.build_bot``.
"""
