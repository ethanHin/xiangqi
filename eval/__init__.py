"""Evaluation features and the combined evaluator.

M2 only needs ``material`` (the greedy bot uses the same piece values that M4's
evaluator will weight). The other feature modules -- piece-square tables,
mobility, king safety -- and the weighted ``evaluator`` arrive in M4.
"""
