"""Tests for the match harness, the bots, and the results aggregation.

The harness tests use small scripted bots where the point is the game loop (the
perpetual-check test is the important one: it only passes if the loop lets
``make_move`` record check flags), and real bots where the point is that a game
terminates and is reproducible.
"""

from __future__ import annotations

import json

import pytest

from bots.base import BOT_TYPES, Bot, Budget, SearchResult, build_bot
from bots.greedy_bot import MATE_SCORE, GreedyBot
from bots.random_bot import RandomBot
from engine import rules
from engine.board import Board
from engine.types import Color, Move, Reason, Result
from harness.match import (
    GameRecord,
    MatchJob,
    play_game,
    play_job,
    save_game,
)
from harness.results import score_for, summarize
from harness.tournament import TournamentConfig, build_jobs, run_match
from tests.helpers import board_from, find_move

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class ScriptedBot(Bot):
    """Plays a fixed list of ICCS moves; fails loudly if one is not legal."""

    type_name = "scripted"

    def __init__(self, move_names: list[str]) -> None:
        super().__init__()
        self._move_names = list(move_names)

    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        if not self._move_names:
            raise AssertionError("scripted bot ran out of moves")
        name = self._move_names.pop(0)
        for move in board.legal_moves():
            if move.to_iccs() == name:
                return SearchResult(move=move)
        raise AssertionError(f"scripted move {name} is not legal in {board.to_fen()}")


def _random_spec() -> dict[str, object]:
    return {"type": "random", "params": {}}


def _greedy_spec() -> dict[str, object]:
    return {"type": "greedy", "params": {}}


# ---------------------------------------------------------------------------
# The game loop
# ---------------------------------------------------------------------------


def test_harness_game_loop_reports_perpetual_check() -> None:
    # Red checks on every move and Black never does, so the third repetition is a
    # perpetual-check loss for Red. This only works if the harness calls
    # make_move with the default record_check=True: with the search shortcut the
    # flags would all be False and the game would end as a plain repetition draw.
    start = board_from({"f0": "K", "e9": "k", "d2": "R"}).to_fen()
    job = MatchJob(
        red={"type": "scripted"},
        black={"type": "scripted"},
        seed=5,
        game_id="perpetual",
        opening_plies=0,
        start_fen=start,
    )
    red = ScriptedBot(["d2e2", "e2d2", "d2e2", "e2d2"])
    black = ScriptedBot(["e9d9", "d9e9", "e9d9", "d9e9"])

    record = play_game(job, red_bot=red, black_bot=black)

    assert record.reason == "perpetual_check"
    assert record.result == "black_win"
    assert record.plies == 8
    assert [move.move for move in record.moves] == [
        "d2e2",
        "e9d9",
        "e2d2",
        "d9e9",
        "d2e2",
        "e9d9",
        "e2d2",
        "d9e9",
    ]


def test_harness_rejects_an_illegal_bot_move() -> None:
    class BrokenBot(Bot):
        type_name = "broken"

        def choose_move(self, board: Board, budget: Budget) -> SearchResult:
            # a0i9 is not a legal move from the start position.
            return SearchResult(move=Move(0, 89))

    job = MatchJob(
        red={"type": "broken"},
        black={"type": "broken"},
        seed=1,
        game_id="broken",
        opening_plies=0,
    )
    with pytest.raises(RuntimeError, match="illegal move"):
        play_game(job, red_bot=BrokenBot(), black_bot=BrokenBot())


def test_random_vs_random_terminates_within_the_cap() -> None:
    record = play_job(
        MatchJob(
            red=_random_spec(),
            black=_random_spec(),
            seed=9,
            game_id="random-game",
            opening_plies=4,
        )
    )
    assert record.plies <= 300
    assert record.result in {"red_win", "black_win", "draw"}
    assert record.reason in {"checkmate", "stalemate", "repetition", "perpetual_check", "move_cap"}
    assert len(record.moves) + len(record.opening_moves) == record.plies


def test_same_job_plays_the_same_game() -> None:
    job = MatchJob(
        red=_random_spec(),
        black=_greedy_spec(),
        seed=21,
        game_id="repeatable",
        opening_plies=6,
    )
    first = play_job(job)
    second = play_job(job)
    assert [m.move for m in first.moves] == [m.move for m in second.moves]
    assert (first.start_fen, first.result, first.reason) == (
        second.start_fen,
        second.result,
        second.reason,
    )


