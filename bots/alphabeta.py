"""Alpha-beta search with iterative deepening, move ordering, and a transposition table.

This module is the M3 milestone. It is built in the order AGENTS.md lists, and each
step is separable so the acceptance tests can compare them:

1. ``reference_minimax`` -- plain depth-limited negamax, no pruning. Slow on
   purpose; it is the oracle the fast search is checked against.
2. Alpha-beta (``_Search.negamax``), the same evaluation, fewer nodes.
3. Move ordering: transposition-table move, then MVV-LVA captures, then killers,
   then the history heuristic (``_Search._order``).
4. Iterative deepening with a deadline; the clock is checked every
   ``CLOCK_CHECK_INTERVAL`` nodes, not every node (``_Search.run``).
5. Transposition table keyed by the Zobrist hash, with exact/lower/upper bounds
   (``Bound``, ``_Search._store``, the probe at the top of ``negamax``).
6. Quiescence search on captures, behind a flag (``_Search.quiescence``).

Search rules (they must agree with ``engine/rules.py``):

* **No legal moves is a loss** for the side to move -- in Xiangqi checkmate and
  stalemate are both losses. The score is ``-MATE_SCORE + ply``, so a mate nearer
  the root is better than one further away and the search prefers faster wins.
* **A position already on the current search path is a draw (0).**

Terminology: ``ply`` is distance from the root (root = 0), ``depth`` is the
remaining full-width plies below this node, ``alpha`` is the best score the side to
move is already guaranteed and ``beta`` is the score at which the opponent does
better elsewhere. Scores are centipawns from the side to move's point of view.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntEnum

from bots.base import Bot, Budget, SearchResult
from engine.board import Board
from engine.types import Color, Move, make_piece
from eval.evaluator import build_evaluator
from eval.material import PIECE_VALUES, material_balance

# A forced win scores MATE_SCORE - ply, so the value of a forced win is always far
# above any heuristic score and "mate in 2" beats "mate in 4".
MATE_SCORE = 100_000
# Scores with an absolute value at or above this are treated as mate scores, which
# is what makes the transposition-table conversion below safe.
MATE_THRESHOLD = MATE_SCORE - 1_000
INFINITY = MATE_SCORE + 1

# Depth limits. MAX_PLY bounds the ply-indexed tables (killers) and the path.
MAX_PLY = 96
DEFAULT_DEPTH = 4

# Check the clock once every N nodes. Checking every node costs a function call per
# node for no benefit; checking too rarely overshoots the budget. At roughly 12k
# nodes/s a node costs ~85 us, so 256 nodes bounds the overshoot to about 25 ms.
CLOCK_CHECK_INTERVAL = 256

# The transposition table is a plain dict; cap it so a long game cannot grow
# without bound. When full, new positions are simply not stored.
TT_MAX_ENTRIES = 200_000

# Ordering scores. The tiers are far apart so a transposition-table move always
# comes first, then captures, then quiet moves.
ORDER_TT_MOVE = 1_000_000
ORDER_CAPTURE = 100_000
ORDER_KILLER_1 = 90_000
ORDER_KILLER_2 = 80_000
ORDER_HISTORY_CAP = 70_000
MVV_LVA_WEIGHT = 16  # captured value * 16 - attacker value, kept below ORDER_CAPTURE


class _SearchTimeout(Exception):
    """Raised inside the search when the deadline passes; caught by ``run``."""


class Bound(IntEnum):
    """What a transposition-table score means.

    EXACT: the true value of the position at that depth.
    LOWER: a fail-high -- the value is at least this (the position was good enough
           that the parent stopped looking).
    UPPER: a fail-low -- the value is at most this (every move was worse than the
           window the parent was interested in).
    """

    EXACT = 0
    LOWER = 1
    UPPER = 2


@dataclass
class TTEntry:
    """One transposition-table record: depth, value, bound type, and best move."""

    depth: int
    value: int
    bound: Bound
    best_move: Move | None


@dataclass(frozen=True)
class FixedDepthResult:
    """Outcome of a fixed-depth search, for the node-count tests and tools."""

    value: int
    nodes: int
    depth: int
    elapsed_ms: float


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


class _Search:
    """One alpha-beta search over a mutable board.

    A fresh instance is cheap; the transposition table is the only expensive part
    and it is created empty. The board is restored exactly when the search ends,
    even if the deadline interrupted it (every make is in a ``try/finally``).
    """

    def __init__(
        self,
        board: Board,
        evaluator: Callable[[Board], int] = material_balance,
        *,
        use_ordering: bool = True,
        use_tt: bool = True,
        quiescence: bool = False,
        time_source: Callable[[], float] = time.perf_counter,
        deadline: float | None = None,
    ) -> None:
        self.board = board
        self.evaluator = evaluator
        self.use_ordering = use_ordering
        self.use_tt = use_tt
        self.use_quiescence = quiescence
        self.time_source = time_source
        self.deadline = deadline
        self.tt: dict[int, TTEntry] = {}
        self.killers: list[list[Move | None]] = [[None, None] for _ in range(MAX_PLY)]
        self.history: dict[tuple[int, int], int] = {}
        self.nodes = 0
        # Hashes on the current path, with a count so two identical positions with
        # the same hash cannot bookkeep each other away.
        self.path: dict[int, int] = {}
        # Incremented every time a repetition draw is scored. A subtree that contains
        # one has a path-dependent value, so it must not go into the table.
        self._path_dependent = 0

    # -- entry points --------------------------------------------------

    def run(self, max_depth: int) -> tuple[Move | None, int, int]:
        """Iterative deepening: search 1, 2, 3, ... plies until depth or time runs out.

        Returns ``(best_move, score, completed_depth)``. If a deeper iteration is
        interrupted, the best move from the last *completed* depth is returned; if
        even depth 1 does not finish, the first ordered legal move is used.
        """
        board = self.board
        legal = board.legal_moves()
        if not legal:
            return None, -MATE_SCORE, 0

        best_move = self._order(legal, 0, None)[0]
        best_value = 0
        completed_depth = 0
        # The root counts as being on the path: returning to it later is a draw.
        self._push(board.hash)
        for depth in range(1, max_depth + 1):
            try:
                value, move = self._search_root(depth)
            except _SearchTimeout:
                break
            best_move, best_value, completed_depth = move, value, depth
            # A proven mate cannot be improved by searching deeper.
            if abs(value) >= MATE_THRESHOLD:
                break
        self.path.clear()
        return best_move, best_value, completed_depth

    def seed_path_from_history(self) -> None:
        """Put the game's played positions on the search path.

        Without this, a search of depth 3 cannot see a four-ply repetition cycle and
        will happily keep repeating checks in a won position; the game then ends by
        the perpetual-check rule and a win becomes a loss. Seeding the path makes
        those lines score 0 (a draw), so the search looks for progress instead.
        """
        for position_hash in self.board.history().hashes():
            self.path[position_hash] = self.path.get(position_hash, 0) + 1

    def _search_root(self, depth: int) -> tuple[int, Move]:
        """Search the root, returning the value and the move that achieves it."""
        board = self.board
        entry = self.tt.get(board.hash) if self.use_tt else None
        moves = self._order(board.legal_moves(), 0, entry.best_move if entry else None)

        # The root uses a full window (beta = +inf), so no fail-high can happen here
        # and the resulting score is exact.
        alpha = -INFINITY
        beta = INFINITY
        best_value = -INFINITY
        best_move = moves[0]
        dependent_before = self._path_dependent

        for move in moves:
            board.make_move(move, record_check=False)
            self._push(board.hash)
            try:
                value = -self.negamax(depth - 1, -beta, -alpha, 1)
            finally:
                self._pop(board.hash)
                board.unmake_move()
            if value > best_value:
                best_value, best_move = value, move
            if value > alpha:
                alpha = value

        if self.use_tt and self._path_dependent == dependent_before:
            self._store(board.hash, depth, best_value, Bound.EXACT, best_move, 0)
        return best_value, best_move

    # -- negamax -------------------------------------------------------

    def negamax(self, depth: int, alpha: int, beta: int, ply: int) -> int:
        """Score the position for the side to move with an alpha-beta window."""
        self._tick()
        board = self.board

        # 1. Repetition. The path holds every position from the root down to here
        #    (the parent pushes a child before recursing), so a hash that appears
        #    more than once means the current position has already occurred: a draw.
        #    The bot seeds the path with the game's own history before searching, so
        #    returning to a position played earlier in the game also scores 0.
        #    This must come before the transposition probe, because the score
        #    depends on the path and a table entry from another path would be wrong.
        #
        #    NOTE: repetition is scored as an immediate DRAW and nothing else. The
        #    "perpetual check loses" rule is not modelled inside the search, so a
        #    line in which we check forever is seen as a draw even though the real
        #    game would award it to the opponent; engine/rules.py adjudicates that at
        #    the end of the ply. Because the score depends on the path, any subtree
        #    that contained a repetition is marked path-dependent and is never stored
        #    in the transposition table (see step 6).
        if ply > 0 and self.path.get(board.hash, 0) > 1:
            self._path_dependent += 1
            return 0

        # 2. Transposition probe. A hit is only usable if it was searched at least
        #    as deep as we are about to search; a shallower hit still narrows the
        #    window when its bound allows it.
        entry = self.tt.get(board.hash) if self.use_tt else None
        if entry is not None and entry.depth >= depth:
            value = _score_from_table(entry.value, ply)
            if entry.bound is Bound.EXACT:
                return value
            if entry.bound is Bound.LOWER:
                # The position was good enough to cause a cutoff before: it is a
                # lower bound, so raise alpha and cut if the window is already full.
                if value >= beta:
                    return value
                if value > alpha:
                    alpha = value
            else:  # UPPER
                if value <= alpha:
                    return value
                if value < beta:
                    beta = value

        # 3. Terminal test. Every node needs its legal moves anyway; if there are
        #    none the side to move has lost, whether it is checkmate or stalemate.
        moves = board.legal_moves()
        if not moves:
            return -MATE_SCORE + ply

        # 4. Horizon. Evaluate, or extend the search on captures first.
        if depth <= 0:
            if self.use_quiescence:
                return self.quiescence(alpha, beta, ply)
            return self.evaluator(board)

        if ply >= MAX_PLY - 1:
            return self.evaluator(board)

        # 5. Order the moves so the most promising are tried first. Good ordering is
        #    what makes the cutoffs happen early and the node count small.
        tt_move = entry.best_move if entry is not None else None
        moves = self._order(moves, ply, tt_move)

        alpha_original = alpha
        best_value = -INFINITY
        best_move = moves[0]
        dependent_before = self._path_dependent

        for move in moves:
            board.make_move(move, record_check=False)
            self._push(board.hash)
            try:
                # Negamax: the child is scored for the opponent, so negate it and
                # swap the window (the opponent's alpha is our beta).
                value = -self.negamax(depth - 1, -beta, -alpha, ply + 1)
            finally:
                self._pop(board.hash)
                board.unmake_move()

            if value > best_value:
                best_value, best_move = value, move
            if value > alpha:
                alpha = value
            if alpha >= beta:
                # Cutoff: the side to move already has a move that is so good the
                # opponent would never allow this position. The exact value does not
                # matter, only that it is at least beta, so report a LOWER bound.
                self._record_cutoff(move, ply, depth)
                break

        # 6. Store the result, unless a repetition draw inside this subtree made the
        #    value depend on the path it was reached by.
        if self.use_tt and self._path_dependent == dependent_before:
            if best_value >= beta:
                bound = Bound.LOWER
            elif best_value > alpha_original:
                bound = Bound.EXACT
            else:
                bound = Bound.UPPER
            self._store(board.hash, depth, best_value, bound, best_move, ply)
        return best_value

    # -- quiescence ----------------------------------------------------

    def quiescence(self, alpha: int, beta: int, ply: int) -> int:
        """Search captures only, so the evaluation is not stopped in the middle of a trade.

        ``stand_pat`` is the score if the side to move simply declines to capture;
        that is a lower bound on its value, which is why the "stand pat" cutoff is
        safe. Captures are ordered MVV-LVA so the most promising are tried first.
        """
        self._tick()
        board = self.board
        legal = board.legal_moves()
        if not legal:
            return -MATE_SCORE + ply
        if ply >= MAX_PLY - 1:
            return self.evaluator(board)

        stand_pat = self.evaluator(board)
        if stand_pat >= beta:
            return stand_pat
        if stand_pat > alpha:
            alpha = stand_pat

        captures = [move for move in legal if move.captured]
        if not captures:
            return stand_pat
        captures.sort(key=self._capture_key, reverse=True)

        best_value = stand_pat
        for move in captures:
            board.make_move(move, record_check=False)
            try:
                value = -self.quiescence(-beta, -alpha, ply + 1)
            finally:
                board.unmake_move()
            if value > best_value:
                best_value = value
            if value > alpha:
                alpha = value
            if alpha >= beta:
                break
        return best_value

    # -- move ordering -------------------------------------------------

    def _order(self, moves: Iterable[Move], ply: int, tt_move: Move | None) -> list[Move]:
        """Sort moves by expected value: table move, captures, killers, history.

        The four tiers are separated by constant gaps so a cheap heuristic can never
        outrank a more reliable one. With ``use_ordering`` off the moves keep the
        generator's order, which is how the node-count comparison measures the
        ordering's contribution.
        """
        ordered = list(moves)
        if not self.use_ordering:
            return ordered
        killers = self.killers[ply]
        history = self.history

        def key(move: Move) -> int:
            if tt_move is not None and move == tt_move:
                return ORDER_TT_MOVE
            if move.captured:
                return ORDER_CAPTURE + self._capture_key(move)
            if move == killers[0]:
                return ORDER_KILLER_1
            if move == killers[1]:
                return ORDER_KILLER_2
            # Quiet move that caused cutoffs before: history heuristic, capped so it
            # can never climb into the killer tier.
            return min(history.get((move.from_square, move.to_square), 0), ORDER_HISTORY_CAP)

        ordered.sort(key=key, reverse=True)
        return ordered

    def _record_cutoff(self, move: Move, ply: int, depth: int) -> None:
        """Remember the quiet move that caused a cutoff: killer slot and history."""
        if move.captured:
            return
        slot = self.killers[ply]
        if slot[0] != move:
            slot[1] = slot[0]
            slot[0] = move
        key = (move.from_square, move.to_square)
        # Deeper cutoffs are more meaningful, hence depth * depth.
        self.history[key] = self.history.get(key, 0) + depth * depth

    # -- transposition table -------------------------------------------

    def _store(
        self,
        hash_value: int,
        depth: int,
        value: int,
        bound: Bound,
        best_move: Move | None,
        ply: int,
    ) -> None:
        """Save a position, converting mate scores to be relative to this node."""
        if depth <= 0 or len(self.tt) >= TT_MAX_ENTRIES:
            return
        self.tt[hash_value] = TTEntry(depth, _score_to_table(value, ply), bound, best_move)

    # -- bookkeeping ---------------------------------------------------

    def _tick(self) -> None:
        """Count a node and, every CLOCK_CHECK_INTERVAL nodes, look at the clock."""
        self.nodes += 1
        if self.deadline is not None and self.nodes % CLOCK_CHECK_INTERVAL == 0:
            if self.time_source() >= self.deadline:
                raise _SearchTimeout

    def _push(self, hash_value: int) -> None:
        self.path[hash_value] = self.path.get(hash_value, 0) + 1

    def _pop(self, hash_value: int) -> None:
        remaining = self.path[hash_value] - 1
        if remaining:
            self.path[hash_value] = remaining
        else:
            del self.path[hash_value]

    def _capture_key(self, move: Move) -> int:
        """MVV-LVA ordering key: a big victim taken by a cheap attacker scores highest."""
        victim = _ORDER_VALUE.get(move.captured, 0)
        attacker = _ORDER_VALUE.get(self.board.piece_at(move.from_square), 0)
        return victim * MVV_LVA_WEIGHT - attacker


# ---------------------------------------------------------------------------
# Score conversions for the transposition table
# ---------------------------------------------------------------------------


# Victim/attacker values for move ordering only; derived from the real material
# values so ordering and evaluation never disagree about which piece is worth more.
_ORDER_VALUE: dict[int, int] = {}
for _kind, _value in PIECE_VALUES.items():
    for _color in Color:
        _ORDER_VALUE[make_piece(_color, _kind)] = _value


def _score_to_table(value: int, ply: int) -> int:
    """Convert a root-relative mate score to a node-relative one for storage.

    A score of ``MATE_SCORE - total_plies`` stored at ``ply`` must read as "mate in
    ``total_plies - ply`` plies from here", which is ``value + ply``. Non-mate scores
    are stored unchanged.
    """
    if value >= MATE_THRESHOLD:
        return value + ply
    if value <= -MATE_THRESHOLD:
        return value - ply
    return value


def _score_from_table(value: int, ply: int) -> int:
    """Inverse of ``_score_to_table``: node-relative mate score -> root-relative."""
    if value >= MATE_THRESHOLD:
        return value - ply
    if value <= -MATE_THRESHOLD:
        return value + ply
    return value


# ---------------------------------------------------------------------------
# Reference implementation and fixed-depth helper
# ---------------------------------------------------------------------------


def reference_minimax(
    board: Board,
    depth: int,
    path: dict[int, int] | None = None,
    ply: int = 0,
    counter: list[int] | None = None,
) -> int:
    """Plain depth-limited negamax: no pruning, no ordering, no table.

    Kept beside the fast search because it is the oracle the pruning has to match.
    It shares the evaluation, the terminal rule, and the path-repetition rule, so
    any difference in a root value is a bug in the fast search.

    ``counter`` is an optional one-element list used to count nodes in tests.
    """
    if path is None:
        path = {board.hash: 1}
    if counter is not None:
        counter[0] += 1

    # Same convention as the fast search: more than one occurrence on the path
    # means the current position is a repetition.
    if ply > 0 and path.get(board.hash, 0) > 1:
        return 0

    moves = board.legal_moves()
    if not moves:
        return -MATE_SCORE + ply
    if depth <= 0:
        return material_balance(board)

    best_value = -INFINITY
    for move in moves:
        board.make_move(move, record_check=False)
        path[board.hash] = path.get(board.hash, 0) + 1
        try:
            value = -reference_minimax(board, depth - 1, path, ply + 1, counter)
        finally:
            if path[board.hash] > 1:
                path[board.hash] -= 1
            else:
                del path[board.hash]
            board.unmake_move()
        if value > best_value:
            best_value = value
    return best_value


def search_at_depth(
    board: Board,
    depth: int,
    *,
    quiescence: bool = False,
    use_ordering: bool = True,
    use_tt: bool = True,
    evaluator: str = "material",
) -> FixedDepthResult:
    """Run one fixed-depth alpha-beta search and report its value and node count.

    Used by the node-count tests and ``tools/search_stats.py``. The board is left
    unchanged.
    """
    started = time.perf_counter()
    search = _Search(
        board,
        build_evaluator(evaluator),
        use_ordering=use_ordering,
        use_tt=use_tt,
        quiescence=quiescence,
    )
    search.seed_path_from_history()
    search.path[board.hash] = search.path.get(board.hash, 0) + 1
    try:
        value = search.negamax(depth, -INFINITY, INFINITY, 0)
    finally:
        search.path.clear()
    return FixedDepthResult(
        value=value,
        nodes=search.nodes,
        depth=depth,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


# ---------------------------------------------------------------------------
# The bot
# ---------------------------------------------------------------------------


class AlphaBetaBot(Bot):
    """Alpha-beta bot with iterative deepening.

    Parameters (the ones the server registry exposes):

    * ``depth``: maximum depth for iterative deepening; None means "time only".
    * ``time_limit_ms``: soft deadline for a move; None means "depth only".
    * ``evaluator``: name from ``eval.evaluator`` (M3 only has ``material``).
    * ``quiescence``: extend the search on captures at the horizon.

    ``use_ordering`` and ``use_tt`` exist so the M3 tests can measure those
    enhancements separately; leave them on in play.
    """

    type_name = "alphabeta"

    def __init__(
        self,
        depth: int | None = None,
        time_limit_ms: float | None = None,
        evaluator: str = "material",
        quiescence: bool = False,
        use_ordering: bool = True,
        use_tt: bool = True,
        time_source: Callable[[], float] = time.perf_counter,
        **params: object,
    ) -> None:
        super().__init__(
            {
                "depth": depth,
                "time_limit_ms": time_limit_ms,
                "evaluator": evaluator,
                "quiescence": quiescence,
            }
        )
        self.depth = depth
        self.time_limit_ms = time_limit_ms
        self.evaluator_name = evaluator
        self.use_quiescence = quiescence
        self.use_ordering = use_ordering
        self.use_tt = use_tt
        # Injectable so tests can drive the deadline deterministically.
        self.time_source = time_source
        # Build once: an unknown evaluator name should fail at construction, not
        # after the game has started.
        self._evaluator = build_evaluator(evaluator)
        # Reuse the table across moves of a game: earlier move results are still
        # valid transpositions.
        self._search: _Search | None = None

    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        """Search within the configured and requested limits and return a move."""
        max_depth, time_limit_ms = self._limits(budget)
        started = self.time_source()
        deadline = None if time_limit_ms is None else started + time_limit_ms / 1000.0

        search = _Search(
            board,
            self._evaluator,
            use_ordering=self.use_ordering,
            use_tt=self.use_tt,
            quiescence=self.use_quiescence,
            time_source=self.time_source,
            deadline=deadline,
        )
        if self._search is not None:
            # Carry over the transposition table and history between moves.
            search.tt = self._search.tt
            search.history = self._search.history
        self._search = search
        # Treat positions already played in the game as repetition, so a shallow
        # search does not walk into a perpetual check it cannot see (see the NOTE
        # in negamax).
        search.seed_path_from_history()

        move, value, completed_depth = search.run(max_depth)
        elapsed_ms = (self.time_source() - started) * 1000.0
        if move is None:
            raise ValueError("no legal moves: the game is already over")
        return SearchResult(
            move=move,
            score=value,
            depth=completed_depth,
            nodes=search.nodes,
            elapsed_ms=elapsed_ms,
        )

    def _limits(self, budget: Budget) -> tuple[int, float | None]:
        """Combine the bot's settings with the caller's budget, taking the stricter."""
        depth = self.depth
        if budget.depth is not None:
            depth = budget.depth if depth is None else min(depth, budget.depth)

        time_limit_ms = self.time_limit_ms
        if budget.time_limit_ms is not None:
            if time_limit_ms is None:
                time_limit_ms = budget.time_limit_ms
            else:
                time_limit_ms = min(time_limit_ms, budget.time_limit_ms)

        if depth is None and time_limit_ms is None:
            depth = DEFAULT_DEPTH
        return (MAX_PLY if depth is None else depth), time_limit_ms
