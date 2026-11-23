"""One test per Xiangqi movement quirk.

Each test builds the smallest position that shows the rule, so a reader can see
the rule and the expectation side by side. Pseudo-legal tests use
``generate_pseudo_legal_moves`` because check is not what they are about; the
legality tests use ``board.legal_moves()``.
"""

from __future__ import annotations

from helpers import board_from, move_names

from engine import movegen
from engine.types import Move


def pseudo_legal(pieces: dict[str, str], side: str = "w") -> set[str]:
    """ICCS names of the pseudo-legal moves for the side to move."""
    return move_names(movegen.generate_pseudo_legal_moves(board_from(pieces, side)))


def legal(pieces: dict[str, str], side: str = "w") -> set[str]:
    """ICCS names of the legal moves for the side to move."""
    return move_names(board_from(pieces, side).legal_moves())


# ---------------------------------------------------------------------------
# Horse
# ---------------------------------------------------------------------------


def test_horse_leg_blocked_stops_both_destinations_that_share_it() -> None:
    # The soldier on e2 is the leg for both e1f3 and e1d3; the other four horse
    # destinations use the legs f1 and d1 and stay available.
    moves = pseudo_legal({"e1": "N", "e2": "P"})
    assert {"e1f3", "e1d3"}.isdisjoint(moves)
    assert {"e1g2", "e1g0", "e1c0", "e1c2"}.issubset(moves)


def test_horse_leg_clear_allows_every_destination() -> None:
    moves = pseudo_legal({"e1": "N"})
    assert {"e1f3", "e1d3", "e1g2", "e1g0", "e1c0", "e1c2"} == moves


# ---------------------------------------------------------------------------
# Elephant
# ---------------------------------------------------------------------------


def test_elephant_eye_blocked() -> None:
    # c2a0 needs the eye on b1; put a piece there and the move disappears.
    moves = pseudo_legal({"c2": "B", "b1": "P"})
    assert "c2a0" not in moves
    assert "c2e0" in moves


def test_elephant_cannot_cross_the_river() -> None:
    # From a4 the elephant could reach c6, but that is across the river for Red.
    assert pseudo_legal({"a4": "B"}) == {"a4c2"}
    # Mirror for Black: h6 can reach f8 but not f4.
    assert pseudo_legal({"h6": "b"}, side="b") == {"h6f8"}


# ---------------------------------------------------------------------------
# Palace limits
# ---------------------------------------------------------------------------


def test_general_is_confined_to_its_palace() -> None:
    assert pseudo_legal({"e0": "K"}) == {"e0d0", "e0f0", "e0e1"}
    assert pseudo_legal({"e9": "k"}, side="b") == {"e9d9", "e9f9", "e9e8"}


def test_advisor_moves_one_diagonal_step_inside_the_palace() -> None:
    assert pseudo_legal({"d0": "A"}) == {"d0e1"}
    assert pseudo_legal({"e1": "A"}) == {"e1d0", "e1f0", "e1d2", "e1f2"}


# ---------------------------------------------------------------------------
# Cannon
# ---------------------------------------------------------------------------


def test_cannon_needs_exactly_one_screen_to_capture() -> None:
    # One screen on a1: the enemy soldier on a3 can be captured.
    assert "a0a3" in pseudo_legal({"a0": "C", "a1": "P", "a3": "p"})


def test_cannon_cannot_capture_with_no_screen() -> None:
    # The enemy soldier is the very first piece on the ray, so there is nothing
    # to jump over.
    assert "a0a1" not in pseudo_legal({"a0": "C", "a1": "p"})


def test_cannon_cannot_capture_with_two_screens() -> None:
    assert "a0a3" not in pseudo_legal({"a0": "C", "a1": "P", "a2": "P", "a3": "p"})


def test_cannon_does_not_jump_to_quiet_squares_past_the_screen() -> None:
    moves = pseudo_legal({"a0": "C", "a1": "P", "a3": "p"})
    assert "a0a2" not in moves
    assert "a0b0" in moves


# ---------------------------------------------------------------------------
# Soldier
# ---------------------------------------------------------------------------


def test_soldier_moves_forward_only_before_the_river() -> None:
    assert pseudo_legal({"e3": "P"}) == {"e3e4"}
    assert pseudo_legal({"e6": "p"}, side="b") == {"e6e5"}


def test_soldier_moves_forward_or_sideways_after_the_river() -> None:
    assert pseudo_legal({"e5": "P"}) == {"e5e6", "e5d5", "e5f5"}
    assert pseudo_legal({"e4": "p"}, side="b") == {"e4e3", "e4d4", "e4f4"}


# ---------------------------------------------------------------------------
# Check rules
# ---------------------------------------------------------------------------


def test_flying_general_makes_the_exposing_move_illegal() -> None:
    # The chariot on e5 is the only piece between the two generals. Leaving the
    # e-file would let them face each other, so every such move is illegal.
    moves = legal({"e0": "K", "e9": "k", "e5": "R"})
    assert "e5d5" not in moves
    assert "e5f5" not in moves
    assert "e5e6" in moves


def test_move_exposing_own_general_is_illegal() -> None:
    # The chariot on e1 shields the general from the Black chariot on e5.
    moves = legal({"e0": "K", "e1": "R", "e5": "r"})
    assert "e1d1" not in moves
    assert "e1e2" in moves


def test_is_in_check_detects_flying_general() -> None:
    board = board_from({"e0": "K", "e9": "k"})
    assert board.is_in_check() is True
    assert board.is_in_check(board.side_to_move.opponent) is True


def test_is_in_check_respects_blockers() -> None:
    assert board_from({"e0": "K", "e1": "R", "e5": "r"}).is_in_check() is False


def test_is_in_check_detects_cannon_with_screen() -> None:
    assert board_from({"e0": "K", "e3": "P", "e5": "c"}).is_in_check() is True
    # No screen, so the cannon does not attack the general.
    assert board_from({"e0": "K", "e5": "c"}).is_in_check() is False


def test_is_in_check_detects_horse_and_soldier() -> None:
    assert board_from({"e0": "K", "f2": "n"}).is_in_check() is True
    # Black soldier one step "ahead" of the Red general (Black advances downward).
    assert board_from({"e0": "K", "e1": "p"}).is_in_check() is True
    # Sideways attack: the soldier has crossed the river, so it also attacks d0.
    assert board_from({"d0": "K", "e0": "p"}).is_in_check() is True


def test_move_objects_compare_by_value() -> None:
    assert Move.from_iccs("h2e2") == Move(25, 22)
    assert len({Move.from_iccs("h2e2"), Move(25, 22)}) == 1
