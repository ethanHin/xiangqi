# Experiment log

A chronological record of every parameter change and its measured effect. The point
is that a reader (and the report) can see *what was tried*, *what it cost*, and
*what actually happened*, including the attempts that made things worse.

## How to read this log

Every entry records:

- **Title and date**, plus the commit or tag it belongs to.
- **What changed** -- one parameter or design choice.
- **Setup** -- bots, games, seed, openings, cap/depth, workers, and the command.
  Where a run predates the current bot code, the entry says
  *"from earlier report, not re-run"*: the code has changed since, so the numbers
  cannot be reproduced now.
- **Result** -- W/D/L and the relevant per-move statistics.
- **Observation** -- what changed and why, marked **MEASURED** (backed by a number
  in the entry) or **LIKELY** (a plausible explanation that was not tested).
- **Caveat** -- sample-size and comparison warnings.
- **Verdict** -- one word:

| verdict | meaning |
|---|---|
| HELPED | the measured metric moved in the desired direction |
| HURT | it moved the wrong way, or introduced a defect |
| NEUTRAL | measured, no change in the metric |
| INCONCLUSIVE | sample too small, or no matched before/after to compare |

Unless stated otherwise, all runs are on this machine with Python 3.11+ (the venv
interpreter), `pytest` for tests, and the random opening applied from the run's seed.
Timing numbers are wall clock and therefore machine-dependent; node counts are not.

**Environment for every entry below:** Windows 11 (10.0.26200), CPython **3.15.0** in
`.venv`, 16 logical CPUs, single local machine. Recorded because wall-clock numbers
are not comparable across machines; node counts and W/D/L are.

## Summary

| tweak | milestone | metric measured | before | after | verdict |
|---|---|---|---|---|---|
| Engine core + perft baseline | M1 | perft nodes d1-d4 | -- | 44 / 1,920 / 79,666 / 3,290,240 | INCONCLUSIVE |
| Greedy tie-break: fixed move -> seeded random | M2 | win rate vs random | 0% (0W 10D 0L, 10 games) | 80% (16W 4D 0L, 20 games) | HELPED |
| Greedy tie-break: prefer checks | M2 | win rate vs random | 80% (20 games) | 70% (14W 1D 5L) | HURT |
| Greedy tie-break: MVV-LVA | M2 | win rate vs random | 70% (20 games) | 95% (19W 1D 0L) | HELPED |
| Greedy: mate-in-one detection | M2 | win rate vs random | 95% (20 games, not matched) | 98% (98W 2D 0L, 100 games) | HELPED |
| Two-tier legality filter, first attempt | M3 | perft d2 / d3 | 1,920 / 79,666 | 1,926 / 80,062 | HURT |
| Cannon-screen guard added | M3 | perft depth-4 time | 22.9 s | 5.0 s | HELPED |
| Material eval: signed-value table | M3 | cost per evaluation | 17.01 us | 2.56 us | HELPED |
| Alpha-beta | M3 | nodes, depth 3, 50 positions | 3,056,865 (minimax) | 430,538 | HELPED |
| Move ordering | M3 | nodes, depth 3, 50 positions | 430,538 | 84,905 | HELPED |
| Transposition table (transposition set, d4) | M3 | nodes, 10 positions | 10,260 | 8,476 | HELPED |
| Transposition table (middlegame, d3) | M3 | nodes, 50 positions | 84,905 | 84,905 | NEUTRAL |
| Quiescence (quiet positions) | M3 | root values, 6 positions | 6/6 equal | 6/6 equal | NEUTRAL |
| Clock check 1024 -> 256 nodes | M3 | avg ms per move at 200 ms cap | 234.1 ms | 200.8 ms | HELPED |
| History seeding of the search path | M3 | win rate vs greedy, 100 games | 95% (95W 0D 5L) | 99% (99W 0D 1L) | HELPED |
| Alpha-beta vs greedy (acceptance) | M3 | win rate vs greedy | -- | 99% | INCONCLUSIVE |
| Iterative deepening, 50 ms budget | M3 | returned depth and elapsed | -- | depth 2 in 50-67 ms | INCONCLUSIVE |
| Piece-square tables | M4 | win rate vs material, 10 games | -- | 70% (7W 1D 2L) | INCONCLUSIVE |
| Mobility + king safety (`full`) | M4 | `full` win rate vs `material_pst`, 10 games | -- | 30% (3W 1D 6L) | INCONCLUSIVE |
| Positional clamps (150 / 200 / 150) | M4 v2 | largest positional total vs a chariot | unbounded | 500 vs 900, clamps fired 0% | HELPED |
| Evaluator smoke check, depth 2 | M4 v2 | richer evaluator's win rate vs material | -- | 50% / 80% / 90% | INCONCLUSIVE |
| Evaluator cost, depth 3, 60 positions | M4 v2 | `full` evaluation cost | 2.31 us (material) | 62.29 us | HURT |
| Evaluator cost, depth 3, 60 positions | M4 v2 | `full` search speed | 15,366 nodes/s | 8,113 nodes/s | HURT |
| MCTS vs random (acceptance) | M5 | win rate, 100 games, 200 iterations | -- | 100% (100W 0D 0L) | INCONCLUSIVE |
| MCTS iterations 100 -> 400 -> 1600 | M5 | win rate vs greedy, 10 games per arm | 10% | 70% | INCONCLUSIVE |
| UCT exploration 0.7 -> 1.4 -> 2.0 | M5 | win rate vs greedy at 400 iterations | 80% (c=0.7) | 30% (c=2.0) | INCONCLUSIVE |

