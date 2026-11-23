"""Core value types shared by every part of the project.

This module is deliberately dependency-free: it defines colors, piece kinds and
piece codes, square helpers, moves, results, and the per-ply records that the
repetition rule needs. ``board``, ``movegen``, the bots, the harness, and the
server all speak these types, which keeps imports acyclic.

Square numbering is ``rank * 9 + file`` with rank 0 at Red's back rank (bottom)
and rank 9 at Black's back rank (top). File 0 is ``a`` (Red's left), file 8 is
``i``. That is the same coordinate system ICCS move strings use, for example
``h2e2``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum, StrEnum
from typing import Final

BOARD_FILES: Final = 9
BOARD_RANKS: Final = 10
BOARD_SQUARES: Final = BOARD_FILES * BOARD_RANKS

FILE_LETTERS: Final = "abcdefghi"

# Piece codes are small ints so the square array stays cheap to read and copy.
# Red pieces are 1..7, Black pieces are 9..15 (8 is unused). None is used for an
# empty square, which is represented by EMPTY = 0.
EMPTY: Final = 0
BLACK_PIECE_OFFSET: Final = 8
# Codes actually used are 0 (empty), 1..7 (Red) and 9..15 (Black); key tables are
# sized to the full range so indexing by code needs no translation.
PIECE_CODE_COUNT: Final = 16


class Color(Enum):
    """The two sides. Red moves first."""

    RED = "red"
    BLACK = "black"

    @property
    def opponent(self) -> Color:
        """The other side."""
        return Color.BLACK if self is Color.RED else Color.RED

    @property
    def forward(self) -> int:
        """Rank step of a forward move: Red advances, Black retreats."""
        return 1 if self is Color.RED else -1

    @property
    def sign(self) -> int:
        """+1 for Red, -1 for Black. Used with the "+1 win / 0 draw / -1 loss" utility."""
        return 1 if self is Color.RED else -1


class PieceKind(IntEnum):
    """The seven piece types. Values are also the Red piece codes."""

    GENERAL = 1
    ADVISOR = 2
    ELEPHANT = 3
    HORSE = 4
    CHARIOT = 5
    CANNON = 6
    SOLDIER = 7


FEN_TO_KIND_CHAR: Final[dict[str, PieceKind]] = {
    "k": PieceKind.GENERAL,
    "a": PieceKind.ADVISOR,
    "b": PieceKind.ELEPHANT,
    "n": PieceKind.HORSE,
    "r": PieceKind.CHARIOT,
    "c": PieceKind.CANNON,
    "p": PieceKind.SOLDIER,
}
KIND_TO_FEN_CHAR: Final[dict[PieceKind, str]] = {
    kind: char for char, kind in FEN_TO_KIND_CHAR.items()
}


# ---------------------------------------------------------------------------
# Piece codes
# ---------------------------------------------------------------------------


def make_piece(color: Color, kind: PieceKind) -> int:
    """Return the piece code for a colored piece, e.g. ``make_piece(Color.RED, HORSE) == 4``."""
    return int(kind) + (0 if color is Color.RED else BLACK_PIECE_OFFSET)


def piece_color(code: int) -> Color | None:
    """Return the color owning ``code``, or None for an empty square."""
    if code == EMPTY:
        return None
    return Color.RED if code < BLACK_PIECE_OFFSET else Color.BLACK


def piece_kind(code: int) -> PieceKind | None:
    """Return the piece kind of ``code``, or None for an empty square."""
    if code == EMPTY:
        return None
    return PieceKind(code) if code < BLACK_PIECE_OFFSET else PieceKind(code - BLACK_PIECE_OFFSET)


# ---------------------------------------------------------------------------
# Squares
# ---------------------------------------------------------------------------


def square(file: int, rank: int) -> int:
    """Convert file/rank to a square index."""
    return rank * BOARD_FILES + file


def file_of(square_index: int) -> int:
    """File (0..8) of a square index."""
    return square_index % BOARD_FILES


def rank_of(square_index: int) -> int:
    """Rank (0..9) of a square index."""
    return square_index // BOARD_FILES


def square_name(square_index: int) -> str:
    """ICCS name of a square, for example ``"e0"``."""
    return f"{FILE_LETTERS[file_of(square_index)]}{rank_of(square_index)}"


def parse_square(name: str) -> int:
    """Parse an ICCS square name such as ``"e0"`` into a square index."""
    if len(name) != 2 or name[0] not in FILE_LETTERS or not name[1].isdigit():
        raise ValueError(f"not an ICCS square: {name!r}")
    file = FILE_LETTERS.index(name[0])
    rank = int(name[1])
    if rank >= BOARD_RANKS:
        raise ValueError(f"rank out of range in {name!r}")
    return square(file, rank)


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------


class Move:
    """A single move: where it starts, where it lands, and what it captured.

    ``captured`` is the piece code that stood on the destination before the move
    (``EMPTY`` for a quiet move). Storing it on the move is what lets
    ``board.unmake_move`` restore the position in O(1).

    Moves are plain ``__slots__`` objects rather than dataclasses because they
    are created in the search hot loop; keep them immutable by convention.
    """

    __slots__ = ("from_square", "to_square", "captured")

    def __init__(self, from_square: int, to_square: int, captured: int = EMPTY) -> None:
        self.from_square = from_square
        self.to_square = to_square
        self.captured = captured

    def to_iccs(self) -> str:
        """Move as an ICCS string, for example ``"h2e2"``."""
        return square_name(self.from_square) + square_name(self.to_square)

    @classmethod
    def from_iccs(cls, text: str, captured: int = EMPTY) -> Move:
        """Parse ``"h2e2"``. ``captured`` must be filled in by the caller (the board)."""
        text = text.strip().lower()
        if len(text) != 4:
            raise ValueError(f"not an ICCS move: {text!r}")
        return cls(parse_square(text[:2]), parse_square(text[2:]), captured)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Move):
            return NotImplemented
        return (
            self.from_square == other.from_square
            and self.to_square == other.to_square
            and self.captured == other.captured
        )

    def __hash__(self) -> int:
        return hash((self.from_square, self.to_square, self.captured))

    def __repr__(self) -> str:
        return f"Move({self.to_iccs()!r})"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class Reason(StrEnum):
    """Why a game ended. Values are used verbatim in the JSON game schema.

    The engine only produces the first five. ``RESIGN`` is set by the server.
    """

    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    REPETITION = "repetition"
    PERPETUAL_CHECK = "perpetual_check"
    MOVE_CAP = "move_cap"
    RESIGN = "resign"


@dataclass(frozen=True)
class Result:
    """Outcome of a finished game: ``winner`` is None for a draw."""

    winner: Color | None
    reason: Reason

    @property
    def is_draw(self) -> bool:
        """True when nobody won."""
        return self.winner is None

    def utility(self, color: Color) -> int:
        """Utility for ``color``: +1 win, 0 draw, -1 loss."""
        if self.winner is None:
            return 0
        return 1 if self.winner is color else -1


# ---------------------------------------------------------------------------
# History records (repetition rule and unmake)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlyRecord:
    """What happened on one ply, enough to unmake the move and judge repetition.

    ``gave_check`` records whether the mover left the opponent in check; the
    perpetual-check rule needs it for every ply of the repeated cycle.
    """

    move: Move
    mover: Color
    gave_check: bool
    hash_before: int
    hash_after: int


@dataclass(frozen=True)
class PositionHistory:
    """The game so far, as the repetition rule sees it.

    ``initial_hash`` is the hash of the initial position and ``plies[i]`` is the
    i-th move played, so the sequence of positions is
    ``[initial_hash, plies[0].hash_after, plies[1].hash_after, ...]``.
    """

    initial_hash: int
    plies: list[PlyRecord]

    def hashes(self) -> list[int]:
        """All position hashes in order, starting with the initial position."""
        return [self.initial_hash, *(record.hash_after for record in self.plies)]