def test_opening_is_recorded_and_reachable_from_start_fen() -> None:
    record = play_job(
        MatchJob(
            red=_random_spec(),
            black=_random_spec(),
            seed=33,
            game_id="opening",
            opening_plies=6,
        )
    )
    assert len(record.opening_moves) == 6

    replay = Board.start()
    for name in record.opening_moves:
        replay.make_move(find_move(replay, name))
    assert replay.to_fen() == record.start_fen
    # Six plies means Red is to move again.
    assert record.start_fen.endswith(" w - - 0 1")


def test_color_swap_and_paired_seeds() -> None:
    config = TournamentConfig(games=4, seed=10)
    jobs = build_jobs(_greedy_spec(), _random_spec(), config)
    assert [job.red["type"] for job in jobs] == ["greedy", "random", "greedy", "random"]
    assert [job.seed for job in jobs] == [10, 10, 11, 11]
    assert [job.game_id for job in jobs] == ["g10-000", "g10-001", "g11-002", "g11-003"]


def test_tournament_config_rejects_odd_game_counts() -> None:
    with pytest.raises(ValueError, match="even"):
        TournamentConfig(games=3)


def test_tournament_runs_games_in_a_process_pool() -> None:
    # Two games, two workers: this is the spawn-safety check. It only passes when
    # play_job is a module-level function taking picklable MatchJob values.
    records = run_match(
        _random_spec(),
        _greedy_spec(),
        TournamentConfig(games=2, seed=3, opening_plies=4, workers=2),
    )
    assert len(records) == 2
    assert {record.id for record in records} == {"g3-000", "g3-001"}
    assert all(record.result is not None for record in records)


# ---------------------------------------------------------------------------
# Game JSON
# ---------------------------------------------------------------------------


