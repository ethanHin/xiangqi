"""Tests for the M3 alpha-beta search, one test per acceptance item.

The fixed position sets in ``tests/positions_*.txt`` are generated once by
``python -m tools.make_positions`` and committed, so node counts are comparable
between runs and between machines.
"""

from __future__ import annotations

import time

import pytest

from bots.alphabeta import (
    CLOCK_CHECK_INTERVAL,
    INFINITY,
    MATE_SCORE,
    MATE_THRESHOLD,
    AlphaBetaBot,
    _Search,
    _SearchTimeout,
    reference_minimax,
    search_at_depth,
)
from bots.base import BOT_DEFAULTS, Budget, build_bot
from engine.board import Board
from eval.material import material_balance
from harness.match import MatchJob, play_job
from harness.results import score_for
from harness.tournament import TournamentConfig, run_match
from tests.helpers import board_from, find_move, load_fens

MIDDLEGAME = load_fens("positions_middlegame.txt")
TRANSPOSITIONS = load_fens("positions_transpositions.txt")
QUIET = load_fens("positions_quiet.txt")

# Small subsets keep the default test run fast; the slow tests use the whole files.
SAMPLE = MIDDLEGAME[:6]


def _total_nodes(fens: list[str], depth: int, **options) -> int:
    return sum(search_at_depth(Board.from_fen(fen), depth, **options).nodes for fen in fens)


def _values(fens: list[str], depth: int, **options) -> list[int]:
    return [search_at_depth(Board.from_fen(fen), depth, **options).value for fen in fens]


# ---------------------------------------------------------------------------
# Reference agreement and node counts
# ---------------------------------------------------------------------------


def test_alpha_beta_matches_minimax_on_sample_positions() -> None:
    for fen in SAMPLE:
        board = Board.from_fen(fen)
        expected = reference_minimax(board, 2)
        actual = search_at_depth(board, 2).value
        assert actual == expected, f"value mismatch on {fen}"


def test_alpha_beta_searches_fewer_nodes_than_minimax() -> None:
    minimax_nodes = 0
    alpha_beta_nodes = 0
    for fen in SAMPLE:
        board = Board.from_fen(fen)
        counter = [0]
        reference_minimax(board, 2, counter=counter)
        minimax_nodes += counter[0]
        alpha_beta_nodes += search_at_depth(board, 2).nodes
    assert alpha_beta_nodes < minimax_nodes


def test_move_ordering_reduces_nodes_and_preserves_values() -> None:
    unordered_nodes = 0
    ordered_nodes = 0
    unordered_values = []
    ordered_values = []
    for fen in SAMPLE:
        board = Board.from_fen(fen)
        plain = search_at_depth(board, 3, use_ordering=False, use_tt=False)
        ordered = search_at_depth(board, 3, use_ordering=True, use_tt=False)
        unordered_nodes += plain.nodes
        ordered_nodes += ordered.nodes
        unordered_values.append(plain.value)
        ordered_values.append(ordered.value)
    assert ordered_nodes < unordered_nodes
    assert ordered_values == unordered_values


def test_transposition_table_reduces_nodes_and_preserves_values() -> None:
    without_nodes = 0
    with_nodes = 0
    without_values = []
    with_values = []
    for fen in TRANSPOSITIONS:
        board = Board.from_fen(fen)
        plain = search_at_depth(board, 4, use_tt=False)
        cached = search_at_depth(board, 4, use_tt=True)
        without_nodes += plain.nodes
        with_nodes += cached.nodes
        without_values.append(plain.value)
        with_values.append(cached.value)
    assert with_nodes < without_nodes
    assert with_values == without_values


@pytest.mark.slow
def test_alpha_beta_matches_minimax_on_fifty_positions() -> None:
    """The M3 acceptance check: same root value as plain minimax, fewer nodes."""
    fens = MIDDLEGAME[:50]
    alpha_beta_nodes = 0
    minimax_nodes = 0
    for fen in fens:
        board = Board.from_fen(fen)
        counter = [0]
        expected = reference_minimax(board, 3, counter=counter)
        minimax_nodes += counter[0]
        result = search_at_depth(board, 3)
        alpha_beta_nodes += result.nodes
        assert result.value == expected, f"value mismatch on {fen}"
    assert alpha_beta_nodes < minimax_nodes


