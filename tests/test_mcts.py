"""Tests for the M5 MCTS bot.

The two tests that matter most are the hand-built tree (the value convention) and
the mate-in-one (a wrong sign makes the bot avoid winning). The rest pin down the
budget, the board restoration, and the registry surface.
"""

from __future__ import annotations

import random
import time

import pytest

from bots.base import BOT_DEFAULTS, Budget, build_bot
from bots.mcts import (
    EVAL_SCALE,
    MAX_REPORTED_SCORE,
    MCTSBot,
    _Node,
    _reward_for_side_to_move,
    _Search,
)
from engine.board import Board
from engine.types import Color, Reason, Result
from tests.helpers import board_from, find_move, load_fens, play

# A small fixed middlegame position, used where the opening is too even to be
# interesting for a 64-iteration search.
MIDDLEGAME_FEN = load_fens("positions_middlegame.txt")[0]


def _search(board: Board, **overrides) -> _Search:
    """Build a search object for white-box tests (no iterations run)."""
    options = {
        "exploration": 1.4,
        "rollout_depth": 4,
        "capture_bias": 0.0,
        "iteration_limit": 1,
        "time_source": time.perf_counter,
        "deadline": None,
    }
    options.update(overrides)
    return _Search(board, lambda b: 0, random.Random(0), **options)


# ---------------------------------------------------------------------------
# Sign handling: the hand-built tree
# ---------------------------------------------------------------------------


def test_backpropagation_flips_the_perspective_at_every_edge() -> None:
    board = Board.start()
    search = _search(board)

    root = _Node(parent=None, move=None)
    first = _Node(root, find_move(board, "b0c2"))
    second = _Node(first, find_move(board, "h0g2"))

    # The player who moved into `second` is Red. Suppose Red won the rollout.
    search._backpropagate(second, 1.0)
    assert (root.visits, first.visits, second.visits) == (1, 1, 1)
    assert second.value_sum == 1.0  # Red moved into it: the win is credited to Red
    assert first.value_sum == 0.0  # Black moved into it: a Red win is Black's loss
    assert root.value_sum == 0.0  # the root has no mover; only visits matter

    # The mirror case: the player who moved into `second` (Red) lost.
    search._backpropagate(second, 0.0)
    assert second.value_sum == 1.0  # 1.0 + 0.0
    assert first.value_sum == 1.0  # 0.0 + 1.0
    assert root.visits == 2


def test_selection_prefers_the_child_that_is_good_for_the_player_to_move() -> None:
    # Child values are "reward for the player who moved into the child", which is
    # the player choosing at the root. With exploration 0, UCT is just that value,
    # so the child worth 1.0 must be chosen. A double-negated bug would pick 0.0.
    board = Board.start()
    search = _search(board, exploration=0.0)
    root = _Node(parent=None, move=None)
    root.visits = 20
    losing = _Node(root, find_move(board, "b0c2"))
    winning = _Node(root, find_move(board, "b0c2"))  # same move, different value
    losing.visits = winning.visits = 10
    losing.value_sum = 0.0
    winning.value_sum = 10.0
    root.children = [losing, winning]
    assert search._best_child(root) is winning


def test_reward_for_side_to_move() -> None:
    win = Result(winner=Color.RED, reason=Reason.CHECKMATE)
    draw = Result(winner=None, reason=Reason.MOVE_CAP)
    assert _reward_for_side_to_move(win, Color.RED) == 1.0
    assert _reward_for_side_to_move(win, Color.BLACK) == 0.0
    assert _reward_for_side_to_move(draw, Color.RED) == 0.5


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


def test_returns_a_legal_move_and_reports_iterations() -> None:
    board = Board.start()
    bot = MCTSBot(iterations=64, seed=1)
    result = bot.choose_move(board, Budget())
    assert result.move in board.legal_moves()
    assert result.nodes == 64
    assert result.depth >= 0
    assert result.elapsed_ms >= 0


