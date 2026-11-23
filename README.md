# Xiangqi Bot-vs-Bot Search Project

A Xiangqi (Chinese Chess) engine, several search bots, a match harness, an
experiment suite, and a local web app for human-vs-bot and bot-vs-bot play with
replay.

**Status: M1 (engine core) and M2 (baselines and match harness) complete.**
Later milestones (alpha-beta, evaluation, MCTS, experiments, server, web UI) are
described in `AGENTS.md`.

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
  base.py          # Bot interface, Budget, SearchResult, build_bot
  random_bot.py
  greedy_bot.py
eval/              # evaluation features
  material.py      # piece values shared by greedy (M2) and the evaluator (M4)
harness/           # play games and count the results
  match.py         # one game -> Game JSON record
  tournament.py    # N games, colour-swapped, optionally in parallel processes
  results.py       # win/draw/loss, averages, CSV rows
tools/             # developer scripts (sample-game generator)
tests/             # pytest suite
games/             # saved game JSON (only samples/ is committed)
  samples/
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
("play it, is my general safe, undo it") and the server, via `board.legal_moves()`.
This keeps the search hot loop free of redundant work.

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
| `greedy` | one-ply material: biggest capture, MVV-LVA tie-break, mate-in-1 detection |

`greedy` is the M2 yardstick for alpha-beta. Two details worth knowing:

* Ties are broken with a seeded RNG. Picking a fixed move from generator order
  makes the bot shuffle between the same squares and draws every game by
  repetition, which would make the baseline useless.
* It also takes mate in one. Only checking moves can mate, so the test runs on a
  handful of moves per position. Without it, greedy wins material but random-walks
  instead of converting, and about one game in eight hits the 300-ply cap.

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

Measured acceptance run (100 games, seed 1, 6-ply openings, 8 workers, about
1.1 s wall clock):

| bot | W | D | L | win rate | score |
|---|---|---|---|---|---|
| greedy | 98 | 2 | 0 | 98% | 99% |
| random | 0 | 2 | 98 | 0% | 1% |

Average game length 101.1 plies; average move 0.41 ms; 84 checkmates, 14
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
| 2     | 1,920   | 0.01 s    |
| 3     | 79,666  | 0.50 s    |
| 4     | 3,290,240 | 22.9 s  |

Depth 4 runs behind `pytest -m slow`. The expected values are published Xiangqi
perft numbers; if the engine ever disagrees, the engine is fixed, never the table.

## Roadmap

- ~~M2 baselines and match harness~~ (done)
- M3 alpha-beta (minimax → alpha-beta → ordering → iterative deepening → TT → quiescence)
- M4 evaluation (material, piece-square tables, mobility, king safety)
- M5 plain MCTS
- M6 experiments (depth scaling, heuristic comparison)
- M7 replay viewer
- M8 live play server and web UI

AlphaZero-style neural MCTS is explicitly future work and has no code here.