def test_saved_game_matches_the_json_schema(tmp_path) -> None:
    record = play_job(
        MatchJob(
            red=_greedy_spec(),
            black=_random_spec(),
            seed=44,
            game_id="schema",
            opening_plies=4,
        )
    )
    path = save_game(record, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert set(data) == {
        "id",
        "red",
        "black",
        "seed",
        "start_fen",
        "opening_moves",
        "moves",
        "result",
        "reason",
        "plies",
    }
    assert data["red"] == {"type": "greedy", "params": {}}
    assert data["black"] == {"type": "random", "params": {}}
    assert data["result"] in {"red_win", "black_win", "draw"}
    assert data["reason"] in {
        "checkmate",
        "stalemate",
        "repetition",
        "perpetual_check",
        "move_cap",
    }
    assert data["plies"] == len(data["opening_moves"]) + len(data["moves"])
    assert set(data["moves"][0]) == {"move", "time_ms", "nodes", "depth", "eval"}
    assert isinstance(data["moves"][0]["time_ms"], (int, float))


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


def test_build_bot_and_specs() -> None:
    random_bot = build_bot(_random_spec(), seed=1)
    assert isinstance(random_bot, RandomBot)
    assert random_bot.spec == {"type": "random", "params": {}}
    assert isinstance(build_bot(_greedy_spec()), GreedyBot)
    assert set(BOT_TYPES) == {"random", "greedy", "alphabeta", "mcts"}

    with pytest.raises(ValueError, match="player, not a bot"):
        build_bot({"type": "human"})
    with pytest.raises(ValueError, match="unknown bot type"):
        build_bot({"type": "does-not-exist"})


def test_random_bot_plays_a_legal_move_and_is_seeded() -> None:
    first = RandomBot(seed=7).choose_move(Board.start(), Budget())
    second = RandomBot(seed=7).choose_move(Board.start(), Budget())
    assert first.move == second.move
    assert first.move in Board.start().legal_moves()


def test_greedy_takes_the_most_valuable_capture() -> None:
    # A chariot is available on a5 (900) and a soldier on h4 (200).
    board = board_from({"e0": "K", "d9": "k", "a0": "R", "a5": "r", "h0": "R", "h4": "p"})
    result = GreedyBot(seed=1).choose_move(board, Budget())
    assert result.move.to_iccs() == "a0a5"
    assert result.score == 900


def test_greedy_uses_mvv_lva_between_equal_captures() -> None:
    # The black horse on e5 can be taken by the chariot on a5 or the horse on d3.
    # Same gain, but the horse is the cheaper attacker, so it must take.
    board = board_from({"e0": "K", "d9": "k", "a5": "R", "d3": "N", "e5": "n"})
    result = GreedyBot(seed=1).choose_move(board, Budget())
    assert result.move.to_iccs() == "d3e5"
    assert result.score == 400


def test_greedy_plays_mate_in_one() -> None:
    # Red plays a8a9: the chariot checks along the back rank, d9 is covered by the
    # chariot and the horse on g7 covers both f9 and e8. Greedy must find it
    # rather than shuffle, and reports the mate score.
    board = board_from({"e9": "k", "a8": "R", "g7": "N", "d0": "K"})
    result = GreedyBot(seed=1).choose_move(board, Budget())
    assert result.move.to_iccs() == "a8a9"
    assert result.score == MATE_SCORE

    # The move really is mate: the game is over and Red won.
    board.make_move(result.move)
    assert board.legal_moves() == []
    assert rules.game_result(board) == Result(winner=Color.RED, reason=Reason.CHECKMATE)


def test_greedy_returns_a_quiet_move_when_nothing_is_capturable() -> None:
    # Two generals only: no captures anywhere, so the score is 0 and any legal
    # move will do.
    board = board_from({"e0": "K", "d9": "k"})
    result = GreedyBot(seed=1).choose_move(board, Budget())
    assert result.move in board.legal_moves()
    assert result.score == 0


def test_greedy_finds_the_cannon_capture_in_the_start_position() -> None:
    # The start position is not quiet: Cb2xb9 and Ch2xh9 both jump a soldier to
    # take a horse, so the greedy bot leaves its opening book immediately.
    board = Board.start()
    result = GreedyBot(seed=1).choose_move(board, Budget())
    assert result.move.to_iccs() in {"b2b9", "h2h9"}
    assert result.score == 400


# ---------------------------------------------------------------------------
# Results aggregation
# ---------------------------------------------------------------------------


def _record(red: str, black: str, result: str | None, reason: str = "checkmate") -> GameRecord:
    return GameRecord(
        id=f"{red}-{black}-{result}",
        red={"type": red, "params": {}},
        black={"type": black, "params": {}},
        seed=0,
        start_fen="",
        opening_moves=[],
        result=result,
        reason=reason,
        plies=10,
    )


def test_score_for_credits_the_right_side() -> None:
    records = [
        _record("greedy", "random", "red_win"),
        _record("random", "greedy", "black_win"),
        _record("greedy", "random", "draw", "move_cap"),
    ]
    greedy = score_for(records, "greedy")
    random_bot = score_for(records, "random")
    assert (greedy.wins, greedy.draws, greedy.losses) == (2, 1, 0)
    assert (random_bot.wins, random_bot.draws, random_bot.losses) == (0, 1, 2)
    assert greedy.win_rate == pytest.approx(2 / 3)
    assert greedy.score_rate == pytest.approx(5 / 6)


def test_score_for_skips_self_play() -> None:
    records = [_record("greedy", "greedy", "red_win")]
    assert score_for(records, "greedy").games == 0


def test_summary_counts_endings_and_averages() -> None:
    records = [
        _record("greedy", "random", "red_win", "checkmate"),
        _record("random", "greedy", "draw", "move_cap"),
    ]
    summary = summarize(records)
    assert (summary.games, summary.red_wins, summary.black_wins, summary.draws) == (2, 1, 0, 1)
    assert summary.reasons == {"checkmate": 1, "move_cap": 1}
    assert summary.cap_hits == 1
    assert summary.avg_plies == 10
    # No moves recorded in these fabricated games, so the per-move averages are 0.
    assert summary.avg_move_time_ms == 0


# ---------------------------------------------------------------------------
# Acceptance (slow): greedy must beat random in 100 games
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_greedy_beats_random_in_100_games() -> None:
    records = run_match(
        _greedy_spec(),
        _random_spec(),
        TournamentConfig(games=100, seed=1, opening_plies=6, workers=1),
    )
    greedy = score_for(records, "greedy")
    assert greedy.games == 100
    assert greedy.losses == 0
    assert greedy.win_rate >= 0.90
