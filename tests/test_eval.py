"""Tests for the M4 evaluation: features, weights, and the acceptance properties.

The two properties the milestone requires are checked over real middlegame
positions, not hand-picked symmetric ones:

* the same position from Red's view and from Black's view gives exact negatives;
* a mirrored + colour-swapped position with the side to move swapped gives the same
  score.

"Mirrored" is a vertical flip (rank r -> 9-r), as defined in AGENTS.md.
"""

from __future__ import annotations

import random

import pytest

from bots.base import build_bot
from engine import movegen
from engine.board import Board
from engine.rules import MOVE_CAP_PLIES
from engine.types import Color, PieceKind, make_piece, parse_square
from eval.evaluator import (
    DEFAULT_WEIGHTS,
    FEATURE_SETS,
    MAX_EVALUATION_MAGNITUDE,
    MAX_POSITIONAL_MAGNITUDE,
    POSITIONAL_BUDGET,
    TEMPO_BONUS,
    available_evaluators,
    build_evaluator,
    evaluate_from_red,
    make_evaluator,
    positional_contributions,
)
from eval.king_safety import king_safety, king_safety_balance
from eval.material import MAX_ARMY_VALUE, PIECE_VALUES, material_balance
from eval.mobility import mobility_balance
from eval.pst import black_table, pst_value, red_table
from tests.helpers import board_from, load_fens, mirror_board, with_side_to_move

MIDDLEGAME = load_fens("positions_middlegame.txt")[:8]


# ---------------------------------------------------------------------------
# Acceptance: symmetry and the start position
# ---------------------------------------------------------------------------


def test_start_position_is_zero_for_every_evaluator() -> None:
    board = Board.start()
    for name in available_evaluators():
        assert build_evaluator(name)(board) == 0, name
        assert evaluate_from_red(board, name) == 0, name


def test_tempo_bonus_defaults_to_zero() -> None:
    # The acceptance rule allows a tempo bonus only if it is a named constant, and
    # the start position must then stop being 0 -- so it is off by default.
    assert TEMPO_BONUS == 0


@pytest.mark.parametrize("name", available_evaluators())
def test_evaluator_gives_exact_negatives_for_each_side(name: str) -> None:
    evaluator = build_evaluator(name)
    for fen in MIDDLEGAME:
        board = Board.from_fen(fen)
        red_view = evaluator(with_side_to_move(board, Color.RED))
        black_view = evaluator(with_side_to_move(board, Color.BLACK))
        assert red_view == -black_view, f"{name} on {fen}"


@pytest.mark.parametrize("name", available_evaluators())
def test_red_view_is_independent_of_whose_turn_it_is(name: str) -> None:
    # evaluate_from_red is the absolute view: the same placement gives the same
    # number whether Red or Black is to move.
    for fen in MIDDLEGAME:
        board = Board.from_fen(fen)
        red_to_move = evaluate_from_red(with_side_to_move(board, Color.RED), name)
        black_to_move = evaluate_from_red(with_side_to_move(board, Color.BLACK), name)
        assert red_to_move == black_to_move, f"{name} on {fen}"
        # ... and it must agree with the side-to-move evaluator.
        assert red_to_move == build_evaluator(name)(with_side_to_move(board, Color.RED))


@pytest.mark.parametrize("name", available_evaluators())
def test_mirrored_position_scores_identically(name: str) -> None:
    evaluator = build_evaluator(name)
    for fen in MIDDLEGAME:
        board = Board.from_fen(fen)
        assert evaluator(mirror_board(board)) == evaluator(board), f"{name} on {fen}"


def test_evaluator_names_are_the_registry_enum() -> None:
    assert set(available_evaluators()) == {
        "material",
        "material_pst",
        "material_pst_mobility",
        "full",
    }
    # Each named evaluator adds one feature to the previous one.
    assert FEATURE_SETS["material"] == ("material",)
    assert FEATURE_SETS["material_pst"][-1] == "pst"
    assert FEATURE_SETS["material_pst_mobility"][-1] == "mobility"
    assert FEATURE_SETS["full"][-1] == "king_safety"

    for name in available_evaluators():
        bot = build_bot({"type": "alphabeta", "params": {"evaluator": name, "depth": 1}})
        assert bot.spec["params"]["evaluator"] == name


