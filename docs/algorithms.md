# Algorithms in this codebase

Plain-language notes on the search algorithms, written for someone reading the code
for the first time. Each section points at the file that implements it.

- [Alpha-beta](#alpha-beta)
- [Move ordering](#move-ordering)
- [Iterative deepening and the clock](#iterative-deepening-and-the-clock)
- [Transposition table](#transposition-table)
- [Quiescence](#quiescence)
- [Evaluation](#evaluation)
- [MCTS](#mcts)

## Alpha-beta

**File:** `bots/alphabeta.py` (`_Search.negamax`)

Alpha-beta is minimax with two extra numbers. Every node gets a window:

- **alpha** is the best score the side to move is already guaranteed. Values at or
  below alpha are not interesting, because the parent already has a better option.
- **beta** is the score at which the *opponent* already does better somewhere else.
  If a move reaches beta, the parent will never allow this position, so the search
  can stop looking at the other moves.

A **cutoff** happens when `alpha >= beta` inside the move loop: the remaining moves
cannot change the parent's decision, so they are skipped. That is the whole trick;
the value returned is still a valid bound, which is why the caller must remember it
as "at least this" (a lower bound) rather than an exact score.

This code uses **negamax**: the same routine serves both sides, and each level flips
the sign and swaps the window:

```python
value = -self.negamax(depth - 1, -beta, -alpha, ply + 1)
```

### Scoring a position

- **No legal moves is a loss** for the side to move, whether it is checkmate or
  stalemate (Xiangqi scores both as a loss). It returns `-MATE_SCORE + ply`, so a
  mate in 2 (`-MATE + 1` from the winner's view at the parent) beats a mate in 6.
- **A repeated position is a draw (0).** The search path is a dictionary of position
  hashes; the parent pushes a child before recursing and the child returns 0 if its
  hash is already there.
- Everything else is the evaluation function (`eval/evaluator.py`), in centipawns
  from the side to move's point of view.

### Reference implementation

`reference_minimax` in the same file is the same rules with no pruning: no window,
no ordering, no table. It is slow on purpose and is the oracle the tests compare
against (`tests/test_alphabeta.py`). If the fast search ever disagrees with it on a
root value, the fast search has a bug.

## Move ordering

**File:** `bots/alphabeta.py` (`_Search._order`, `_Search._record_cutoff`)

Alpha-beta only saves work when the best move is tried first: a good move raises
alpha early, and every later move that fails to beat it is cut off immediately.
Moves are sorted into four tiers, separated by wide constant gaps so a cheap
heuristic can never outrank a more reliable one:

1. the best move from the transposition table for this position;
2. captures, by MVV-LVA (most valuable victim, least valuable attacker) -- taking a
   chariot with a soldier scores highest;
3. the two **killer** moves for this ply: quiet moves that caused a cutoff at the
   same ply in another branch, remembered in `self.killers`;
4. quiet moves ordered by the **history heuristic**: how often this from/to pair has
   caused a cutoff anywhere, weighted by `depth * depth`.

The node-count table in the README shows the effect: at depth 3 on 50 middlegame
positions, ordering takes 430,538 nodes down to 84,905.

## Iterative deepening and the clock

**File:** `bots/alphabeta.py` (`_Search.run`, `_Search._tick`)

Instead of searching depth *d* once, search depth 1, then 2, then 3, ... until the
time budget runs out. Two reasons: the result of a shallow search orders the moves
of the next one (through the transposition table), and the bot always has a usable
answer if time expires.

The time check is in `_tick`, which every node calls: it increments the node counter
and, every `CLOCK_CHECK_INTERVAL` nodes, compares the clock with the deadline. This
keeps the harness responsive without paying for a clock read on every node.

When the deadline hits, `_tick` raises `_SearchTimeout`. Every `make_move` sits in a
`try/finally` so the board, the path, and the move stack are restored as the
exception unwinds. `run` catches it and returns the best move from the last
*completed* depth -- never a partially searched iteration.

## Transposition table

**File:** `bots/alphabeta.py` (`TTEntry`, `Bound`, `_store`, `_score_to_table`)

The same position can be reached by many move orders. The table is a dictionary
keyed by the Zobrist hash, storing the depth searched, the value, the best move, and
what the value means:

- `EXACT`: the true score at that depth;
- `LOWER`: the search cut off, so the position is worth *at least* this;
- `UPPER`: every move failed low, so it is worth *at most* this.

A probe can only return a value when the stored depth is at least the requested
depth. Otherwise the bound can still narrow the window: a `LOWER` value raises
alpha, an `UPPER` value lowers beta, and if the window closes the node returns
immediately.

Mate scores are stored relative to the node, not the root: a score of
`MATE_SCORE - total_plies` at `ply` is stored as `value + ply` and converted back on
the way out, so the same position found at a different depth still means "mate in n
from here".

**Path-dependent values are never stored.** A subtree that scored a repetition draw
depends on the path that reached it, so storing it would poison the table for other
paths. `_path_dependent` is a counter: if it changed during a node's search, that
node skips the store. This is the one place where the table and the repetition rule
interact, and the `# NOTE:` in `negamax` says so.

## Quiescence

**File:** `bots/alphabeta.py` (`_Search.quiescence`)

A depth-limited search that stops right after a capture is misleading: the
opponent's recapture is not counted. Quiescence (behind `quiescence=True`) keeps
searching captures at the horizon until the position is quiet.

- `stand_pat` is the evaluation if the side to move simply declines to capture; it
  is a lower bound, so a `stand_pat >= beta` cutoff is safe.
- Only captures are searched, ordered MVV-LVA; the search is capped by `MAX_PLY`.
- If the side to move has no legal move, it is still scored as a loss, so quiescence
  does not miss a mate at the horizon.

Because it only looks at captures, quiescence changes nothing on positions where no
capture exists; `tests/positions_quiet.txt` is the set that proves it.

## Evaluation

**Files:** `eval/material.py`, `eval/pst.py`, `eval/mobility.py`,
`eval/king_safety.py`, combined in `eval/evaluator.py`.

Every feature returns **centipawns from the side to move's point of view**: positive
means the side to move is better. That is the sign convention negamax needs, because
a child's value is negated on the way back up. Because every feature uses the same
convention, the evaluator is a weighted sum:

```
score = w_material * material + w_pst * pst + w_mobility * mobility + w_safety * king_safety
```

**Units of the weights.** Material and PST are already in centipawns, so weight 1
means "use the value as it is". Mobility is a *move count*, so its weight is
centipawns per extra pseudo-legal move; the default is 2. King safety is a sum of its
own centipawn constants, so weight 1 uses it as is. The defaults are
`{"material": 1, "pst": 1, "mobility": 2, "king_safety": 1}`.

`evaluate_from_red(board, evaluator)` is the absolute view: it negates the side-to-
move score when Black is to move, so the same placement always reports the same
number and a Red advantage is positive whoever is to move.

**Material** is the piece values (chariot 900, cannon 450, horse 400, advisor 200,
elephant 200, soldier 100, or 200 once it has crossed the river). It is implemented
as a flat table of *signed* values indexed by `piece_code * 90 + square`, so a leaf
costs one add per occupied square: about 2.4 microseconds. Material has to dominate
every other term; the positional features are worth tens of centipawns.

**Piece-square tables** add a small bonus for a piece's square. Table values are
centipawns; the full set is printed in the README. Soldiers are the clearest example,
because the table encodes two rules at once -- advance, and then cross the river:

| rank | a | b | c | d | e | f | g | h | i |
|---|---|---|---|---|---|---|---|---|---|
| 9 | 26 | 36 | 50 | 66 | 78 | 66 | 50 | 36 | 26 |
| 8 | 22 | 30 | 42 | 56 | 66 | 56 | 42 | 30 | 22 |
| 7 | 18 | 24 | 32 | 44 | 52 | 44 | 32 | 24 | 18 |
| 6 | 14 | 18 | 24 | 32 | 38 | 32 | 24 | 18 | 14 |
| 5 | 10 | 12 | 16 | 22 | 26 | 22 | 16 | 12 | 10 |
| 4 | 5 | 5 | 8 | 12 | 15 | 12 | 8 | 5 | 5 |
| 3-0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

A soldier at home scores 0; every step adds a little; crossing the river (rank 5 for
Red) jumps the value; and the centre of the enemy palace is the peak. Horses prefer
the centre (more destinations, fewer blocked legs), chariots and cannons prefer the
central files, advisors and elephants prefer their home points, and the general
prefers the back rank. Red's tables are written bottom-up, 10 rows of 9; Black uses
the same values **flipped by rank** (`r -> 9-r`).

One honest limitation: a piece-square table cannot know whether a **file is open** --
that is a property of the other pieces, not of the square. The chariot table encodes
a central-file preference as the static proxy, because central files are the ones
that open first. A real open-file term needs a separate occupancy feature, which the
proposal does not include.

**Mobility** is the difference in pseudo-legal move counts. Pseudo-legal (rather than
legal) counts keep the cost down: it is already the most expensive feature at ~39
microseconds per evaluation, because it counts moves for both sides. The count is a
measure of freedom, not of legal options.

**King safety** rewards the general's armour: advisors and elephants still on the
board, more so when they are near the general, with penalties for a missing advisor,
a missing elephant, both missing, or a general standing on the palace's forward rank.
All constants are named and live next to the code.

**Bounds.** Each positional contribution is clamped after weighting to a named budget
(PST 150, mobility 200, king safety 150 centipawns), so the three together can never
exceed 500 and therefore can never outweigh a chariot (900). The largest possible
evaluation -- one full army with every soldier across the river (5,300) plus that
budget = 5,800 -- plus the 300-ply mate-distance adjustment stays far below
`MATE_SCORE` (100,000), so a forced mate always outranks a heuristic score. Both
inequalities are asserted in `tests/test_eval.py`, and `python -m tools.clamp_stats`
reports how often a clamp actually fires (0% on the fixed position sets).

The named evaluators add one feature at a time -- `material`, `material_pst`,
`material_pst_mobility`, `full` -- so the M6 comparison can attribute a difference to
one feature. Weights live in `DEFAULT_WEIGHTS` and can be overridden per evaluator;
`TEMPO_BONUS` is a named constant left at 0 so the start position still evaluates to
0.

## MCTS

**File:** `bots/mcts.py`. Plain UCT with random rollouts; no neural network, no
policy, no value head. The four phases are one method each, in the order they run:

**1. Selection** (`_select`, `_best_child`). Start at the root and walk down, always
entering the child with the highest UCT score, until a node that is not fully
expanded (or a terminal node) is reached. For a node with `N` visits and a child with
`n` visits and average reward `Q`:

```
UCT = Q + c * sqrt(ln N / n)
```

The first term is exploitation (the child has paid off so far); the second is
exploration, large for rarely visited children and shrinking as they are visited. `c`
is the exploration constant (default 1.4). This formula assumes rewards in **[0, 1]**,
which is why every value is mapped into that range before it is stored -- a reward
outside the range would make the two terms incomparable.

**2. Expansion** (`_expand`). Add one child for a randomly chosen untried move, then
ask the engine's `rules.game_result` whether the position is already over. That single
call covers checkmate, stalemate, the 300-ply cap, and repetition, so the tree uses
exactly the rules the game does. Terminal children are scored immediately; no rollout
runs from them.

**3. Simulation** (`_rollout`). From the new child, play random legal moves for at
most `rollout_depth` plies (default 30). If a player has no legal move, the rollout
ends with that player's loss; if the ply cap is reached, the rollout ends as a draw.
Otherwise the position is evaluated and squashed into a reward for the side to move:

```
reward = 0.5 + 0.5 * tanh(score_in_centipawns / EVAL_SCALE)
```

`EVAL_SCALE = 600` centipawns: one `tanh` unit. A 600-point advantage is already close
to decisive (that is a bit less than a chariot), so the squash saturates around there
instead of giving absurd rewards for a rook up. The squash keeps every evaluation
inside (0, 1) while preserving the sign convention of the evaluation.

Rollout policy: uniform random legal moves, optionally with `capture_bias` (default
off) making a capture likely. Rollouts move with `record_check=False` for speed.

> **NOTE:** rollouts do not model the perpetual-check rule and do not detect
> repetition inside the rollout; only "no legal moves loses" and the 300-ply cap are
> enforced there. Tree nodes are exact: they use `record_check=True` and
> `game_result`.

**4. Backpropagation** (`_backpropagate`). Add the reward to every node on the path
from the leaf to the root. Values are stored **for the player who moved into the
node**, so at each edge the reward flips: `reward = 1 - reward`. That is the whole
sign convention, and `tests/test_mcts.py` pins it down with a hand-built tree: the
node reached by Red's move gets 1.0 for a Red win and the node reached by Black's move
one ply above gets 0.0. A sign error here makes the bot play to lose, which is why it
has its own test.

**Choosing the move.** The move is the **most-visited root child** (ties go to the
earlier child, keeping seeded runs deterministic), not the highest average value: with
few visits the average is noisy, while visit counts are what the search actually
invested in. The reported depth is the length of the **most-visited line** below the
root, and the reported score converts the chosen child's mean reward back to
centipawns with the inverse squash, `EVAL_SCALE * atanh(2q - 1)`, clamped to
`MAX_REPORTED_SCORE` (5,000) because `atanh` diverges near 0 and 1.

**Budget.** Either a fixed iteration count (deterministic for a given seed) or a time
limit, checked every `CLOCK_CHECK_INTERVAL` iterations rather than every iteration; if
both are set, the search stops at whichever comes first. All randomness comes from one
`random.Random` seeded per bot instance. The board is never copied: the search moves
down the tree with `make_move` and unwinds everything in a `finally` block, so it ends
exactly where it started (tested on FEN, hash and history length).