## Problem definition

**State.** A Xiangqi position is fully described by a 9x10 board (90 squares, 16
pieces per side in the starting position), the side to move, and the move history
needed for the repetition rule. The engine stores it as a flat 90-entry list of
piece codes plus a Zobrist hash; the serialised form is FEN:

```
rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1
```

Every fact is visible to both players: **perfect information**, no hidden state.

**Actions.** One action is a legal move: a piece, a destination, and (implicitly)
what it captures. The measured branching factor at the start position is **44 legal
moves**. Across the 103 saved games in `games/` (16,179 positions, replayed by
`python -m tools.branching_stats`) the average number of legal moves per position is
**26.9** (median 30, min 1, max 81), falling as pieces come off:

| ply band | positions | avg legal moves |
|---|---|---|
| 0-19 | 2,027 | 34.57 |
| 20-39 | 1,993 | 31.02 |
| 40-59 | 1,960 | 27.53 |
| 60+ | 10,199 | 24.42 |

**Caveat on that sample:** the 103 saved games are almost all greedy-vs-random, plus
a random-vs-random sample and a scripted 8-ply game. Random play trades pieces
differently from a searching player, so the 26.9 figure understates how wide a
typical alpha-beta game is. It will be recomputed from alpha-beta vs alpha-beta
games after M6, once those exist in quantity.

**Transition model.** Deterministic and known: `board.make_move(move)` updates the
position, the hash, and the move history; `unmake_move()` restores them exactly.
There is no chance element (no dice, no hidden cards) and no simultaneous moves.

**Terminal states.**

| terminal state | utility for the side that caused it |
|---|---|
| no legal moves, in check (checkmate) | loser is the side to move |
| no legal moves, not in check (stalemate) | loser is the side to move |
| same position three times | draw, unless one side checked on every move of the cycle and the other did not -- then the checker loses |
| 300 plies played | draw |

**Utility.** For a player: +1 win, 0 draw, -1 loss. The search works in centipawns
and uses `MATE_SCORE - ply` for forced wins, so a faster mate is strictly better.

**Game tree.** Alternating **Max** (Red) and **Min** (Black) layers, no chance nodes,
no hidden information, and a hard horizon at 300 plies. The tree grows about 41-44
ways per ply near the start: perft gives 44, 1,920 (x43.6), 79,666 (x41.5) and
3,290,240 (x41.3) nodes at depths 1-4. A plain minimax to depth 3 therefore visits
about 75,000 nodes, which is why alpha-beta and move ordering matter: the M3 search
reaches the same depth-3 values with 84,905 nodes on 50 middlegame positions instead
of 3,056,865, and with a fixed 200 ms budget it completes depth 2-3 per move, at
2,291 nodes per move on average.

---

## 2026-11-23 -- Engine core and perft baseline (M1)

- **Commit/tag:** `5828502` / `m1-perft`.
- **What changed:** initial engine: board, FEN, make/unmake, Zobrist hashing, move
  generation, terminal detection.
- **Setup:** `pytest -q` and `pytest -q -m slow` at that commit.
- **Result:** perft from the start position: depth 1 = 44, depth 2 = 1,920,
  depth 3 = 79,666, depth 4 = 3,290,240, all matching the published Xiangqi values
  on the first run. Depth 4 took 22.9 s (behind the `slow` marker). 48 fast tests
  passed in 0.7 s.
- **Observation:** MEASURED: every reference node count matched, so the movement
  rules, the flying-general rule, and the legality filter are correct at the start
  position. The cost is dominated by legality filtering (one make/unmake plus an
  attack scan per candidate move).
- **Caveat:** no before/after; this is the baseline the later speedups are measured
  against.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- Greedy tie-break: fixed first move (M2)

- **Commit/tag:** measured while building M2 (before `34027f9` / `m2-harness`).
- **What changed:** the greedy bot's choice among equally valuable moves: always the
  first move the generator produced (`best_moves[0]`).
- **Setup:** greedy vs random, 10 games (5 per colour), seed 1, 6-ply random opening,
  200 ms per-move cap (greedy ignores it), 1 worker. Recorded inline with
  `harness.tournament.run_match`; no CLI existed yet. From earlier report, not
  re-run -- the bot has changed since.
- **Result:** 0W / 10D / 0L, win rate 0%. Every game ended by threefold repetition,
  average 95.3 plies.