# ---------------------------------------------------------------------------
# Terminal scores and repetition
# ---------------------------------------------------------------------------


def test_stalemated_side_to_move_scores_a_loss_not_a_draw() -> None:
    # Black to move, not in check, and no legal move: a loss in Xiangqi, so the
    # value must be -MATE, not 0.
    board = board_from({"e9": "k", "d0": "R", "f0": "R", "e7": "P", "f1": "K"}, side_to_move="b")
    assert board.legal_moves() == []
    assert search_at_depth(board, 1).value == -MATE_SCORE


def test_stalemating_the_opponent_scores_a_win() -> None:
    # Red to move; every legal move leaves Black with no legal move, so this is a
    # win in one, not a draw.
    board = board_from({"e9": "k", "d0": "R", "f0": "R", "e7": "P", "f1": "K"})
    value = search_at_depth(board, 1).value
    assert value == MATE_SCORE - 1
    assert value >= MATE_THRESHOLD


def test_repeated_position_on_the_path_scores_zero() -> None:
    # White-box: mark the position after a move as "already on the path" and check
    # that the node returns the draw score immediately.
    board = Board.start()
    search = _Search(board, material_balance, use_ordering=False, use_tt=False)
    move = board.legal_moves()[0]
    board.make_move(move, record_check=False)
    try:
        search.path[board.hash] = 2
        value = search.negamax(2, -INFINITY, INFINITY, 1)
    finally:
        board.unmake_move()
    assert value == 0
    assert search._path_dependent == 1
    assert search.tt == {}  # repetitions are path-dependent and never stored


def test_subtrees_with_a_repetition_are_not_stored() -> None:
    # The root has a child that scores a path repetition, so the root's own value is
    # path-dependent and must not enter the transposition table.
    board = board_from({"a0": "R", "a9": "r", "e0": "K", "d9": "k"})
    search = _Search(board)
    move = find_move(board, "a0a1")
    board.make_move(move, record_check=False)
    repeated_hash = board.hash
    board.unmake_move()

    search.path[board.hash] = 1
    search.path[repeated_hash] = 2
    search.negamax(2, -INFINITY, INFINITY, 0)

    assert board.hash not in search.tt


# ---------------------------------------------------------------------------
# Iterative deepening and the clock
# ---------------------------------------------------------------------------


def test_iterative_deepening_respects_a_50ms_budget() -> None:
    board = Board.start()
    bot = AlphaBetaBot(time_limit_ms=50)
    started = time.perf_counter()
    result = bot.choose_move(board, Budget())
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert result.move in board.legal_moves()
    assert result.depth >= 1
    # The clock is checked every CLOCK_CHECK_INTERVAL nodes, so a search can overshoot
    # by at most one interval; 3x the budget is a generous upper bound for a loaded
    # machine, while still catching "ignores the budget" bugs.
    assert elapsed_ms < 150, f"search took {elapsed_ms:.0f} ms for a 50 ms budget"


class _FakeClock:
    """Clock that reports a huge time on the second read, so a search aborts early."""

    def __init__(self) -> None:
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return 0.0 if self.reads == 1 else 1.0


def test_iterative_deepening_returns_the_last_completed_depth(monkeypatch) -> None:
    # Drive the abort directly instead of through the clock, so the test does not
    # depend on how many nodes a depth happens to need. Depth 1 completes; every
    # deeper iteration reports a timeout.
    search = _Search(Board.start())
    original = search._search_root
    calls = {"count": 0}

    def fake_search_root(depth: int):
        calls["count"] += 1
        if depth >= 2:
            raise _SearchTimeout
        return original(depth)

    monkeypatch.setattr(search, "_search_root", fake_search_root)
    move, value, completed_depth = search.run(6)

    assert completed_depth == 1
    assert calls["count"] >= 2  # depth 1 finished, depth 2 was attempted
    # The result must be the depth-1 answer (take a hanging horse), not a partial
    # score from the abandoned iteration.
    assert value == search_at_depth(Board.start(), 1).value
    assert move is not None and move.to_iccs() in {"b2b9", "h2h9"}