# ---------------------------------------------------------------------------
# Piece-square tables
# ---------------------------------------------------------------------------


def test_soldier_gains_value_as_it_advances() -> None:
    red_soldier = make_piece(Color.RED, PieceKind.SOLDIER)
    home = pst_value(red_soldier, parse_square("e3"))
    forward = pst_value(red_soldier, parse_square("e4"))
    crossed = pst_value(red_soldier, parse_square("e6"))
    deep = pst_value(red_soldier, parse_square("e8"))
    assert home < forward < crossed < deep


def test_horse_prefers_the_centre() -> None:
    horse = make_piece(Color.RED, PieceKind.HORSE)
    assert pst_value(horse, parse_square("e4")) > pst_value(horse, parse_square("a4"))
    assert pst_value(horse, parse_square("e4")) > pst_value(horse, parse_square("a0"))


def test_black_tables_are_red_tables_flipped_by_rank() -> None:
    for kind in PieceKind:
        red = red_table(kind)
        black = black_table(kind)
        for rank in range(9 + 1):
            for file in range(9):
                assert black[(9 - rank) * 9 + file] == red[rank * 9 + file]
    # And the signed table negates Black's values.
    red_horse = make_piece(Color.RED, PieceKind.HORSE)
    black_horse = make_piece(Color.BLACK, PieceKind.HORSE)
    assert pst_value(black_horse, parse_square("e5")) == -pst_value(red_horse, parse_square("e4"))


# ---------------------------------------------------------------------------
# Mobility
# ---------------------------------------------------------------------------


def test_mobility_rewards_freedom() -> None:
    # Red has a free chariot, Black has a general and a soldier that cannot move far.
    board = board_from({"e0": "K", "d9": "k", "a0": "R", "e6": "p"})
    assert mobility_balance(board) > 0
    assert movegen.count_pseudo_legal_moves(board, Color.RED) > movegen.count_pseudo_legal_moves(
        board, Color.BLACK
    )


def test_count_pseudo_legal_moves_matches_the_generator() -> None:
    rng = random.Random(8675309)
    board = Board.start()
    checked = 0
    for _ in range(60):
        for color in Color:
            assert movegen.count_pseudo_legal_moves(board, color) == len(
                movegen.generate_pseudo_legal_moves(board, color)
            ), board.to_fen()
            checked += 1
        legal = board.legal_moves()
        if not legal:
            break
        board.make_move(rng.choice(legal))
    assert checked > 60


# ---------------------------------------------------------------------------
# King safety
# ---------------------------------------------------------------------------


def test_king_safety_rewards_intact_guards() -> None:
    guarded = board_from({"e0": "K", "d9": "k", "d0": "A", "f0": "A", "c0": "B", "g0": "B"})
    bare = board_from({"e0": "K", "d9": "k"})
    assert king_safety(guarded, Color.RED) > king_safety(bare, Color.RED)


def test_king_safety_penalises_an_exposed_general() -> None:
    # A general that has stepped up the palace is one step closer to danger.
    home = board_from({"e0": "K", "d9": "k"})
    advanced = board_from({"e2": "K", "d9": "k"})
    assert king_safety(advanced, Color.RED) < king_safety(home, Color.RED)


def test_king_safety_balance_is_zero_on_the_start_position() -> None:
    assert king_safety_balance(Board.start()) == 0


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


def test_weights_are_configurable_and_default_to_the_documented_values() -> None:
    assert DEFAULT_WEIGHTS == {"material": 1, "pst": 1, "mobility": 2, "king_safety": 1}

    board = board_from({"e0": "K", "d9": "k", "a0": "R", "e6": "p"})
    no_mobility = make_evaluator(("material", "pst", "mobility"), {"mobility": 0})
    heavy_mobility = make_evaluator(("material", "pst", "mobility"), {"mobility": 100})
    assert heavy_mobility(board) > no_mobility(board)


def test_unknown_feature_or_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown evaluator"):
        build_evaluator("nope")
    with pytest.raises(KeyError, match="unknown evaluation feature"):
        make_evaluator(("material",), {"tempo": 1})