- **Observation:** MEASURED: with no capture available the bot always played the
  same move, so the two sides settled into a four-ply cycle and the repetition rule
  ended every game. A deterministic baseline is not even a usable yardstick.
- **Caveat:** 10 games, and the opponent is random; the point is the shape of the
  failure (10/10 identical endings), not the exact rate.
- **Verdict:** HURT.

---

## 2026-11-23 -- Greedy tie-break: seeded random (M2)

- **Commit/tag:** measured while building M2 (before `34027f9`).
- **What changed:** ties are broken uniformly at random from a seeded `Random`, so
  the bot does not repeat the same move whenever nothing is capturable.
- **Setup:** greedy vs random, 20 games, seed 1, 6-ply openings, 1 worker. From
  earlier report, not re-run.
- **Result:** 16W / 4D / 0L, win rate 80%, average 177.5 plies. Endings: 7
  checkmate, 9 stalemate (a win), 3 move-cap draws, 1 repetition draw.
- **Observation:** MEASURED: repetition draws went from 10/10 games to 1/20. The
  remaining draws are mostly 300-ply caps -- without a plan the bot still wanders
  once the captures run out (LIKELY).
- **Caveat:** 20 games; the 0-80% jump is far larger than the noise, but the exact
  rate is not precise.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Greedy tie-break: prefer checks (M2)

- **Commit/tag:** measured while building M2 (before `34027f9`).
- **What changed:** among equal-value moves, try a checking move first (computed with
  `board.gives_check`), then fall back to random.
- **Setup:** greedy vs random, 20 games, seed 1, 6-ply openings, 1 worker. From
  earlier report, not re-run.
- **Result:** 14W / 1D / 5L, win rate 70% (down from 80%). Five of the six losses to
  the random bot were `perpetual_check`: the greedy side was the one checking every
  move, so the rule scored the game as a loss. Average 142.1 plies.
- **Observation:** MEASURED: the check preference turned wins into rule-based losses
  -- 5 losses, all perpetual check. Greedy cannot tell "check" from "useful check";
  it checks because checking is the tie-break, and then repeats the check.
- **Caveat:** 20 games, so the 80% -> 70% drop is not statistically strong on its
  own, but the failure mode (perpetual-check losses) is unambiguous in the endings.
- **Verdict:** HURT.

---

## 2026-11-23 -- Greedy tie-break: MVV-LVA (M2)

- **Commit/tag:** measured while building M2 (before `34027f9`).
- **What changed:** among equal-value captures, prefer the cheapest attacker (most
  valuable victim / least valuable attacker); random among anything still tied.
- **Setup:** greedy vs random, 20 games, seed 1, 6-ply openings, 1 worker. From
  earlier report, not re-run.
- **Result:** 19W / 1D / 0L, win rate 95%, average 152.0 plies. Endings: 7
  checkmate, 12 stalemate, 1 move-cap draw, no perpetual checks.
- **Observation:** MEASURED: keeping the cheap attacker alive preserves material that
  the random tie-break used to trade away (a horse taking a cannon instead of a
  chariot doing it), and the material edge converts into more stalemate/mate wins.
  LIKELY: the 15-point jump also benefits from fewer self-inflicted trades.
- **Caveat:** 20 games, and the code has changed since.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Greedy: mate-in-one detection (M2)

- **Commit/tag:** `34027f9` / `m2-harness` (this is also the M2 acceptance run).
- **What changed:** before choosing, test the candidate moves that give check and
  play any that leaves the opponent with no legal move.
- **Setup:** greedy vs random, 100 games (50 per colour), seed 1, 6-ply openings,
  8 workers. Command: `python -m harness.tournament --bot-a greedy --bot-b random
  --games 100 --seed 1 --workers 8 --no-save`.
- **Result:** 98W / 2D / 0L, win rate 98% (target 90%). Average game 101.1 plies,
  0.41 ms per move. Endings: 84 checkmate, 14 stalemate, 2 move-cap draws.
  Average game length also fell from 152.0 to 101.1 plies.
- **Observation:** MEASURED: the 3-point win-rate gain is small, but the 51-ply drop
  in average game length shows the bot now finishes games it previously wandered
  through. LIKELY: most of the gain is converting near-mates instead of shuffling.
- **Caveat:** the "before" number (95%) came from a 20-game sample, so this is not a
  matched comparison; the 100-game figure is itself the acceptance sample.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Two-tier legality filter, first attempt (M3)

- **Commit/tag:** work in progress towards `f5c4af4` / `m3-alphabeta`.
- **What changed:** legal move generation stopped testing every pseudo-legal move.
  Without a check, only moves by the general or by a pinned piece were tested; the
  pin was found with four ray scans from the general instead of ~40 attack scans.
- **Setup:** `pytest tests/test_perft.py` plus perft by hand.
- **Result:** depth 2 = **1,926** (expected 1,920), depth 3 = **80,062** (expected
  79,666): +6 and +396 extra moves. No mismatch on the start position itself, only
  in the positions after one ply.
