# Algorithms in this codebase

Plain-language notes on the search algorithms, written for someone reading the code
for the first time. Each section points at the file that implements it.

- [Alpha-beta](#alpha-beta)
- [Move ordering](#move-ordering)
- [Iterative deepening and the clock](#iterative-deepening-and-the-clock)
- [Transposition table](#transposition-table)
- [Quiescence](#quiescence)
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

## MCTS

Not implemented yet; plain UCT arrives in M5 (`bots/mcts.py`) and will be documented
here with its four phases: selection, expansion, rollout, and backpropagation.
