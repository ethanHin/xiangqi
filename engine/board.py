"""Board state, FEN parsing, make/unmake, and attack detection.

The board is a flat list of 90 piece codes (see ``engine.types``) indexed by
``rank * 9 + file``. Rank 0 is Red's back rank, rank 9 is Black's back rank.

Design notes:

* ``make_move`` / ``unmake_move`` mutate the board in place and push/pop a
  ``PlyRecord``. Search therefore never copies a board per node; a copy is only
  ever made by ``Board.start()`` / ``from_fen``.
* ``make_move`` does not check legality. It asserts that the move is
  pseudo-legal (a piece of the side to move is on ``from_square`` and
  ``move.captured`` matches the destination). ``movegen.generate_legal_moves``
  and the server's move validation apply the "own general must be safe" test.
* The Zobrist hash is updated incrementally and restored exactly on unmake.
"""

from __future__ import annotations

from engine import movegen, zobrist
from engine.types import (
    BOARD_FILES,
    BOARD_RANKS,
    BOARD_SQUARES,
    EMPTY,
    FEN_TO_KIND_CHAR,
    FILE_LETTERS,
    KIND_TO_FEN_CHAR,
    Color,
    Move,
    PieceKind,
    PlyRecord,
    PositionHistory,
    file_of,
    make_piece,
    piece_color,
    piece_kind,
    rank_of,
    square,
)

START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"

# Rank/file steps for the four orthogonal directions, used by the attack scans.
ORTHOGONAL_STEPS = ((0, 1), (1, 0), (0, -1), (-1, 0))
# Diagonal steps, used to find adjacent advisors and two-away elephants.
DIAGONAL_STEPS = ((1, 1), (1, -1), (-1, 1), (-1, -1))
# Offsets of the squares a horse could attack ``target`` from, plus how to find
# the horse's leg for that offset (see ``is_square_attacked``).
HORSE_ATTACK_OFFSETS = ((1, 2), (-1, 2), (1, -2), (-1, -2), (2, 1), (2, -1), (-2, 1), (-2, -1))

# A Red soldier attacks sideways only from rank 5 and up; a Black soldier only
# from rank 4 and down. Those are the ranks at which each side has crossed the river.
RED_SIDEWAYS_MIN_RANK = 5
BLACK_SIDEWAYS_MAX_RANK = 4

_GENERAL_INDEX = {Color.RED: 0, Color.BLACK: 1}