- **Observation:** MEASURED: two cannon cases were missing. Moving a piece *onto*
  the line between an enemy cannon and the general creates the required screen, and
  moving *away* one of two existing screens leaves exactly one -- both leave the
  general attacked, and neither involved a pinned piece. The perft tests caught it
  immediately, which is exactly what they are for.
- **Caveat:** none; the expected numbers are the published reference.
- **Verdict:** HURT.

---

## 2026-11-23 -- Cannon-screen guard (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** the legality filter now walks each ray once and returns both the
  pinned pieces and the empty squares that would become a cannon's screen, and also
  pins pieces when an enemy cannon has exactly two screens in front of it. A move is
  fully tested only if it is by the general, by a pinned piece, or lands on a
  "would-be screen".
- **Setup:** `pytest -q -m slow` (perft depth 4) and a manual comparison against the
  naive filter over 480 positions from 12 seeded random playouts.
- **Result:** perft back to 44 / 1,920 / 79,666 / 3,290,240, with depth 4 falling
  from **22.9 s to 4.98 s** (4.6x) and depth 3 from 0.50 s to 0.11 s. The 480 random
  positions matched the naive filter exactly.
- **Observation:** MEASURED: the speedup is real and the node counts did not move,
  so the shortcut is exact, not approximate. The regression test
  `test_fast_legal_moves_equal_the_naive_filter_on_random_positions` keeps it that
  way.