def test_material_still_dominates_the_smaller_features() -> None:
    # A chariot up must outscore any plausible positional advantage: the positional
    # budget caps out at 500 centipawns in total.
    ahead = board_from({"e0": "K", "d9": "k", "a0": "R", "e8": "p"})
    behind = board_from({"e0": "K", "d9": "k", "a8": "r", "e3": "P"})
    full = build_evaluator("full")
    assert full(ahead) > 500
    assert full(behind) < -500


# ---------------------------------------------------------------------------
# Bounds: positional terms stay small, evaluations stay below the mate score
# ---------------------------------------------------------------------------


def test_largest_material_total_is_computed_from_the_army() -> None:
    # 2 chariots + 2 cannons + 2 horses + 2 advisors + 2 elephants, all soldiers
    # across the river. This is the number the mate-margin test builds on.
    expected = 2 * 900 + 2 * 450 + 2 * 400 + 2 * 200 + 2 * 200 + 5 * 200
    assert MAX_ARMY_VALUE == expected == 5300


def test_positional_terms_cannot_outweigh_a_chariot() -> None:
    chariot = PIECE_VALUES[PieceKind.CHARIOT]
    assert MAX_POSITIONAL_MAGNITUDE == sum(POSITIONAL_BUDGET.values()) == 500
    assert MAX_POSITIONAL_MAGNITUDE < chariot


def test_positional_budget_beats_even_an_absurd_weight() -> None:
    # Clamping happens after weighting, so a huge weight cannot break the bound.
    board = Board.from_fen(MIDDLEGAME[0])
    absurd = make_evaluator(
        ("pst", "mobility", "king_safety"),
        {"pst": 10_000, "mobility": 10_000, "king_safety": 10_000},
    )
    assert abs(absurd(board)) <= MAX_POSITIONAL_MAGNITUDE + TEMPO_BONUS


def test_positional_advantage_never_beats_a_chariot_in_practice() -> None:
    # Red is exactly one chariot down, but has the advanced soldiers and Black's army
    # sits at home. Material must still win, because the positional terms are capped
    # far below the chariot's 900.
    board = board_from(
        {
            # Red: full army except one chariot, soldiers on the river's edge.
            "e0": "K",
            "d0": "A",
            "f0": "A",
            "c0": "B",
            "g0": "B",
            "b0": "N",
            "h0": "N",
            "b2": "C",
            "h2": "C",
            "a0": "R",
            "a4": "P",
            "c4": "P",
            "e4": "P",
            "g4": "P",
            "i4": "P",
            # Black: full army, untouched at home.
            "e9": "k",
            "d9": "a",
            "f9": "a",
            "c9": "b",
            "g9": "b",
            "b9": "n",
            "h9": "n",
            "b7": "c",
            "h7": "c",
            "a9": "r",
            "i9": "r",
            "a6": "p",
            "c6": "p",
            "e6": "p",
            "g6": "p",
            "i6": "p",
        }
    )
    assert build_evaluator("material")(board) == -900
    assert build_evaluator("full")(board) < -500
    # The positional advantage Red does have is real but small.
    assert sum(positional_contributions(board).values()) < 200


def test_evaluation_plus_move_cap_stays_below_the_mate_score() -> None:
    from bots.alphabeta import MATE_SCORE

    margin = MAX_EVALUATION_MAGNITUDE + MOVE_CAP_PLIES
    assert margin < MATE_SCORE, (
        f"MAX_EVALUATION_MAGNITUDE ({MAX_EVALUATION_MAGNITUDE}) + "
        f"move cap ({MOVE_CAP_PLIES}) = {margin} does not fit under MATE_SCORE ({MATE_SCORE})"
    )


def test_every_middlegame_score_stays_within_the_bound() -> None:
    for name in available_evaluators():
        evaluator = build_evaluator(name)
        for fen in load_fens("positions_middlegame.txt"):
            score = evaluator(Board.from_fen(fen))
            assert abs(score) <= MAX_EVALUATION_MAGNITUDE, f"{name} on {fen}: {score}"


def test_material_evaluator_is_bit_identical_to_material_balance() -> None:
    # The clamp machinery must not perturb the material evaluator: the M3 search
    # results and the slow acceptance tests stay valid only if it does not.
    for fen in load_fens("positions_middlegame.txt")[:10]:
        board = Board.from_fen(fen)
        assert build_evaluator("material")(board) == material_balance(board)