class Board:
    """A mutable Xiangqi position plus the move stack needed to undo it.

    Create one with ``Board.start()`` or ``Board.from_fen(fen)``. The public API
    is the one documented in ``AGENTS.md``; the square array (``squares``) is
    exposed only for move generation and tests.
    """

    __slots__ = (
        "squares",
        "side_to_move",
        "hash",
        "initial_hash",
        "records",
        "general_squares",
    )

    def __init__(self, squares: list[int], side_to_move: Color) -> None:
        self.squares = squares
        self.side_to_move = side_to_move
        self.records: list[PlyRecord] = []
        self.general_squares = [
            self._find_general(Color.RED),
            self._find_general(Color.BLACK),
        ]
        self.initial_hash = self._compute_hash()
        self.hash = self.initial_hash

    # ------------------------------------------------------------------
    # Construction and serialisation
    # ------------------------------------------------------------------

    @classmethod
    def start(cls) -> Board:
        """The standard starting position with Red to move."""
        return cls.from_fen(START_FEN)

    @classmethod
    def from_fen(cls, fen: str) -> Board:
        """Parse a FEN string.

        Only the piece placement and the side-to-move field are meaningful in
        Xiangqi; the remaining fields (castling, en passant, clocks) are parsed
        leniently and ignored. ``to_fen`` always writes them as ``- - 0 1``.
        """
        fields = fen.split()
        if not fields:
            raise ValueError("empty FEN")
        placement = fields[0]
        if len(fields) > 1 and fields[1] not in ("w", "b"):
            raise ValueError(f"bad side-to-move field: {fields[1]!r}")
        side_to_move = Color.BLACK if len(fields) > 1 and fields[1] == "b" else Color.RED

        ranks = placement.split("/")
        if len(ranks) != BOARD_RANKS:
            raise ValueError(f"FEN must have {BOARD_RANKS} ranks, got {len(ranks)}")

        squares = [EMPTY] * BOARD_SQUARES
        # FEN lists Black's back rank first, so the first field is rank 9.
        for fen_rank, row in enumerate(ranks):
            rank = BOARD_RANKS - 1 - fen_rank
            file = 0
            for char in row:
                if char.isdigit():
                    file += int(char)
                    continue
                kind = FEN_TO_KIND_CHAR.get(char.lower())
                if kind is None:
                    raise ValueError(f"unknown piece character {char!r}")
                color = Color.RED if char.isupper() else Color.BLACK
                if file >= BOARD_FILES:
                    raise ValueError(f"rank {rank} has too many squares in FEN")
                squares[square(file, rank)] = make_piece(color, kind)
                file += 1
            if file != BOARD_FILES:
                raise ValueError(f"rank {rank} describes {file} squares, expected {BOARD_FILES}")
        return cls(squares, side_to_move)

    def to_fen(self) -> str:
        """Serialise the current position (placement and side to move) as FEN."""
        rows = []
        for rank in range(BOARD_RANKS - 1, -1, -1):
            row: list[str] = []
            empty_run = 0
            for file in range(BOARD_FILES):
                code = self.squares[square(file, rank)]
                if code == EMPTY:
                    empty_run += 1
                    continue
                if empty_run:
                    row.append(str(empty_run))
                    empty_run = 0
                char = KIND_TO_FEN_CHAR[piece_kind(code)]  # type: ignore[index]
                row.append(char.upper() if piece_color(code) is Color.RED else char)
            if empty_run:
                row.append(str(empty_run))
            rows.append("".join(row))
        side = "w" if self.side_to_move is Color.RED else "b"
        return "/".join(rows) + f" {side} - - 0 1"

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def legal_moves(self) -> list[Move]:
        """All legal moves for the side to move (own general left safe)."""
        return movegen.generate_legal_moves(self)

    @property
    def ply_count(self) -> int:
        """Number of plies played from the initial position of this board."""
        return len(self.records)

    def history(self) -> PositionHistory:
        """A snapshot of the game so far, for ``rules.repetition_result``."""
        return PositionHistory(initial_hash=self.initial_hash, plies=list(self.records))

    def piece_at(self, square_index: int) -> int:
        """Piece code on a square (``EMPTY`` for an empty square)."""
        return self.squares[square_index]

    def general_square(self, color: Color) -> int:
        """Square of ``color``'s general, or -1 if it is missing (test positions only)."""
        return self.general_squares[_GENERAL_INDEX[color]]

    def is_in_check(self, color: Color | None = None) -> bool:
        """True if ``color``'s general is attacked. Defaults to the side to move.

        Includes the flying-general rule, because a general facing the enemy
        general on an open file *is* an attacked general.
        """
        if color is None:
            color = self.side_to_move
        general = self.general_squares[_GENERAL_INDEX[color]]
        if general < 0:
            # No general on the board means the position is already lost; that can
            # only happen in hand-written test positions.
            return True
        return self.is_square_attacked(general, color.opponent)

    def is_square_attacked(self, target: int, by_color: Color) -> bool:
        """True if any piece of ``by_color`` attacks ``target``.

        The scan starts at ``target`` and looks outward, which is much cheaper
        than generating the opponent's moves: rays stop at the first blocker,
        horses are found by their offsets, and soldiers/advisors/elephants sit on
        fixed nearby squares.
        """
        target_file = file_of(target)
        target_rank = rank_of(target)

        if self._attacked_by_slider(target_file, target_rank, by_color):
            return True
        if self._attacked_by_horse(target_file, target_rank, by_color):
            return True
        if self._attacked_by_soldier(target_file, target_rank, by_color):
            return True
        return self._attacked_by_short_range(target_file, target_rank, by_color)

    # ------------------------------------------------------------------
    # Making and unmaking moves
    # ------------------------------------------------------------------

    def make_move(self, move: Move, record_check: bool = True) -> None:
        """Play ``move`` on this board.

        ``record_check`` decides whether the ply record stores whether the move
        gave check, which the perpetual-check rule needs. Game loops keep the
        default (True); the search passes False and saves one attack scan per
        node, because it never asks for the repetition rule on internal nodes.
        """
        mover = self.side_to_move
        piece = self.squares[move.from_square]
        captured = move.captured

        # Pseudo-legality only: legality means "my general is safe afterwards" and
        # is the caller's job. Catching mistakes here keeps perft honest.
        assert piece != EMPTY, "no piece on the origin square"
        assert piece_color(piece) is mover, "move plays an opponent piece"
        assert captured == self.squares[move.to_square], "captured piece does not match the board"

        hash_before = self.hash
        self.squares[move.to_square] = piece
        self.squares[move.from_square] = EMPTY

        # Zobrist update: remove the piece from its old square, put it on the new
        # one, remove whatever was captured, then flip the side-to-move key.
        new_hash = (
            hash_before
            ^ zobrist.PIECE_KEYS[piece][move.from_square]
            ^ zobrist.PIECE_KEYS[piece][move.to_square]
            ^ zobrist.SIDE_KEY
        )
        if captured != EMPTY:
            new_hash ^= zobrist.PIECE_KEYS[captured][move.to_square]
        self.hash = new_hash

        if piece_kind(piece) is PieceKind.GENERAL:
            self.general_squares[_GENERAL_INDEX[mover]] = move.to_square
        elif captured != EMPTY and piece_kind(captured) is PieceKind.GENERAL:
            # Only reachable from hand-written positions; real play never allows it.
            self.general_squares[_GENERAL_INDEX[mover.opponent]] = -1

        self.side_to_move = mover.opponent
        gave_check = self.is_in_check(mover.opponent) if record_check else False
        self.records.append(
            PlyRecord(
                move=move,
                mover=mover,
                gave_check=gave_check,
                hash_before=hash_before,
                hash_after=self.hash,
            )
        )

    def unmake_move(self) -> Move:
        """Undo the last move and return it. Restores squares, hash, and side to move exactly."""
        record = self.records.pop()
        move = record.move
        piece = self.squares[move.to_square]

        self.squares[move.from_square] = piece
        self.squares[move.to_square] = move.captured
        self.side_to_move = record.mover
        self.hash = record.hash_before

        if piece_kind(piece) is PieceKind.GENERAL:
            self.general_squares[_GENERAL_INDEX[record.mover]] = move.from_square
        elif move.captured != EMPTY and piece_kind(move.captured) is PieceKind.GENERAL:
            self.general_squares[_GENERAL_INDEX[record.mover.opponent]] = move.to_square
        return move

    def gives_check(self, move: Move) -> bool:
        """True if ``move`` would leave the opponent in check (does not change the board)."""
        mover = self.side_to_move
        self.make_move(move, record_check=False)
        in_check = self.is_in_check(mover.opponent)
        self.unmake_move()
        return in_check

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_general(self, color: Color) -> int:
        """Locate ``color``'s general, or -1 if there is none."""
        general = make_piece(color, PieceKind.GENERAL)
        for index, code in enumerate(self.squares):
            if code == general:
                return index
        return -1

    def _compute_hash(self) -> int:
        """Full Zobrist hash from scratch (only used when the board is created)."""
        value = 0
        for index, code in enumerate(self.squares):
            if code != EMPTY:
                value ^= zobrist.PIECE_KEYS[code][index]
        if self.side_to_move is Color.BLACK:
            value ^= zobrist.SIDE_KEY
        return value

    def _attacked_by_slider(self, target_file: int, target_rank: int, by_color: Color) -> bool:
        """Chariots, cannons, and the flying general, scanned along the four rays.

        The first piece on a ray can be a chariot (direct attack) or a general
        (adjacent attack, or the flying-general rule when the ray is vertical).
        The second piece on the ray can only be a cannon, because a cannon
        captures by jumping exactly one screen.
        """
        squares = self.squares
        for df, dr in ORTHOGONAL_STEPS:
            file = target_file + df
            rank = target_rank + dr
            screen_seen = False
            while 0 <= file < BOARD_FILES and 0 <= rank < BOARD_RANKS:
                code = squares[square(file, rank)]
                if code != EMPTY:
                    if screen_seen:
                        if piece_color(code) is by_color and piece_kind(code) is PieceKind.CANNON:
                            return True
                        break
                    if piece_color(code) is by_color:
                        kind = piece_kind(code)
                        if kind is PieceKind.CHARIOT:
                            return True
                        # Flying general: only along a file. An adjacent general also
                        # attacks one step sideways, which the distance test covers.
                        if kind is PieceKind.GENERAL and (
                            df == 0 or abs(file - target_file) + abs(rank - target_rank) == 1
                        ):
                            return True
                    screen_seen = True
                file += df
                rank += dr
        return False

    def _attacked_by_horse(self, target_file: int, target_rank: int, by_color: Color) -> bool:
        """Horses two-plus-one steps away whose leg is not blocked."""
        squares = self.squares
        for df, dr in HORSE_ATTACK_OFFSETS:
            file = target_file + df
            rank = target_rank + dr
            if not (0 <= file < BOARD_FILES and 0 <= rank < BOARD_RANKS):
                continue
            code = squares[square(file, rank)]
            if piece_color(code) is not by_color or piece_kind(code) is not PieceKind.HORSE:
                continue
            # Horse leg rule: the leg is the orthogonal square next to the horse,
            # in the direction of its long step toward the target.
            if abs(dr) == 2:
                leg_file, leg_rank = file, rank - (1 if dr > 0 else -1)
            else:
                leg_file, leg_rank = file - (1 if df > 0 else -1), rank
            if squares[square(leg_file, leg_rank)] == EMPTY:
                return True
        return False

    def _attacked_by_soldier(self, target_file: int, target_rank: int, by_color: Color) -> bool:
        """An enemy soldier one step ahead of the target, or beside it once across the river."""
        squares = self.squares
        ahead = target_rank - by_color.forward
        if 0 <= ahead < BOARD_RANKS:
            code = squares[square(target_file, ahead)]
            if piece_color(code) is by_color and piece_kind(code) is PieceKind.SOLDIER:
                return True
        # Sideways attacks are legal only after the river. The attacker's rank is
        # the target's rank, so the crossing test can use that rank directly.
        if by_color is Color.RED:
            crossed = target_rank >= RED_SIDEWAYS_MIN_RANK
        else:
            crossed = target_rank <= BLACK_SIDEWAYS_MAX_RANK
        if not crossed:
            return False
        for df in (-1, 1):
            file = target_file + df
            if not 0 <= file < BOARD_FILES:
                continue
            code = squares[square(file, target_rank)]
            if piece_color(code) is by_color and piece_kind(code) is PieceKind.SOLDIER:
                return True
        return False

    def _attacked_by_short_range(self, target_file: int, target_rank: int, by_color: Color) -> bool:
        """Adjacent advisors and two-away elephants.

        In legal play these can never attack the enemy general (both are confined
        to their own half), but including them keeps ``is_square_attacked``
        correct for hand-written test positions and for future use.
        """
        squares = self.squares
        for df, dr in DIAGONAL_STEPS:
            file = target_file + df
            rank = target_rank + dr
            if 0 <= file < BOARD_FILES and 0 <= rank < BOARD_RANKS:
                code = squares[square(file, rank)]
                if piece_color(code) is by_color and piece_kind(code) is PieceKind.ADVISOR:
                    return True
            file = target_file + 2 * df
            rank = target_rank + 2 * dr
            if not (0 <= file < BOARD_FILES and 0 <= rank < BOARD_RANKS):
                continue
            code = squares[square(file, rank)]
            if piece_color(code) is not by_color or piece_kind(code) is not PieceKind.ELEPHANT:
                continue
            # Elephant eye rule: the square one step diagonally in between must be empty.
            if squares[square(target_file + df, target_rank + dr)] == EMPTY:
                return True
        return False


# Re-exported for callers that only import ``board``; the canonical definitions
# live in ``engine.types``.
__all__ = ["Board", "START_FEN", "FILE_LETTERS", "square", "file_of", "rank_of"]