def test_deadline_aborts_the_search_but_keeps_a_usable_result() -> None:
    clock = _FakeClock()
    board = Board.start()
    bot = AlphaBetaBot(time_limit_ms=50, time_source=clock)
    result = bot.choose_move(board, Budget())

    # Whatever depth the abort allowed, the reported score must belong to that
    # completed depth rather than to a half-searched iteration.
    assert result.depth >= 1
    assert result.move in board.legal_moves()
    clean = search_at_depth(Board.start(), result.depth)
    assert result.score == clean.value


def test_clock_is_checked_every_n_nodes_not_every_node() -> None:
    clock = _FakeClock()
    bot = AlphaBetaBot(time_limit_ms=50, time_source=clock)
    result = bot.choose_move(Board.start(), Budget())
    # One read for the start time, then at most one per interval of nodes searched.
    assert clock.reads <= 2 + result.nodes // CLOCK_CHECK_INTERVAL
    assert clock.reads < 10  # nowhere near one read per node


# ---------------------------------------------------------------------------
# Board safety and the bot wrapper
# ---------------------------------------------------------------------------


def test_search_leaves_the_board_unchanged() -> None:
    board = Board.start()
    before = (board.to_fen(), board.hash, board.ply_count)
    search_at_depth(board, 3)
    assert (board.to_fen(), board.hash, board.ply_count) == before


def test_bot_reports_depth_nodes_and_score() -> None:
    board = Board.start()
    bot = AlphaBetaBot(depth=3)
    result = bot.choose_move(board, Budget())
    assert result.move in board.legal_moves()
    assert result.depth == 3
    assert result.nodes > 0
    assert result.elapsed_ms >= 0


def test_bot_raises_when_the_game_is_over() -> None:
    board = board_from({"e9": "k", "d0": "R", "f0": "R", "e7": "P", "f1": "K"}, side_to_move="b")
    with pytest.raises(ValueError, match="no legal moves"):
        AlphaBetaBot(depth=2).choose_move(board, Budget())


def test_alphabeta_is_registered_with_its_parameters() -> None:
    assert "alphabeta" in BOT_DEFAULTS
    defaults = BOT_DEFAULTS["alphabeta"]
    assert set(defaults) == {"depth", "time_limit_ms", "evaluator", "quiescence"}

    bot = build_bot(
        {"type": "alphabeta", "params": {"depth": 2, "time_limit_ms": 100, "evaluator": "material"}}
    )
    assert isinstance(bot, AlphaBetaBot)
    assert bot.spec["type"] == "alphabeta"
    assert bot.spec["params"]["depth"] == 2

    with pytest.raises(ValueError, match="unknown evaluator"):
        build_bot({"type": "alphabeta", "params": {"evaluator": "does-not-exist"}})


# ---------------------------------------------------------------------------
# Quiescence
# ---------------------------------------------------------------------------


def test_quiescence_does_not_change_quiet_positions() -> None:
    for fen in QUIET:
        board = Board.from_fen(fen)
        without = search_at_depth(board, 3, quiescence=False).value
        with_q = search_at_depth(board, 3, quiescence=True).value
        assert with_q == without, f"quiescence changed the value of {fen}"


def test_quiescence_extends_the_search_on_captures() -> None:
    # A position with captures available must give quiescence more work to do.
    board = Board.start()
    without = search_at_depth(board, 2, quiescence=False)
    with_q = search_at_depth(board, 2, quiescence=True)
    assert with_q.nodes > without.nodes


# ---------------------------------------------------------------------------
# Harness integration and acceptance (slow)
# ---------------------------------------------------------------------------


def test_harness_plays_an_alphabeta_game() -> None:
    record = play_job(
        MatchJob(
            red={"type": "alphabeta", "params": {"depth": 1}},
            black={"type": "greedy", "params": {}},
            seed=7,
            game_id="alphabeta-game",
            opening_plies=4,
            move_time_limit_ms=200,
        )
    )
    assert record.result in {"red_win", "black_win", "draw"}
    assert record.plies <= 300


@pytest.mark.slow
def test_alphabeta_beats_greedy_in_100_games() -> None:
    records = run_match(
        {"type": "alphabeta", "params": {"time_limit_ms": 200}},
        {"type": "greedy", "params": {}},
        TournamentConfig(games=100, seed=1, opening_plies=6, move_time_limit_ms=200, workers=4),
    )
    alpha_beta = score_for(records, "alphabeta")
    assert alpha_beta.games == 100
    assert alpha_beta.win_rate >= 0.95
