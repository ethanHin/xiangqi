# Xiangqi Bot-vs-Bot Search Project

A Xiangqi (Chinese Chess) engine, several search bots, a match harness, an
experiment suite, and a local web app for human-vs-bot and bot-vs-bot play with
replay.

**Status: M1 (engine core), M2 (baselines and match harness), M3 (alpha-beta), M4
(evaluation) and M5 (plain MCTS) complete.** Later milestones (experiments, server,
web UI) are described in `AGENTS.md`.

## Quick start

```bash
py -m venv .venv                       # or: python -m venv .venv
.venv\Scripts\activate                 # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]"

pytest                                 # fast tests (perft 1-3, quirks, rules)
pytest -m slow                         # perft depth 4, ~23 s
```

`pytest` skips the `slow` marker by default; everything else runs in about a
second. `ruff check .` and `ruff format .` keep the code tidy.

> NOTE: on this machine the `python` command points at the Windows Store stub and
> fails. Use `py` (the launcher) or the virtualenv's `.venv\Scripts\python.exe`.

## Layout

```
engine/            # the project's bottom layer, no dependencies above it
  types.py         # colors, piece codes, squares, Move, Result, history records
  zobrist.py       # incremental hashing keys (fixed seed, reproducible)
  board.py         # board state, FEN, make_move/unmake_move, attack detection
  movegen.py       # pseudo-legal + legal move generation, perft
  rules.py         # terminal detection: mate, stalemate, repetition, move cap
bots/              # one class per algorithm, all behind bots.base.Bot
  base.py          # Bot interface, Budget, SearchResult, build_bot, BOT_DEFAULTS
  random_bot.py
  greedy_bot.py
  alphabeta.py     # iterative-deepening alpha-beta, reference minimax, node counts
  mcts.py          # plain UCT MCTS: select, expand, rollout, backpropagate
eval/              # evaluation features
  material.py      # piece values, crossed-soldier bonus, material balance
  pst.py           # piece-square tables, mirrored for Black by flipping ranks
  mobility.py      # difference in pseudo-legal move counts
  king_safety.py   # advisors/elephants near the general, exposure penalties
  evaluator.py     # weighted combination; named evaluators for the bot registry
harness/           # play games and count the results
  match.py         # one game -> Game JSON record
  tournament.py    # N games, colour-swapped, optionally in parallel processes
  results.py       # win/draw/loss, averages, CSV rows
tools/             # developer scripts
  make_positions.py  # regenerate the fixed test position sets
  make_samples.py    # regenerate games/samples
  search_stats.py    # node-count table for each M3 enhancement
tests/             # pytest suite, including the fixed position sets
  positions_middlegame.txt
  positions_transpositions.txt
  positions_quiet.txt
games/             # saved game JSON (only samples/ is committed)
  samples/
docs/              # algorithms.md: how the search works, in plain language
```

How one game runs:

`harness.match.play_game` → build the board → `play_random_opening` (seeded RNG)
→ loop: `board.legal_moves()` → `rules.game_result` → `bot.choose_move(board, budget)`
→ validate the move → `board.make_move(move)` → record it → repeat until a result.
The history stays on the board, so the move cap and the repetition rule see the
opening plies too.

## Coordinates and notation

Squares are numbered `rank * 9 + file`, with rank 0 at Red's back rank (bottom),
rank 9 at Black's back rank (top), file `a` on Red's left and `i` on Red's right.
Moves use ICCS strings: `h2e2` means "from h2 to e2". Red starts on ranks 0-4,
Black on ranks 5-9, and Red moves first.

FEN uses the standard Xiangqi layout: Black's back rank first, uppercase for Red.
Only the piece placement and the side-to-move field are read; `to_fen()` always
writes the trailer as `- - 0 1` because Xiangqi has no castling, en passant, or
50-move clock in this project.

## Engine notes

