"""Plain MCTS with UCT (M5).

The four phases are one method each, in the order the algorithm runs them:

* ``_select``       -- walk down the tree choosing the child with the highest UCT
                       score until a node that is not fully expanded (or a terminal
                       node) is reached;
* ``_expand``       -- add one child for a random untried move and record whether
                       the resulting position is already over;
* ``_rollout``      -- play random legal moves from the new child for up to
                       ``rollout_depth`` plies, then evaluate (squashed);
* ``_backpropagate``-- add the reward to every node on the path, flipping the
                       perspective at every edge.

Value convention (the part that silently breaks MCTS if it is wrong): a node stores
the sum of rewards **for the player who moved into it**. That is the same player who
chooses at its parent, so selection can maximise ``child.mean_value`` directly, and
backpropagation alternates ``reward -> 1 - reward`` on the way to the root. A leaf is
evaluated for the side to move there, which is the opponent of the player who moved
into it, so the reward is flipped once before the walk starts.

Rewards are always in [0, 1] (loss 0, draw 0.5, win 1; evaluations mapped with a
tanh squash), because the UCT exploration constant assumes that range.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable

from bots.base import Bot, Budget, SearchResult
from engine import rules
from engine.board import Board
from engine.types import Color, Move, Result
from eval.evaluator import build_evaluator

# Centipawn advantage at which the squash reaches tanh(1) ~ 0.76. A chariot is 900,
# so most real advantages are still on the responsive part of the curve; a decisive
# material win saturates without ever reaching 1 (which is reserved for forced wins).
EVAL_SCALE = 600

# The reported score comes from inverting the squash, which diverges as the mean
# reward approaches 0 or 1; clamp it far below the search's mate range (99,000).
MAX_REPORTED_SCORE = 5_000

# Budgets. The server will clamp again; doing it here too means a bot instance
# cannot be asked to run for an unbounded time even if a caller forgets.
MAX_ITERATIONS = 1_000_000
MAX_TIME_LIMIT_MS = 10_000.0

# An iteration is milliseconds of work, so checking the clock more often than this
# buys nothing; 16 keeps the overshoot on a 200 ms budget small.
CLOCK_CHECK_INTERVAL = 16

DEFAULT_ITERATIONS = 1_000
DEFAULT_EXPLORATION = 1.4
DEFAULT_ROLLOUT_DEPTH = 30
DEFAULT_CAPTURE_BIAS = 0.0


class _Node:
    """One position in the search tree.

    ``value_sum`` accumulates rewards for the player who moved into this node (the
    parent's side to move). The root has no such player, so its own value is unused;
    only its visits matter (for the UCT logarithm).
    """

    __slots__ = (
        "parent",
        "move",
        "children",
        "untried_moves",
        "visits",
        "value_sum",
        "terminal_reward",
    )

    def __init__(self, parent: _Node | None, move: Move | None) -> None:
        self.parent = parent
        self.move = move
        self.children: list[_Node] = []
        self.untried_moves: list[Move] = []
        self.visits = 0
        self.value_sum = 0.0
        # Reward for the side to move at this node if the game is already over
        # (0.0 loss, 0.5 draw), otherwise None.
        self.terminal_reward: float | None = None

    @property
    def is_terminal(self) -> bool:
        """True if the position at this node ends the game."""
        return self.terminal_reward is not None

    @property
    def mean_value(self) -> float:
        """Average reward for the player who moved into this node (0 for unvisited)."""
        return self.value_sum / self.visits if self.visits else 0.0


class _Search:
    """One MCTS run over a mutable board.

    The board is moved down the path with ``make_move`` and unwound in a ``finally``
    block after every iteration, so it is never copied and always ends where it
    started, even if an exception escapes.
    """

    def __init__(
        self,
        board: Board,
        evaluator: Callable[[Board], int],
        rng: random.Random,
        *,
        exploration: float,
        rollout_depth: int,
        capture_bias: float,
        iteration_limit: int,
        time_source: Callable[[], float],
        deadline: float | None,
    ) -> None:
        self.board = board
        self.evaluator = evaluator
        self.rng = rng
        self.exploration = exploration
        self.rollout_depth = rollout_depth
        self.capture_bias = capture_bias
        self.iteration_limit = iteration_limit
        self.time_source = time_source
        self.deadline = deadline
        self.root = _Node(parent=None, move=None)
        self.root.untried_moves = board.legal_moves()
        # Plies made since the root in the current iteration, unwound in ``finally``.
        self._made = 0

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Iterate until the iteration or time budget ends; return iterations completed."""
        completed = 0
        for _ in range(self.iteration_limit):
            if self._time_is_up(completed):
                break
            self._iterate()
            completed += 1
        return completed

    def _time_is_up(self, completed: int) -> bool:
        """Check the clock every CLOCK_CHECK_INTERVAL iterations, not every one."""
        if self.deadline is None:
            return False
        if completed % CLOCK_CHECK_INTERVAL:
            return False
        return self.time_source() >= self.deadline

    def _iterate(self) -> None:
        """One MCTS iteration: select, expand, simulate, backpropagate."""
        self._made = 0
        try:
            node = self._select()
            if not node.is_terminal:
                node = self._expand(node)
            reward = self._simulate(node)
            self._backpropagate(node, reward)
        finally:
            self._unwind()

    # ------------------------------------------------------------------
    # Phase 1: selection
    # ------------------------------------------------------------------

    def _select(self) -> _Node:
        """Walk down the tree by UCT until a node to expand (or a terminal node)."""
        node = self.root
        while not node.is_terminal and not node.untried_moves and node.children:
            node = self._best_child(node)
            # Tree nodes use the normal record_check path: game_result needs the
            # check flags to apply the repetition rule properly.
            self._play(node.move, record_check=True)
        return node

    def _best_child(self, node: _Node) -> _Node:
        """Child with the highest UCT score.

        UCT = exploitation + exploration, where

            exploitation = child.mean_value
                (the average reward for the player who moved into the child --
                 exactly the player choosing at ``node``, thanks to the convention)
            exploration  = exploration_constant * sqrt(ln(parent_visits) / child_visits)

        The exploration term grows for rarely visited children and shrinks as they
        are visited, and it assumes rewards in [0, 1]: that is why every value is
        mapped into that range before backpropagation.
        """
        log_visits = math.log(node.visits) if node.visits > 1 else 0.0
        best = None
        best_score = -math.inf
        for child in node.children:
            score = child.mean_value + self.exploration * math.sqrt(log_visits / child.visits)
            if score > best_score:
                best, best_score = child, score
        return best

    # ------------------------------------------------------------------
    # Phase 2: expansion
    # ------------------------------------------------------------------

    def _expand(self, node: _Node) -> _Node:
        """Add one child for a random untried move and note whether it is terminal."""
        index = self.rng.randrange(len(node.untried_moves))
        move = node.untried_moves.pop(index)
        self._play(move, record_check=True)
        child = _Node(parent=node, move=move)
        node.children.append(child)

        # The engine decides terminality here, so repetition and the 300-ply cap are
        # handled by the same code the game itself uses.
        legal = self.board.legal_moves()
        result = rules.game_result(self.board, legal)
        if result is not None:
            child.terminal_reward = _reward_for_side_to_move(result, self.board.side_to_move)
        else:
            child.untried_moves = legal
        return child

    # ------------------------------------------------------------------
    # Phase 3: simulation (rollout)
    # ------------------------------------------------------------------

    def _simulate(self, node: _Node) -> float:
        """Reward for the player who moved into ``node``.

        The rollout (or the stored terminal result) is computed for the side to move
        at ``node``, which is that player's opponent, so it is flipped exactly once.
        """
        if node.is_terminal:
            return 1.0 - node.terminal_reward
        reward_for_side_to_move = self._rollout()
        return 1.0 - reward_for_side_to_move

    def _rollout(self) -> float:
        """Play random legal moves for up to ``rollout_depth`` plies.

        Returns the reward for the side to move at the rollout's final position:
        0 for a loss, 0.5 for a draw, or the squashed evaluation.

        # NOTE: rollouts use ``record_check=False`` for speed, so the per-ply check
        # flags are all False: repetition is not detected inside a rollout and the
        # "perpetual check loses" rule is not modelled there. Only the 300-ply cap
        # and "no legal moves loses" are enforced. Tree nodes are unaffected: they
        # use ``record_check=True`` and the engine's game_result.
        """
        board = self.board
        for _ in range(self.rollout_depth):
            if board.ply_count >= rules.MOVE_CAP_PLIES:
                return 0.5
            legal = board.legal_moves()
            if not legal:
                # No legal moves is a loss, checkmate or stalemate alike.
                return 0.0
            move = self._rollout_move(legal)
            self._play(move, record_check=False)
        return self._evaluate_reward()

    def _rollout_move(self, legal: list[Move]) -> Move:
        """Random move; with probability ``capture_bias`` prefer a capture."""
        if self.capture_bias > 0.0 and self.rng.random() < self.capture_bias:
            captures = [move for move in legal if move.captured]
            if captures:
                return captures[self.rng.randrange(len(captures))]
        return legal[self.rng.randrange(len(legal))]

    def _evaluate_reward(self) -> float:
        """Evaluation at the cutoff, squashed into [0, 1] for the side to move."""
        score = self.evaluator(self.board)
        return 0.5 + 0.5 * math.tanh(score / EVAL_SCALE)

    # ------------------------------------------------------------------
    # Phase 4: backpropagation
    # ------------------------------------------------------------------

    def _backpropagate(self, node: _Node, reward_for_mover: float) -> None:
        """Add the reward up the path, flipping perspective at every edge.

        ``reward_for_mover`` is for the player who moved into ``node``. One ply up,
        the move was made by the other player, so the reward becomes ``1 - reward``,
        and so on to the root. The root's own value is not used (only its visits).
        """
        reward = reward_for_mover
        while node is not None:
            node.visits += 1
            if node.parent is not None:
                node.value_sum += reward
            reward = 1.0 - reward
            node = node.parent

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def result(self) -> tuple[Move, int, int, int]:
        """Return ``(move, depth, score)`` for the completed search.

        The move is the most-visited root child (ties go to the earlier child, which
        keeps a seeded run deterministic). ``depth`` is the length of the most-visited
        line below the root. ``score`` is the chosen child's mean reward converted
        back to centipawns with the inverse squash ``EVAL_SCALE * atanh(2q - 1)``,
        clamped to ``MAX_REPORTED_SCORE`` so it can never look like a mate score.
        """
        if not self.root.children:
            # The budget expired before a single iteration: fall back to a legal move.
            move = self.root.untried_moves[0]
            return move, 0, 0

        best = max(self.root.children, key=lambda child: (child.visits, child.mean_value))
        depth = self._most_visited_depth(best)

        q = 2.0 * best.mean_value - 1.0
        q = min(max(q, -0.999999), 0.999999)
        score = int(round(EVAL_SCALE * math.atanh(q)))
        score = max(-MAX_REPORTED_SCORE, min(MAX_REPORTED_SCORE, score))
        return best.move, depth, score

    @staticmethod
    def _most_visited_depth(node: _Node) -> int:
        """Follow the most-visited child from ``node`` down, counting plies."""
        depth = 0
        while node.children:
            node = max(node.children, key=lambda child: child.visits)
            depth += 1
        return depth

    # ------------------------------------------------------------------
    # Board bookkeeping
    # ------------------------------------------------------------------

    def _play(self, move: Move, record_check: bool) -> None:
        """Make a move and count it, so ``_unwind`` can restore the board exactly."""
        self.board.make_move(move, record_check=record_check)
        self._made += 1

    def _unwind(self) -> None:
        """Undo everything this iteration made."""
        while self._made:
            self.board.unmake_move()
            self._made -= 1


def _reward_for_side_to_move(result: Result, side_to_move: Color) -> float:
    """0 for a loss, 0.5 for a draw, 1 for a win, for ``side_to_move``."""
    if result.winner is None:
        return 0.5
    return 1.0 if result.winner is side_to_move else 0.0


class MCTSBot(Bot):
    """Plain UCT MCTS.

    Parameters (the ones the server registry exposes):

    * ``iterations``: fixed iteration budget; None means "time only".
    * ``time_limit_ms``: soft deadline; None means "iterations only".
    * ``exploration``: UCT constant, default 1.4 (rewards are in [0, 1]).
    * ``rollout_depth``: rollout ply cap.
    * ``evaluator``: name from ``eval.evaluator``; default ``full``.
    * ``capture_bias``: probability of preferring a capture during a rollout, 0 = off.

    If both budgets are set the search stops at whichever comes first. Unknown
    parameters raise ``TypeError``; out-of-range values raise ``ValueError``.
    """

    type_name = "mcts"

    def __init__(
        self,
        iterations: int | None = DEFAULT_ITERATIONS,
        time_limit_ms: float | None = None,
        exploration: float = DEFAULT_EXPLORATION,
        rollout_depth: int = DEFAULT_ROLLOUT_DEPTH,
        evaluator: str = "full",
        capture_bias: float = DEFAULT_CAPTURE_BIAS,
        seed: int | None = None,
        time_source: Callable[[], float] = time.perf_counter,
    ) -> None:
        if iterations is not None and iterations < 1:
            raise ValueError(f"iterations must be at least 1, got {iterations}")
        if time_limit_ms is not None and time_limit_ms <= 0:
            raise ValueError(f"time_limit_ms must be positive, got {time_limit_ms}")
        if exploration < 0:
            raise ValueError(f"exploration must be non-negative, got {exploration}")
        if rollout_depth < 1:
            raise ValueError(f"rollout_depth must be at least 1, got {rollout_depth}")
        if not 0.0 <= capture_bias <= 1.0:
            raise ValueError(f"capture_bias must be in [0, 1], got {capture_bias}")
        # Building the evaluator here rejects an unknown name before any game starts.
        self._evaluator = build_evaluator(evaluator)

        super().__init__(
            {
                "iterations": iterations,
                "time_limit_ms": time_limit_ms,
                "exploration": exploration,
                "rollout_depth": rollout_depth,
                "evaluator": evaluator,
                "capture_bias": capture_bias,
            }
        )
        self.iterations = iterations
        self.time_limit_ms = time_limit_ms
        self.exploration = exploration
        self.rollout_depth = rollout_depth
        self.evaluator_name = evaluator
        self.capture_bias = capture_bias
        # All randomness comes from this instance; a fixed seed makes a run
        # reproducible, and the stream continues across the moves of one game.
        self._rng = random.Random(seed)
        # Injectable so a test can drive the deadline deterministically.
        self.time_source = time_source

    def choose_move(self, board: Board, budget: Budget) -> SearchResult:
        """Run MCTS within the budget and return the most-visited root move."""
        legal = board.legal_moves()
        if not legal:
            raise ValueError("no legal moves: the game is already over")

        iteration_limit, time_limit_ms = self._limits(budget)
        started = self.time_source()
        deadline = None if time_limit_ms is None else started + time_limit_ms / 1000.0

        search = _Search(
            board,
            self._evaluator,
            self._rng,
            exploration=self.exploration,
            rollout_depth=self.rollout_depth,
            capture_bias=self.capture_bias,
            iteration_limit=iteration_limit,
            time_source=self.time_source,
            deadline=deadline,
        )
        completed = search.run()
        move, depth, score = search.result()
        elapsed_ms = (self.time_source() - started) * 1000.0
        return SearchResult(
            move=move,
            score=score,
            depth=depth,
            nodes=completed,
            elapsed_ms=elapsed_ms,
        )

    def _limits(self, budget: Budget) -> tuple[int, float | None]:
        """Combine configured and requested budgets, taking the stricter of each."""
        iterations = self.iterations
        if budget.max_iterations is not None:
            if iterations is None:
                iterations = budget.max_iterations
            else:
                iterations = min(iterations, budget.max_iterations)

        time_limit_ms = self.time_limit_ms
        if budget.time_limit_ms is not None:
            if time_limit_ms is None:
                time_limit_ms = budget.time_limit_ms
            else:
                time_limit_ms = min(time_limit_ms, budget.time_limit_ms)

        if iterations is None and time_limit_ms is None:
            iterations = DEFAULT_ITERATIONS

        # Server-side style clamps: a bot instance cannot be asked to run forever.
        if iterations is not None:
            iterations = min(iterations, MAX_ITERATIONS)
        if time_limit_ms is not None:
            time_limit_ms = min(time_limit_ms, MAX_TIME_LIMIT_MS)
        return (MAX_ITERATIONS if iterations is None else iterations), time_limit_ms