def test_single_legal_move_is_returned() -> None:
    # Black to move: the general on d9 has both destinations covered by the horse on
    # f7, so the soldier on a6 is the only piece that can move.
    board = board_from({"e0": "K", "f7": "N", "d9": "k", "a6": "p"}, side_to_move="b")
    legal = board.legal_moves()
    assert [move.to_iccs() for move in legal] == ["a6a5"]
    result = MCTSBot(iterations=32, seed=3).choose_move(board, Budget())
    assert result.move.to_iccs() == "a6a5"


def test_finds_mate_in_one() -> None:
    # Black's king (e9) has one real escape (d9; f9 is its own advisor and e8 is
    # covered by the horse). Only a1a9 gives check *and* covers d9; the other
    # checking moves (e.g. a1e1) let the king run to d9. The terminal child is
    # scored 1.0, so a correct search visits it most.
    board = board_from({"e9": "k", "f9": "a", "g7": "N", "a1": "R", "d0": "K"})
    result = MCTSBot(iterations=400, seed=1).choose_move(board, Budget())
    assert result.move.to_iccs() == "a1a9"


def test_prefers_capturing_a_hanging_chariot() -> None:
    # Red's soldier on e4 can take the undefended chariot on e5. Quiet moves keep
    # the evaluation near 0; the capture is worth a chariot, so the squashed reward
    # must make it the most-visited child.
    board = board_from({"e0": "K", "d9": "k", "e4": "P", "e5": "r"})
    result = MCTSBot(iterations=400, seed=1).choose_move(board, Budget())
    assert result.move.to_iccs() == "e4e5"


def test_same_seed_and_iterations_play_the_same_move() -> None:
    first = MCTSBot(iterations=48, seed=7).choose_move(Board.from_fen(MIDDLEGAME_FEN), Budget())
    second = MCTSBot(iterations=48, seed=7).choose_move(Board.from_fen(MIDDLEGAME_FEN), Budget())
    assert first.move == second.move
    assert (first.nodes, first.score, first.depth) == (second.nodes, second.score, second.depth)


# ---------------------------------------------------------------------------
# Board handling
# ---------------------------------------------------------------------------


def test_search_leaves_the_board_exactly_as_it_was() -> None:
    board = Board.start()
    play(board, "h2e2 h9g7")
    before = (board.to_fen(), board.hash, board.ply_count, len(board.records))
    MCTSBot(iterations=64, seed=5).choose_move(board, Budget())
    assert (board.to_fen(), board.hash, board.ply_count, len(board.records)) == before


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_iteration_budget_wins_over_a_generous_time_budget() -> None:
    bot = MCTSBot(iterations=5, time_limit_ms=10_000, seed=1)
    result = bot.choose_move(Board.start(), Budget())
    assert result.nodes == 5


class _FakeClock:
    """Clock that is 0 for the start time and the first check, then 1 s."""

    def __init__(self) -> None:
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return 0.0 if self.reads <= 2 else 1.0


def test_time_budget_is_checked_every_few_iterations() -> None:
    clock = _FakeClock()
    bot = MCTSBot(iterations=10_000, time_limit_ms=50, seed=1, time_source=clock)
    result = bot.choose_move(Board.start(), Budget())

    # The first clock check happens at iteration 0, the next at iteration 16; the
    # fake clock is already past the deadline by then, so 16 iterations complete.
    assert result.nodes == 16
    # Clock reads: start time, checks at iterations 0 and 16, and elapsed_ms.
    assert clock.reads == 4


def test_budget_from_the_caller_is_respected() -> None:
    # The harness's Budget(max_iterations=...) must win over the bot's own default.
    bot = MCTSBot(iterations=1000, seed=1)
    result = bot.choose_move(Board.start(), Budget(max_iterations=12))
    assert result.nodes == 12