- **Caveat:** the speedup is on this machine only; the ratio should hold elsewhere.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Material evaluation: precomputed signed-value table (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** `material_balance` stopped looking up a dict and comparing colours
  per piece. It now reads a flat table of signed values indexed by
  `piece_code * 90 + square` (the crossed-soldier bonus baked in), sums, and flips
  the sign once for Black. The generic loop is still available by passing `values=`.
- **Setup:** 20,000 evaluations of the start position with
  `python -c "..."` (timing loop around `material_balance`).
- **Result:** **2.56 us** per evaluation (389,872/s) versus **17.01 us**
  (58,784/s) for the generic loop: 6.6x faster.
- **Observation:** MEASURED: the delta is a pure constant-factor improvement, and it
  shows up in the search as a higher node rate (roughly 4k -> 12k nodes/s across
  M3). LIKELY: most of that end-to-end gain comes from the legality filter, not the
  evaluation; the two were introduced in the same milestone and were not separated.
- **Caveat:** micro-benchmark of one function on one position; real leaves are not
  all identical.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Alpha-beta versus plain minimax (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** alpha-beta pruning with the same evaluation, terminal scoring,
  and repetition rule as the reference minimax.
- **Setup:** 50 middlegame positions from seeded random playouts
  (`tests/positions_middlegame.txt`), fixed depth 3, material evaluation, no
  ordering, no table. Command:
  `python -m tools.search_stats --limit 50 --depth 3`.
- **Result:** minimax **3,056,865 nodes / 212.4 s**; alpha-beta **430,538 nodes /
  29.6 s** (-86% nodes, 7.2x faster). **Root values identical on all 50 positions.**
- **Observation:** MEASURED: pruning alone is worth about 7x here. The node saving is
  smaller than the textbook "square root of the tree" because material-only values
  create many near-equal quiet lines (LIKELY).
- **Caveat:** one depth, one position set; the ratio depends on ordering (off here on
  purpose).
- **Verdict:** HELPED.

---

## 2026-11-23 -- Move ordering (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** transposition move first, then MVV-LVA captures, then two killer
  moves per ply, then the history heuristic.
- **Setup:** same 50 positions at depth 3, transposition table off.
  `python -m tools.search_stats --limit 50 --depth 3`.
- **Result:** **430,538 -> 84,905 nodes** (-80%), 29.6 s -> 6.0 s, **values
  identical**.
- **Observation:** MEASURED: ordering is the single largest M3 node saving. With a
  material-only evaluation many quiet moves tie, so a good first move (the previous
  iteration's best, or a capture) is what makes cutoffs happen early (LIKELY for the
  mechanism, MEASURED for the size).
- **Caveat:** none beyond the single position set.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Transposition table on transposition-rich positions (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** bounded transposition table keyed by the Zobrist hash, with
  exact/lower/upper bounds and mate scores converted to be node-relative.
- **Setup:** 10 sparse endgames (`tests/positions_transpositions.txt`), fixed depth 4,
  ordering on. `python -m tools.search_stats --limit 50 --depth 3` also prints this
  table.
- **Result:** 10,260 -> **8,476 nodes** (-17%), 0.50 s -> 0.34 s, values identical.
- **Observation:** MEASURED: spare pieces with free space have many move orders, so
  different lines land on the same position at the same remaining depth. The gain is
  modest because each position is cheap.
- **Caveat:** 10 hand-built positions; they were chosen *because* they transpose, so
  the rate is not typical.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Transposition table on the middlegame set (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** none beyond the previous entry; this is the same table measured
  where transpositions are rare.
- **Setup:** 50 middlegame positions at depth 3, then 5 of them at depth 4.
  `python -m tools.search_stats --limit 50 --depth 3`.
- **Result:** depth 3: **84,905 -> 84,905 nodes** (0%). Depth 4 on five positions:
  26,652 -> 24,617 nodes (-7.6%), values identical.
- **Observation:** MEASURED: at depth 3 two different three-ply orders almost never
  reach the same position with the same remaining depth, so there is nothing to
  reuse. At depth 4 the tree is wider and some transpositions appear. The table is
  not harmful, just useless at shallow depth.
- **Caveat:** the depth-4 figure is a 5-position sample, from an earlier report, not
  re-run at that size.
- **Verdict:** NEUTRAL.

---

## 2026-11-23 -- Quiescence on capture-free positions (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** quiescence search on captures at the horizon, behind a flag.
- **Setup:** the 6 positions in `tests/positions_quiet.txt` (generated and verified to
  contain no capture at the root or after any legal move), depth 3, quiescence on
  and off.
- **Result:** **6/6 identical root values.** On the middlegame set quiescence changes
  values as expected and costs **84,905 -> 194,497 nodes** (+129%).
- **Observation:** MEASURED: where there is nothing to capture, quiescence is
  provably a no-op on the value -- which is exactly the property the acceptance
  criterion asks for. The extra nodes on real positions are the price for not
  stopping in the middle of a trade.
- **Caveat:** "no capture in the position" is checked two plies deep by the generator,
  not exhaustively to depth 3.
- **Verdict:** NEUTRAL.

---

## 2026-11-23 -- Clock check interval 1024 -> 256 nodes (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** the iterative-deepening deadline is checked every 256 nodes
  instead of every 1024.
- **Setup:** alpha-beta with a 200 ms per-move cap, 100 games vs greedy, seed 1,
  6-ply openings, 8 workers. `python -m harness.tournament --bot-a alphabeta --bot-b
  greedy --games 100 --seed 1 --workers 8 --bot-a-params '{"time_limit_ms": 200}'`.
- **Result:** average **234.1 ms -> 200.8 ms** per move (p95 226 ms, max 264 ms, from
  349 ms). Node budget spent per move went down slightly (about 2,741 -> 2,291
  average nodes) because more of the budget is used and less is overshoot.
- **Observation:** MEASURED: at ~12k nodes/s one node is ~85 us, so 1024 nodes
  overshoots the deadline by up to ~87 ms; 256 bounds it to ~25 ms. Checking every
  node would add a clock read per node for no measurable benefit.
- **Caveat:** the win rate in the same period moved (95% -> 99%), but history seeding
  changed at the same time, so the win-rate change is not attributable to this tweak.
- **Verdict:** HELPED.

---

## 2026-11-23 -- History seeding of the search path (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** before searching, the bot puts the positions already played in the
  game on the search path, so returning to one scores 0 (a draw) instead of looking
  like progress.
- **Setup:** alpha-beta vs greedy, 200 ms cap, seed 1, 6-ply openings, 8 workers;
  100 games before the change (without seeding) and 100 after. Also a 20-game sample
  at fixed depth 3 before and after. Before numbers are from earlier report, not
  re-run (the code has changed).
- **Result:** 100 games: **95W 0D 5L (95%) -> 99W 0D 1L (99%)**, four of the five
  losses being `perpetual_check`. 20 games at fixed depth 3: **85% (3 perpetual-check
  losses) -> 100%**.
- **Observation:** MEASURED: in the lost games the bot was up about +3000 and kept
  checking; the four-ply repetition cycle was invisible at depth 3, so it never saw
  the repetition. With the game history on the path, those lines score 0 and the bot
  looks for progress instead. Still a draw score, never a loss: the
  perpetual-check-loses rule is deliberately not modelled in the search.
- **Caveat:** the before/after 100-game runs are the same seed and settings but not
  the same bot build; the effect (4 perpetual-check losses -> 0) is much larger than
  the 1-game residual noise.
- **Verdict:** HELPED.

---

## 2026-11-23 -- M3 acceptance: alpha-beta vs greedy (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** no new tweak; this is the milestone acceptance match with the
  final build.
- **Setup:** alpha-beta (`time_limit_ms=200`) vs greedy, 100 games (50 per colour),
  seed 1, 6-ply openings, 200 ms cap, 8 workers, 55.7 s wall.
  `python -m harness.tournament --bot-a alphabeta --bot-b greedy --games 100
  --seed 1 --workers 8 --bot-a-params '{"time_limit_ms": 200}' --no-save`.
- **Result:** **99W 0D 1L (99%)**, target 95%. Average game 47.8 plies; 86 checkmate,
  14 stalemate; alpha-beta averaged **2,291 nodes, depth 2.59, 200.8 ms per move**
  (p95 226 ms). Depth histogram: 103 moves at depth 1, 804 at 2, 1,070 at 3, 138 at
  4, 1 at 5.
- **Observation:** MEASURED: a pure-Python alpha-beta with a material-only evaluation
  and a 200 ms budget wins essentially every game against a one-ply greedy bot, and
  does it in under 50 plies on average. The shallow depths are enough because greedy
  hands over material.
- **Caveat:** only one opponent and one seed; "depth 2.59 on average" is a property of
  the cap, not of the search's maximum depth.
- **Verdict:** INCONCLUSIVE (acceptance check; there is no matched before/after for
  "alpha-beta exists", and the opponent differs from every earlier comparison).

---

## 2026-11-23 -- Iterative deepening with a 50 ms budget (M3)

- **Commit/tag:** `f5c4af4` / `m3-alphabeta`.
- **What changed:** no new tweak; a property check on the deadline handling and the
  "last completed depth" rule.
- **Setup:** five runs of `AlphaBetaBot(time_limit_ms=50)` on the start position, plus
  `pytest -q -k iterative_deepening`.
- **Result:** elapsed **50.0-66.7 ms**, completed **depth 2** every time, 768-1,024
  nodes, same move (`b2b9`) and score each run. The unit test that aborts the search
  artificially confirms the returned move and score come from the last completed
  depth.
- **Observation:** MEASURED: the search respects a tight budget to within one clock
  interval and never reports a partial iteration. The 16 ms spread is the 256-node
  check granularity plus machine noise (LIKELY).
- **Caveat:** no before/after; this is a correctness property, not a tweak.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- Piece-square tables (M4)

- **Commit/tag:** `m4-eval`.
- **What changed:** added the `pst` feature (piece-square tables, Black's tables being
  Red's flipped by rank) and the named evaluator `material_pst`.
- **Setup:** alpha-beta at fixed depth 3, `material` vs `material_pst`, 10 games
  (5 per colour), seed 1, 6-ply openings, 8 workers. Command: `python -m
  harness.tournament --bot-a alphabeta --bot-a-params '{"depth": 3, "evaluator":
  "material"}' --bot-b alphabeta --bot-b-params '{"depth": 3, "evaluator":
  "material_pst"}' --games 10 --seed 1 --workers 8 --no-save`.
- **Result:** `material_pst` **7W 1D 2L (70%)**, `material` 2W 1D 7L. Average game
  84.6 plies; 140.6 ms per move; 1,514 nodes per move.
- **Observation:** MEASURED: the PST side scored 70%, but with 10 games the 95%
  confidence interval is roughly 35-93%, so this does not establish a ranking.
  LIKELY: centralising horses and chariots and pushing soldiers helps at fixed depth,
  because the search sees the extra space but the opponent's counterplay is still one
  ply away.
- **Caveat:** 10 games is too small to rank evaluators; M6 is where this gets a real
  sample.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- Mobility and king safety: the `full` evaluator (M4)

- **Commit/tag:** `m4-eval`.
- **What changed:** added the `mobility` (pseudo-legal move difference) and
  `king_safety` (guards near the general, exposure penalties) features, giving the
  named evaluator `full`.
- **Setup:** alpha-beta at fixed depth 3, `material_pst` vs `full`, 10 games, seed 1,
  6-ply openings, 8 workers. Same command with the two evaluator names swapped. A
  third 10-game pairing, `material` vs `full`, was run for context.
- **Result:** `material_pst` **6W 1D 3L** vs `full` 3W 1D 6L -- `full` won **30%** in
  this `full`-vs-`material_pst` matchup. In the third pairing (10 games, same setup),
  `full` beat `material` **6W 0D 4L (60%)**. Average
  129.8 plies and 136.7 ms per move in the PST/full match.
- **Observation:** MEASURED: the three pairings are intransitive (PST > material,
  full > material, PST > full), which is what 10-game noise looks like. The negative
  direction for `full` is still worth noting. LIKELY: mobility double-counts material
  -- more pieces simply move more -- so the term can reward keeping material over
  making good trades, and flat guard bonuses in king safety may reward passivity.
  Testing that needs a larger sample and an ablation of each feature.
- **Caveat:** 10 games per pairing; fixed depth, so the extra evaluation cost does not
  explain the result. Do not rank evaluators from this.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- Evaluator cost table (M4)

- **Commit/tag:** `m4-eval`.
- **What changed:** no behaviour change; a measurement of what each evaluator costs.
- **Setup:** `python -m tools.eval_cost --limit 5 --depth 3` (2,000 evaluation calls
  per feature and evaluator, then a fixed-depth-3 search on five middlegame
  positions).
- **Result:** per evaluation: material **2.35 us**, pst **1.81 us**, mobility
  **38.04 us**, king safety **15.84 us**, `material_pst` **4.10 us**,
  `material_pst_mobility` **42.72 us**, `full` **61.05 us**. Search speed at depth 3:
  material 16,782 nodes/s (87.7 ms per move), `material_pst` 15,915 (94.3 ms),
  `material_pst_mobility` 9,335 (161.4 ms), `full` 7,948 (189.6 ms).
- **Observation:** MEASURED: mobility dominates the cost (~38 us, because it counts
  pseudo-legal moves for both sides), so `full` is about 2.2x slower per move than
  material at the same depth. In a time-capped game that becomes fewer plies, which
  the fixed-depth smoke matches deliberately do not capture.
- **Caveat:** one machine; the ms-per-move figures include everything the search does
  at that depth, so they show the end-to-end price, not the evaluation alone.
- **Verdict:** HURT (cost only; strength is unresolved).

---

## 2026-11-23 -- Positional clamps and evaluator bounds (M4 v2)

- **Commit/tag:** this commit, tag `m4-eval-v2`. The original `m4-eval` tag stays on
  the earlier commit, which the three entries above reference.
- **What changed:** every positional feature is now clamped after weighting to a named
  budget -- PST 150, mobility 200, king safety 150 centipawns -- so the positional
  total cannot exceed 500. Added `evaluate_from_red` (absolute Red view) and the
  mate-margin test. **No table value and no weight was changed.** The chariot
  open-file question is documented as a limitation: the central-file preference is a
  static proxy, and no open-file feature was added (the proposal lists four features).
- **Setup:** `pytest -q tests/test_eval.py`; `python -m tools.clamp_stats` over the 60
  middlegame positions, the 10 sparse endgames and the 6 quiet positions.
- **Result:** clamps fired on **0 of 60** middlegame, **0 of 10** endgame and **0 of 6**
  quiet positions (0.0% for each of the three features). Bounds: largest positional
  total 500 < chariot 900; largest evaluation 5,300 + 500 = 5,800; 5,800 + 300-ply mate
  adjustment = 6,100 < `MATE_SCORE` 100,000.
- **Observation:** MEASURED: the clamp never fires on these sets, so it is a
  structural guarantee for extreme positions, not a live tuning knob. The
  no-outweigh-a-chariot and mate-margin properties are now enforced by construction
  and asserted, rather than being usually true.
- **Caveat:** the sets are samples; a deliberately lopsided position can hit a budget
  -- which is exactly what the clamp is for.
- **Verdict:** HELPED.

---

## 2026-11-23 -- Evaluator smoke check at depth 2 (M4 v2)

- **Commit/tag:** `m4-eval-v2` (sanity check only; nothing tuned).
- **What changed:** none. This is the specified wiring check, run after the v2 change.
- **Setup:** fixed depth 2, 10 games per pairing (5 per colour), seed 1, 6-ply
  openings, 8 workers. Commands, one per richer evaluator (replace the evaluator
  name): `python -m harness.tournament --bot-a alphabeta --bot-a-params '{"depth": 2,
  "evaluator": "full"}' --bot-b alphabeta --bot-b-params '{"depth": 2, "evaluator":
  "material"}' --games 10 --seed 1 --workers 8 --no-save`.
- **Result:**

  | richer evaluator | W | D | L | win rate | avg plies | ms/move |
  |---|---|---|---|---|---|---|
  | material_pst | 5 | 2 | 3 | 50% | 60.0 | 16.0 |
  | material_pst_mobility | 8 | 2 | 0 | 80% | 58.2 | 24.6 |
  | full | 9 | 1 | 0 | 90% | 67.5 | 27.6 |

- **Observation:** MEASURED: every richer evaluator won at least half of its games and
  none lost badly (the "2 or fewer wins" trigger never fired), so no bug hunt was
  needed. LIKELY: the ordering PST < PST+mobility < full is real, since each step adds
  information, but 10 games cannot separate them -- the 95% interval for 5/10 is about
  20-80%.
- **Caveat:** 10 games is too noisy to rank evaluators. M6 runs the real comparison.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- Evaluator cost on the full middlegame set (M4 v2)

- **Commit/tag:** `m4-eval-v2`.
- **What changed:** none; this replaces the earlier 5-position cost measurement with
  the factory on all 60 M3 positions.
- **Setup:** `python -m tools.eval_cost --limit 60 --depth 3`.
- **Result:** per evaluation: material **2.31 us**, `material_pst` **4.20 us**,
  `material_pst_mobility` **43.38 us**, `full` **62.29 us**. Search at depth 3 over 60
  positions: material 101,720 nodes / 15,366 nodes/s / 110.3 ms per move;
  `material_pst` 107,309 / 14,753 / 121.2; `material_pst_mobility` 108,625 / 9,407 /
  192.4; `full` 107,735 / 8,113 / 221.3.
- **Observation:** MEASURED: mobility is the expensive feature (~39 us per call,
  counting pseudo-legal moves for both sides), so `full` is about twice as slow per
  move as material at the same depth. The node counts differ slightly because a
  different evaluation changes which lines are pruned, not the depth reached.
- **Caveat:** one machine; fixed depth, so the numbers show cost at equal depth and
  say nothing about strength.
- **Verdict:** HURT (cost only).

---

## 2026-11-23 -- Fixed-depth search with no time limit (M4 v2)

- **Commit/tag:** `m4-eval-v2`.
- **What changed:** nothing; a test of the behaviour the M6 depth-scaling experiment
  depends on.
- **Setup:** `AlphaBetaBot(depth=3, time_limit_ms=None)` with a counting fake clock on
  the start position; also `AlphaBetaBot()` with no depth and an empty budget, under
  `pytest -q -k fixed_depth`.
- **Result:** the depth-3 search completed depth 3 and read the clock **exactly twice**
  (once for the start time, once for `elapsed_ms`); no reads came from the node
  counter, confirming there is no deadline check. With no depth and no limit the bot
  falls back to `DEFAULT_DEPTH` (4) and still performs no clock checks.
- **Observation:** MEASURED: `_tick` short-circuits when `deadline is None`, so a
  fixed-depth search is a pure tree search. M6 can therefore use depth as the
  independent variable without the clock interfering.
- **Caveat:** property check, no before/after.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- MCTS added: acceptance against random (M5)

- **Commit/tag:** `m5-mcts`.
- **What changed:** plain UCT MCTS (select, expand, random rollout, backpropagate),
  values in [0, 1], the `full` evaluator squashed at the rollout cutoff, most-visited
  root child as the move.
- **Setup:** MCTS vs random, 100 games (50 per colour), seed 1, 6-ply openings, 200
  iterations per move, 8 workers, harness time limit 10 s (large enough that the
  iteration budget always decides). Command: `python -m harness.tournament --bot-a
  mcts --bot-a-params '{"iterations": 200}' --bot-b random --games 100 --seed 1
  --workers 8 --time-limit-ms 10000 --no-save`.
- **Result:** **100W 0D 0L (100%)**, target 95%. Average game 96.5 plies, 84
  checkmates and 16 stalemates (a win), 296.8 s wall clock, 0 games reached the
  300-ply cap.
- **Observation:** MEASURED: even 200 iterations wins every game against random, and
  the games end by mate or stalemate rather than the cap, so the tree's terminal
  scoring is working. The 200-iteration budget keeps a 100-game run at about five
  minutes; the bot's default is 1000.
- **Caveat:** acceptance check, no matched before/after -- the verdict convention
  calls that INCONCLUSIVE even though the target is met. Only one opponent.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- MCTS iteration scaling (M5)

- **Commit/tag:** `m5-mcts` (informational run; no default changed).
- **What changed:** nothing; the iteration budget was varied to see the scaling curve.
- **Setup:** MCTS vs greedy, 10 games per arm, colours swapped, seed 1, 6-ply
  openings, 8 workers, 10 s safety limit; 100, 400 and 1600 iterations.
  `python -m tools.mcts_sweep --sweep iterations`.
- **Result:**

  | iterations | W | D | L | win rate | iterations/s | avg depth | avg ms/move | avg plies |
  |---|---|---|---|---|---|---|---|---|
  | 100 | 1 | 1 | 8 | 10% | 552 | 1.47 | 181 | 141.6 |
  | 400 | 4 | 1 | 5 | 40% | 536 | 1.62 | 747 | 96.4 |
  | 1600 | 7 | 0 | 3 | 70% | 502 | 2.12 | 3186 | 78.6 |

- **Observation:** MEASURED: the win rate rises monotonically with the budget, and
  the games get shorter as the search plays better. At 100 iterations MCTS loses badly
  to a one-ply greedy bot: the tree is almost flat (most-visited line 1.47 plies) and
  30-ply random rollouts carry little signal. Even 1600 iterations only reach depth
  2.12, which explains why MCTS is not yet dominant.
- **Caveat:** 10 games per arm; the defaults (1000 iterations) were chosen by
  reasoning for playability, not from this table.
- **Verdict:** INCONCLUSIVE.

---

## 2026-11-23 -- MCTS exploration constant (M5)

- **Commit/tag:** `m5-mcts` (informational run; the default stays 1.4).
- **What changed:** nothing; the UCT exploration constant was varied.
- **Setup:** MCTS vs greedy, 400 iterations, 10 games per arm, colours swapped, seed
  1, 6-ply openings, 8 workers. `python -m tools.mcts_sweep --sweep exploration`.
- **Result:**

  | c | W | D | L | win rate | iterations/s | avg depth | avg ms/move | avg plies |
  |---|---|---|---|---|---|---|---|---|
  | 0.7 | 8 | 0 | 2 | 80% | 549 | 1.71 | 729 | 92.9 |
  | 1.4 | 4 | 1 | 5 | 40% | 557 | 1.62 | 718 | 96.4 |
  | 2.0 | 3 | 2 | 5 | 30% | 607 | 1.97 | 659 | 136.5 |

- **Observation:** MEASURED: the lower constant looked better in this sample, and the
  c = 1.4 row reproduces the 400-iteration row of the previous sweep game for game,
  which is a useful check that the search is deterministic given seed and iterations.
  LIKELY: at 400 iterations most children are under-sampled, so extra exploration
  spends visits on lines that have no time to become informative.
- **Caveat:** 10 games. The default remains 1.4 as specified; changing it on this
  evidence would be tuning to noise, and M6 is where a real comparison belongs.
- **Verdict:** INCONCLUSIVE.