**`make_move` is not a legality check.** It asserts pseudo-legality only: a piece
of the side to move is on the origin square and `move.captured` matches the
destination. Two callers filter legality themselves: `movegen.generate_legal_moves`
and the server, via `board.legal_moves()`. Legal move generation is two-tier: in
check, every move is played and tested; otherwise only moves that could expose the
general are tested (the general's own moves, pinned pieces, and moves that would
become a cannon's screen), which is what makes the search affordable.

**`make_move(move, record_check=False)`** skips storing whether the move gave
check. Game loops keep the default `True` because the perpetual-check rule needs
that flag for every ply; the search passes `False` to save one attack scan per
node. `harness.match` relies on the default: its perpetual-check test fails if the
loop takes the shortcut, because every recorded flag would then be `False`.

**Attack detection** scans outward from the target square instead of generating
the opponent's moves: rays for chariots, cannons, and the flying general, offsets
for horses (with the leg test), fixed squares for soldiers, advisors, and
elephants (with the eye test). `is_in_check` is that scan on the general's square,
so self-check, pins, and the flying-general rule are all one test.

**Hashes** are Zobrist keys generated from a fixed seed, including a side-to-move
key. `unmake_move` restores the previous hash exactly rather than recomputing it,
and `initial_hash` + per-ply `hash_after` are what the repetition rule reads.

## Rule readings

Where the rules leave room, this project takes the simplest reading and records
it here (and in the code that implements it):

- **Repetition** (`engine/rules.py`): a position (pieces plus side to move) that
  occurs three times ends the game. The "repeated cycle" is every move played
  since the *first* occurrence of the current position. If one side checked on
  every one of its moves in that window and the opponent checked on none, the
  checking side loses (`perpetual_check`); any other combination is a draw.
- **No legal moves** loses, whether the general is attacked (checkmate) or not
  (stalemate). Both are losses for the side to move.
- **Move cap** is 300 plies counted from the position the board was created at
  (for a played game, the position after the random opening). A mate on the same
  ply the cap is reached is a loss, not a draw: mate is checked first.
- **Flying general** is treated as an attack by the enemy general, so a move that
  opens the file between the two generals is illegal by the ordinary self-check
  test.

## Bots

Every bot implements `choose_move(board, budget) -> SearchResult`, where the
result carries the move plus `score` (centipawns, mover's view), `depth`, `nodes`,
and `elapsed_ms`. `Bot.spec` serialises the bot to the player-spec JSON that the
server and the Game JSON use.

| type | what it does |
|---|---|
| `random` | uniform random legal move, seeded |
| `greedy` | one-ply material with mate-in-one detection |
| `alphabeta` | iterative-deepening alpha-beta; params `depth`, `time_limit_ms`, `evaluator`, `quiescence` |
| `mcts` | plain UCT MCTS; params `iterations`, `time_limit_ms`, `exploration`, `rollout_depth`, `evaluator`, `capture_bias` |

`greedy` is the M2 yardstick for alpha-beta. How it picks a move, in order:

1. Score every legal move by the material it captures (a crossed-river soldier
   counts 200). The biggest capture wins.
2. Between equal-value captures, prefer the cheapest attacker (MVV-LVA): the horse
   takes the cannon and the chariot stays alive.
3. If a candidate gives mate in one, play it. Only checking moves can mate, so the
   expensive test runs on a handful of moves per position.
4. Otherwise pick at random among the tied candidates with the seeded RNG.

Steps 2-4 are not decoration. With a fixed tie-break ("first legal move") the bot
shuffles between the same two squares and drew 10 of 10 games by repetition;
MVV-LVA took it to 95% wins against random, and mate detection to 98%.

## Search: alpha-beta (M3)

`bots/alphabeta.py` follows the order AGENTS.md builds it in: plain minimax
(`reference_minimax`, kept as the test oracle), alpha-beta, move ordering, iterative
deepening, a transposition table, and quiescence behind a flag. `docs/algorithms.md`
walks through each part.

Three rules live inside the search:

* **No legal moves is a loss** for the side to move -- checkmate and stalemate
  alike (Xiangqi has no drawn stalemate) -- scored `-MATE_SCORE + ply`, so a
  faster mate outscores a slower one.
* **A repeated position scores 0.** The bot seeds the search path with the game's
  own history, so a depth-3 search cannot walk into a four-ply repetition it cannot
  see. The perpetual-check *loss* is deliberately not modelled inside the search;
  see the `# NOTE:` in `negamax`. `engine/rules.py` still adjudicates it, so the
  search may treat a line as a draw that the game scores as a loss.
* **Path-dependent scores never enter the transposition table.** A subtree that
  scored a repetition marks itself, and every node above it skips the store.

The clock is read every `CLOCK_CHECK_INTERVAL = 256` nodes rather than every node.
At roughly 12k nodes/s a node costs ~85 us, so 256 nodes bounds the overshoot to
about 25 ms; checking every node would add a call per node for no benefit.

### Measured node counts (`python -m tools.search_stats`)

Middlegame set, 50 positions, depth 3. The "same value" column compares every
configuration against plain minimax -- node counts alone would be meaningless if
the scores changed.

| configuration | total nodes | avg nodes | time | same value |
|---|---|---|---|---|
| plain minimax (reference) | 3,056,865 | 61,137 | 212.4 s | -- |
| alpha-beta | 430,538 | 8,611 | 29.6 s | yes |
| + move ordering | 84,905 | 1,698 | 6.0 s | yes |
| + ordering + transposition table | 84,905 | 1,698 | 6.0 s | yes |
| + ordering + TT + quiescence | 194,497 | 3,890 | 13.1 s | no (expected: quiescence changes leaf values) |

At depth 3 the table finds nothing to reuse in middlegame positions: two different
three-ply orders almost never land on the same position with the same remaining
depth. At depth 4 on five of the same positions it removes 7.6% of the nodes
(26,652 -> 24,617) for identical values. Sparse endgames are where transpositions
live, which is what the second set is for (10 positions, depth 4):

| configuration | total nodes | avg nodes |
|---|---|---|
| alpha-beta | 17,842 | 1,784 |
| + ordering | 10,260 | 1,026 |
| + ordering + TT | 8,476 | 848 |

Quiescence is judged by stability, not node count: on the six no-capture positions
in `tests/positions_quiet.txt`, values with and without quiescence are identical at
depth 3.

### Iterative deepening

The reported result is always the last *completed* depth. With a 50 ms budget on
the start position the bot returns in 50-67 ms having completed depth 2 (the
repetition test for this is in `tests/test_alphabeta.py`). At the 200 ms cap used
for the acceptance match the average is 201 ms per move, p95 226 ms.

### Acceptance

* 50 middlegame positions at depth 3: identical root value to plain minimax on all
  50, with fewer nodes (`pytest -m slow -k fifty_positions`).
* Alpha-beta vs greedy, 100 games, 200 ms cap, 8 workers: **99W 0D 1L (99%)**,
  average game 47.8 plies, endings 86 checkmate and 14 stalemate (a win). Alpha-beta
  averaged 2,291 nodes and depth 2.59 per move, with 1,070 of its 2,116 moves
  completing depth 3 or more; iterative deepening stops early when it proves a mate.

## Evaluation (M4)

Four features, each in its own module, each returning centipawns **for the side to
move** (negamax-compatible), combined by `eval/evaluator.py` with configurable
weights:

| feature | module | units of its weight | what it measures |
|---|---|---|---|
| material | `eval/material.py` | centipawns (weight 1 = use the value as is) | piece values; a soldier is worth 200 instead of 100 once it crosses the river |
| pst | `eval/pst.py` | centipawns per table value | where each piece stands: soldiers advance, horses centralise, chariots take central files, advisors/elephants stay home, the general stays back |
| mobility | `eval/mobility.py` | centipawns per extra pseudo-legal move (default 2) | pseudo-legal move difference, own minus opponent |
| king safety | `eval/king_safety.py` | centipawns per safety point | advisors and elephants near the general, penalties for a bare or forward general |

`DEFAULT_WEIGHTS = {"material": 1, "pst": 1, "mobility": 2, "king_safety": 1}`, and
the weights are a config dict, so an experiment can override them (`make_evaluator(...,
weights=...)`). `TEMPO_BONUS` is a named constant and is 0, because the start position
must evaluate to exactly 0.

`evaluate_from_red(board, evaluator)` gives the absolute view: the side-to-move score,
negated when Black is to move, so the same placement always reports the same number
and a position good for Red is positive whoever is to move.

Two bounds keep positional terms in their place:

* each positional contribution is **clamped after weighting** to a budget: PST 150,
  mobility 200, king safety 150 centipawns. The three together can never exceed 500,
  which is less than a chariot (900), so no combination of positional terms can
  outweigh winning a chariot -- by construction, not by luck;
* the largest possible evaluation (a full army with every soldier across the river,
  5,300, plus the 500 positional budget = 5,800) plus the 300-ply mate-distance
  adjustment is 6,100, far below `MATE_SCORE` (100,000), so a forced mate always
  outranks any heuristic score. `tests/test_eval.py` checks both inequalities and
  prints the numbers if they ever fail.

The clamps are a safety bound rather than a tuning knob: they fired on **0% of
positions** in the 60-position middlegame set and the sparse endgame and quiet sets
(`python -m tools.clamp_stats`).

Black's piece-square tables are Red's tables **flipped by rank** (`r -> 9-r`), which
is also the project's definition of "mirrored". The named evaluators add one feature
at a time, matching the M6 comparison: `material`, `material_pst`,
`material_pst_mobility`, `full`.

Acceptance properties, tested over real middlegame positions rather than hand-picked
ones, for all four evaluators:

* the same position from Red's view and from Black's view gives exact negatives;
* a mirrored + colour-swapped position with the side to move swapped scores
  identically;
* the start position scores 0, from the side to move and from Red's absolute view.

### Piece-square tables

Values are centipawns, columns are files a-i, ranks are printed from 9 (Black's back
rank) down to 0 (Red's back rank). Black uses the same table flipped by rank. The
values were set once by reasoning and have not been tuned by playing evaluators
against each other.

**Soldiers** -- no bonus at home, then rising with every step, with an extra jump when
the river is crossed (rank 5) and a peak in the centre of the enemy palace:

```
rank 9   26   36   50   66   78   66   50   36   26
rank 8   22   30   42   56   66   56   42   30   22
rank 7   18   24   32   44   52   44   32   24   18
rank 6   14   18   24   32   38   32   24   18   14
rank 5   10   12   16   22   26   22   16   12   10
rank 4    5    5    8   12   15   12    8    5    5
rank 3    0    0    0    0    0    0    0    0    0
rank 2    0    0    0    0    0    0    0    0    0
rank 1    0    0    0    0    0    0    0    0    0
rank 0    0    0    0    0    0    0    0    0    0
```

**Horses** -- centralisation: the centre gives more destinations and fewer blocked
legs, so rim squares are negative:

```
rank 9   -6   -3    0    3    4    3    0   -3   -6
rank 8   -3    0    3    6    7    6    3    0   -3
rank 7    0    3    6    9   10    9    6    3    0
rank 6    1    4    7   10   11   10    7    4    1
rank 5    2    5    8   11   12   11    8    5    2
rank 4    2    5    8   11   12   11    8    5    2
rank 3    1    4    7   10   11   10    7    4    1
rank 2    0    3    6    9   10    9    6    3    0
rank 1   -3    0    3    6    7    6    3    0   -3
rank 0   -6   -3    0    3    4    3    0   -3   -6
```

**Chariots** -- central files and advanced ranks. A piece-square table cannot see
whether a **file is open**; that depends on the other pieces, not on the square. The
central-file preference is the static proxy, because central files are the ones that
open first, while the rim files usually stay blocked by horses and elephants. A true
open-file term would need a separate occupancy feature, which the proposal does not
include:

```
rank 9    0    2    4    6    8    6    4    2    0
rank 8    2    4    6    8   10    8    6    4    2
rank 7    2    4    6    8   10    8    6    4    2
rank 6    2    4    6    8   10    8    6    4    2
rank 5    2    4    6    8   10    8    6    4    2
rank 4    2    4    6    8   10    8    6    4    2
rank 3    2    4    6    8   10    8    6    4    2
rank 2    2    4    6    8   10    8    6    4    2
rank 1    2    4    6    8   10    8    6    4    2
rank 0    0    2    4    6    8    6    4    2    0
```

**Cannons** -- the centre file early, when the cannon has screens to work with, and a
mild preference for staying on its own half:

```
rank 9    0    0    0    0    0    0    0    0    0
rank 8    0    0    0    2    2    2    0    0    0
rank 7    0    0    2    4    4    4    2    0    0
rank 6    0    2    4    6    6    6    4    2    0
rank 5    0    2    4    6    8    6    4    2    0
rank 4    0    2    4    6    8    6    4    2    0
rank 3    0    2    4    6    8    6    4    2    0
rank 2    0    2    4    6    8    6    4    2    0
rank 1    0    2    4    6    6    6    4    2    0
rank 0    0    0    2    4    4    4    2    0    0
```

**Advisors** -- palace points, with the centre point worth most:

```
rank 9    0    0    0    0    0    0    0    0    0
rank 8    0    0    0    0    0    0    0    0    0
rank 7    0    0    0    0    0    0    0    0    0
rank 6    0    0    0    0    0    0    0    0    0
rank 5    0    0    0    0    0    0    0    0    0
rank 4    0    0    0    0    0    0    0    0    0
rank 3    0    0    0    0    0    0    0    0    0
rank 2    0    0    0    3    0    3    0    0    0
rank 1    0    0    0    0   10    0    0    0    0
rank 0    0    0    0    5    0    5    0    0    0
```

**Elephants** -- home points and the centre of one's own half:

```
rank 9    0    0    0    0    0    0    0    0    0
rank 8    0    0    0    0    0    0    0    0    0
rank 7    0    0    0    0    0    0    0    0    0
rank 6    0    0    0    0    0    0    0    0    0
rank 5    0    0    0    0    0    0    0    0    0
rank 4    0    0    5    0    0    0    5    0    0
rank 3    0    0    0    0    0    0    0    0    0
rank 2    3    0    0    0   10    0    0    0    3
rank 1    0    0    0    0    0    0    0    0    0
rank 0    0    0    5    0    0    0    5    0    0
```

**General** -- the back rank is home, and the palace's forward rank is a small
penalty:

```
rank 9    0    0    0    0    0    0    0    0    0
rank 8    0    0    0    0    0    0    0    0    0
rank 7    0    0    0    0    0    0    0    0    0
rank 6    0    0    0    0    0    0    0    0    0
rank 5    0    0    0    0    0    0    0    0    0
rank 4    0    0    0    0    0    0    0    0    0
rank 3    0    0    0    0    0    0    0    0    0
rank 2    0    0    0   -8  -12   -8    0    0    0
rank 1    0    0    0    0    4    0    0    0    0
rank 0    0    0    0    5    8    5    0    0    0
```

### What the evaluators cost

`python -m tools.eval_cost --limit 60 --depth 3` on the M3 middlegame set (60
positions, fixed depth 3):

| evaluator | us per eval | search nodes | nodes/s | ms per move |
|---|---|---|---|---|
| material | 2.31 | 101,720 | 15,366 | 110.3 |
| material_pst | 4.20 | 107,309 | 14,753 | 121.2 |
| material_pst_mobility | 43.38 | 108,625 | 9,407 | 192.4 |
| full | 62.29 | 107,735 | 8,113 | 221.3 |

Mobility dominates the cost (about 39 us per call: it counts pseudo-legal moves for
both sides), so `full` is about twice as slow per move as material at the same depth.
The node counts differ a little because a different evaluation changes which lines
are pruned, not how deep the search goes.

### Sanity smoke check (not a ranking)

Fixed depth 2, 10 games each against `material` (5 games per colour), seed 1, 6-ply
openings, 8 workers. This is a wiring check, not a comparison:

| richer evaluator | W | D | L | win rate |
|---|---|---|---|---|
| material_pst | 5 | 2 | 3 | 50% |
| material_pst_mobility | 8 | 2 | 0 | 80% |
| full | 9 | 1 | 0 | 90% |

**Ten games is far too noisy to rank evaluators**: the 95% confidence interval for
5/10 spans roughly 20-80%. The only conclusions are that the names are wired
correctly, that no richer evaluator loses badly, and that material alone is a weak
opponent at depth 2. M6 runs the real comparison with a proper sample. The full log,
including the earlier depth-3 exploratory matches (which disagreed with each other),
is in `docs/experiment_log.md`.

## MCTS (M5)

`bots/mcts.py` is plain UCT MCTS: selection by UCT, expansion of one child at a time,
a random rollout capped at `rollout_depth` plies, and backpropagation. `docs/algorithms.md`
walks through each phase; the points that matter for reading the numbers:

* Node values are stored **for the player who moved into the node**, and backprop
  flips `reward -> 1 - reward` at every edge. A hand-built-tree test pins the sign
  down, because a sign bug makes MCTS play to lose.
* Every value is in **[0, 1]** (loss 0, draw 0.5, win 1; evaluations squashed with
  `0.5 + 0.5*tanh(score/600)`), which is the range the UCT exploration constant
  assumes.
* The move is the **most-visited** root child. `nodes` = iterations completed,
  `depth` = length of the most-visited line, `score` = the root value converted back
  with the inverse squash and clamped to +/-5,000 so it never looks like a mate score.
* Terminal detection at tree nodes uses the engine's `game_result` (including
  repetition). Rollouts use `record_check=False`, so repetition and the
  perpetual-check rule are not modelled inside them (see the `# NOTE:`).
* No board copies: the search walks the real board and unwinds in a `finally`; a test
  checks the FEN, hash and history length are unchanged after `choose_move`.

Defaults: `iterations=1000`, `time_limit_ms=None`, `exploration=1.4`,
`rollout_depth=30`, `evaluator="full"`, `capture_bias=0.0`. If both budgets are set
the search stops at whichever comes first.

### Acceptance

MCTS vs random, 100 games, 200 iterations per move, seed 1, 8 workers:
**100W 0D 0L (100%)** (target 95%), average game 96.5 plies, 84 checkmates and 16
stalemates, 4:57 wall clock, no game reached the 300-ply cap.

### Informational sweeps (10 games per arm: not a ranking)

Against greedy, 8 workers, seed 1:

| arm | W | D | L | win rate | iterations/s | avg depth | avg ms/move |
|---|---|---|---|---|---|---|---|
| 100 iterations | 1 | 1 | 8 | 10% | 552 | 1.47 | 181 |
| 400 iterations | 4 | 1 | 5 | 40% | 536 | 1.62 | 747 |
| 1600 iterations | 7 | 0 | 3 | 70% | 502 | 2.12 | 3186 |
| c = 0.7, 400 iterations | 8 | 0 | 2 | 80% | 549 | 1.71 | 729 |
| c = 1.4, 400 iterations | 4 | 1 | 5 | 40% | 557 | 1.62 | 718 |
| c = 2.0, 400 iterations | 3 | 2 | 5 | 30% | 607 | 1.97 | 659 |

The trend with iterations is monotone and the two 400-iteration runs are the same
games, which confirms the seeded search is reproducible. The absolute level is the
interesting part: at 100 iterations MCTS loses badly to a one-ply greedy bot, and even
1600 iterations only reach depth ~2, because random 30-ply rollouts carry a weak
signal. Ten games per arm cannot rank the settings, the defaults were chosen by
reasoning, and M6 is where MCTS gets a real comparison.

## Tournament harness

```bash
python -m harness.tournament --bot-a greedy --bot-b random --games 100 --workers 8
```

* Games come in pairs: the same opening seed, colours swapped. `--games` must be
  even. `--seed`, `--opening-plies` (default 6, 0-8), `--time-limit-ms` (default
  200), `--save-dir`, and `--no-save` are the other options.
* With `--workers > 1` the games run in a `ProcessPoolExecutor`. On Windows that
  means spawn, so `play_job` is a module-level function, `MatchJob` holds only
  picklable values, and the pool is created under a `__main__` guard.
* Every game is written to `games/<id>.json` unless `--no-save` is given.
* `--workers` defaults to one process per game, capped at the CPU count.

Measured acceptance run (100 games, seed 1, 6-ply openings, 8 workers):

| bot | W | D | L | win rate | score |
|---|---|---|---|---|---|
| greedy | 98 | 2 | 0 | 98% | 99% |
| random | 0 | 2 | 98 | 0% | 1% |

Average game length 101.3 plies; average move 0.27 ms; 84 checkmates, 14
stalemates (also a win), 2 move-cap draws. Reproduce with
`pytest -m slow -k greedy_beats_random`, or run the CLI above.

## Game JSON notes

* `plies` counts every ply including the random opening, so
  `plies == len(opening_moves) + len(moves)`. `start_fen` is the position after
  the opening; the replay screen only needs `start_fen` and `moves`.
* `time_ms` is the wall-clock time the harness measured around `choose_move`
  (not the bot's self-report), so a bot that ignores its budget is visible.
* `eval` is the bot's `score`; `depth` and `nodes` are 0 for the simple bots.

`games/samples/` holds three committed examples: greedy vs random, random vs
random (a 300-ply cap draw), and an 8-ply perpetual check. Regenerate them with
`python -m tools.make_samples`.

## Perft

Reference node counts from the standard starting position:

| depth | nodes   | time      |
|-------|---------|-----------|
| 1     | 44      | < 0.01 s  |
| 2     | 1,920   | < 0.01 s  |
| 3     | 79,666  | 0.11 s    |
| 4     | 3,290,240 | 5.0 s   |

Depth 4 runs behind `pytest -m slow`. The expected values are published Xiangqi
perft numbers; if the engine ever disagrees, the engine is fixed, never the table.
The move generator only re-tests legality for moves that can actually expose the
general (moves by a pinned piece, by the general, or onto a line where a piece would
become a cannon's screen), which is what took depth 4 from 23 s in M1 to 5 s.

## Roadmap

- ~~M2 baselines and match harness~~ (done)
- ~~M3 alpha-beta~~ (done)
- ~~M4 evaluation (material, piece-square tables, mobility, king safety)~~ (done)
- ~~M5 plain MCTS~~ (done)
- M6 experiments (depth scaling, heuristic comparison)
- M7 replay viewer
- M8 live play server and web UI

AlphaZero-style neural MCTS is explicitly future work and has no code here.