# ---------------------------------------------------------------------------
# Registry and validation
# ---------------------------------------------------------------------------


def test_mcts_is_registered_with_its_defaults() -> None:
    defaults = BOT_DEFAULTS["mcts"]
    assert set(defaults) == {
        "iterations",
        "time_limit_ms",
        "exploration",
        "rollout_depth",
        "evaluator",
        "capture_bias",
    }
    assert defaults["evaluator"] == "full"
    assert defaults["capture_bias"] == 0.0

    bot = build_bot({"type": "mcts", "params": {}})
    assert isinstance(bot, MCTSBot)
    assert bot.spec["params"]["iterations"] == 1000
    assert bot.spec["params"]["evaluator"] == "full"

    custom = build_bot({"type": "mcts", "params": {"iterations": 20, "evaluator": "material"}})
    assert custom.spec["params"]["iterations"] == 20


def test_unknown_parameters_are_rejected() -> None:
    with pytest.raises(TypeError, match="nope"):
        build_bot({"type": "mcts", "params": {"nope": 1}})
    with pytest.raises(ValueError, match="unknown evaluator"):
        build_bot({"type": "mcts", "params": {"evaluator": "nope"}})


def test_out_of_range_parameters_are_rejected() -> None:
    for params, message in (
        ({"iterations": 0}, "iterations"),
        ({"time_limit_ms": 0}, "time_limit_ms"),
        ({"exploration": -1}, "exploration"),
        ({"rollout_depth": 0}, "rollout_depth"),
        ({"capture_bias": 1.5}, "capture_bias"),
    ):
        with pytest.raises(ValueError, match=message):
            build_bot({"type": "mcts", "params": params})


# ---------------------------------------------------------------------------
# Harness integration
# ---------------------------------------------------------------------------


def test_harness_plays_an_mcts_game() -> None:
    from harness.match import MatchJob, play_job

    record = play_job(
        MatchJob(
            red={"type": "mcts", "params": {"iterations": 16}},
            black={"type": "greedy", "params": {}},
            seed=11,
            game_id="mcts-game",
            opening_plies=4,
        )
    )
    assert record.result in {"red_win", "black_win", "draw"}
    assert record.plies <= 300
    # The MCTS moves report their iteration count in the "nodes" field.
    mcts_moves = [move for move in record.moves if move.depth > 0 or move.nodes > 0]
    assert mcts_moves
    assert all(move.nodes > 0 for move in mcts_moves)


def test_reported_score_never_reaches_the_mate_range() -> None:
    # The inverse squash diverges near 0/1; the clamp keeps the report in centipawns
    # and far below the search's mate threshold.
    from bots.alphabeta import MATE_THRESHOLD

    board = board_from({"e9": "k", "f9": "a", "g7": "N", "a1": "R", "d0": "K"})
    result = MCTSBot(iterations=64, seed=1).choose_move(board, Budget())
    assert abs(result.score) <= MAX_REPORTED_SCORE
    assert abs(result.score) < MATE_THRESHOLD
    assert result.score > 0  # Red is winning here
    assert EVAL_SCALE > 0


# ---------------------------------------------------------------------------
# Acceptance (slow)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_mcts_beats_random_in_100_games() -> None:
    # 200 iterations per move is the acceptance budget: about 0.24 s per move on this
    # machine, which keeps the 100-game run at roughly five minutes with 8 workers.
    from harness.results import score_for
    from harness.tournament import TournamentConfig, run_match

    records = run_match(
        {"type": "mcts", "params": {"iterations": 200}},
        {"type": "random", "params": {}},
        TournamentConfig(
            games=100,
            seed=1,
            opening_plies=6,
            move_time_limit_ms=10_000.0,
            workers=8,
        ),
    )
    mcts = score_for(records, "mcts", {"iterations": 200})
    assert mcts.games == 100
    assert mcts.win_rate >= 0.95
    assert all(record.plies <= 300 for record in records)
