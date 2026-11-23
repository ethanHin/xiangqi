# Xiangqi Bot-vs-Bot Search Project

Build a Xiangqi (Chinese Chess) engine, several search bots, a match harness, and a web app for live play and replay, then use them to run two experiments. Work through the milestones in order. Do not start a milestone until the previous one's acceptance criteria pass.

## Decisions (already made, do not revisit)

- Language: Python 3.11+, standard library first. Optional: `numpy`, `matplotlib`. Required for tests: `pytest` (every milestone runs it). `ruff` is allowed as a dev tool for linting and formatting. For the web server (M8) the only extra dependencies allowed are `fastapi`, `uvicorn`, and `httpx` (for tests). Do not add other heavy dependencies without asking.
- Frontend: a plain HTML/JS web app (no framework, no build step) with three screens: game setup, live play, and replay. It supports human vs bot and bot vs bot (human vs human is not supported). The user picks who plays each side and, for each bot, which algorithm and settings it uses. There is no rules logic in the frontend: legality, check, and game-over detection all come from the backend.
- Scope: alpha-beta (primary bot) and plain MCTS (comparison). AlphaZero-style neural MCTS is future work; leave a note in the README, build nothing for it.
- Opening: N random legal plies from a seeded RNG (default N=6, configurable 4-8).
- Repetition: threefold repetition is a draw, except that a side giving perpetual check loses (see Rules). Implemented behind one swappable function.

### Decisions recorded during review

These were settled while reviewing the spec. They are binding; do not revisit.

- Game-over reasons: `reason` gains `resign` alongside the engine reasons. Resignation is a server-level result; `engine/rules.py` never produces it.
- Undo: `undo` never goes below the post-opening position (ply 0 = the position after the random opening moves). Undo is allowed after a terminal result and clears `result`/`reason`.
- Terminal precedence: checkmate or stalemate on the same ply the 300-ply cap is reached is a loss for the side to move, not a draw. Check for mate first, then apply the cap.
- Game JSON: `red` and `black` are player-spec objects (the same shape `POST /api/games` accepts), not free text, so an exported game can be reposted to recreate it.
- Opening plies count toward the 300-ply cap. A random opening ply that would repeat a position for the third time or end the game is re-drawn.
- Evaluator names are a closed enum in `server/registry.py`: `material`, `material_pst`, `material_pst_mobility`, `full`. Anything else is rejected with HTTP 400.
- Server budget clamp: every bot call is limited to `min(requested, 10 s)` and a maximum iteration count, regardless of the requested parameters; the response reports the budget actually used.
- `board.make_move` asserts pseudo-legality only (it does not check that the mover's own general is safe), because the search hot loop filters legality itself. The server validates human moves against `board.legal_moves()`.
- The 100-game acceptance matches (M2 greedy vs random, M3 alpha-beta vs greedy, M5 MCTS vs random) stay at 100 games, but run with a per-move time cap and in parallel processes so they finish in well under an hour.
- Perpetual check: the window is the moves since the first occurrence of the repeated position. If the checking side checked on all of its own moves in that window and the opponent checked on none, the checker loses; if both sides checked on all of their moves it is a draw; any other combination of checking is a draw.
- `GET /api/games` lists saved and in-progress games (id, players, result, plies, created) so the Replay screen can find games in `games/`.
- Version control: the repository is initialised with `git init`. `games/*.json` is gitignored except samples, along with `__pycache__`, `.venv`, and `experiments/results/`.

## Rules

Board is 9 files x 10 ranks. Each side has 16 pieces: 1 general, 2 advisors, 2 elephants, 2 horses, 2 chariots, 2 cannons, 5 soldiers.

Starting position (FEN, Red to move, Red at the bottom):

```
rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1
```

Uppercase = Red, lowercase = Black. Letters: r chariot, n horse, b elephant, a advisor, k general, c cannon, p soldier.

Movement:
- General: one step orthogonally, stays inside its 3x3 palace.
- Advisor: one step diagonally, stays inside the palace.
- Elephant: exactly two steps diagonally, cannot cross the river, blocked if the diagonal midpoint ("eye") is occupied.
- Horse: one step orthogonally then one diagonally outward, blocked if the orthogonal first square ("leg") is occupied.
- Chariot: slides any distance orthogonally like a rook.
- Cannon: moves like a chariot without capturing; captures by jumping over exactly one piece (the screen) to land on an enemy piece.
- Soldier: one step forward only before crossing the river; after crossing, one step forward or sideways. Never backward.

Special rules:
- Flying general: the two generals may never face each other on an open file with no piece between. Any move that causes this is illegal.
- A move is illegal if it leaves the mover's own general attacked (including by flying general).
- No legal moves (checkmate or stalemate) = the side to move loses.
- Move cap: 300 plies total (150 per side) is a draw.
- Repetition: if the same position (same pieces, same side to move) occurs 3 times, the game ends. Draw by default. If during the repeated cycle one side gave check on every one of its moves and the other did not, the checking side loses. If both sides were checking every move, draw.
- Implement repetition as `repetition_result(history) -> Optional[Result]` in `engine/rules.py` so the rule can be swapped without touching the game loop.

Utility from a given player's view: +1 win, 0 draw, -1 loss.

## Repo layout

```
xiangqi/
  AGENTS.md
  README.md
  pyproject.toml
  engine/
    board.py        # Board class, FEN parse/serialize, make_move/unmake_move
    movegen.py      # pseudo-legal and legal move generation
    rules.py        # terminal detection, repetition, move cap
    zobrist.py      # Zobrist hashing
    types.py        # Move, Piece, Color, Result
  bots/
    base.py         # Bot interface and SearchResult
    random_bot.py
    greedy_bot.py   # one-ply material
    alphabeta.py
    mcts.py
  eval/
    material.py
    pst.py
    mobility.py
    king_safety.py
    evaluator.py    # weighted combination, configurable weights
  harness/
    match.py        # play one game
    tournament.py   # many games, colors swapped, seeded openings
    results.py      # aggregate win/draw/loss, avg move time, avg nodes
  experiments/
    depth_scaling.py
    heuristic_comparison.py
  server/
    app.py          # FastAPI app (thin layer over engine, bots, harness)
    registry.py     # bot registry: names, parameter schemas, defaults, factory
    games.py        # in-memory game store, per-game lock, saving finished games
    __main__.py     # `python -m server` starts uvicorn and serves web/
  web/
    index.html      # app shell: setup, play, replay screens
    app.js          # screen routing and API calls
    board.js        # board rendering and click handling
    replay.js       # replay screen
    style.css
  games/            # saved game JSON (gitignored except samples)
  tests/
```

## Engine API (keep this stable; bots depend on it)

- `Board.from_fen(fen)`, `board.to_fen()`, `Board.start()`
- `board.side_to_move`
- `board.legal_moves() -> list[Move]`
- `board.make_move(move)` and `board.unmake_move()` (must restore state exactly, including hash and repetition history)
- `board.is_in_check(color) -> bool`
- `board.gives_check(move) -> bool` (needed for repetition rule)
- `board.hash` (Zobrist, updated incrementally)
- `rules.game_result(board) -> Optional[Result]` returning win/loss/draw with reason (checkmate, stalemate, repetition, perpetual_check, move_cap)
- Board is mutable with make/unmake. Search must never deep-copy the board per node.

Use a flat 90-square array internally with piece codes. Moves are lightweight (from, to, captured) tuples or a `__slots__` class.

## Milestones

### M1: Engine core
Implement board, FEN, move generation, legality filter, make/unmake, terminal detection, Zobrist hashing.

Acceptance:
- Start position has 44 legal moves for Red.
- `make_move` then `unmake_move` over random games restores identical FEN and hash every time.
- Perft from the start position matches known Xiangqi values: depth 1 = 44, depth 2 = 1920, depth 3 = 79666. Depth 4 = 3290240 is desirable but may be slow in pure Python; run it behind a `--slow` pytest marker. If Pikafish or another engine is installed locally, cross-check; otherwise treat these published numbers as the reference and flag any mismatch rather than adjusting the numbers.
- Unit tests for each quirk: blocked horse leg, blocked elephant eye, elephant river limit, palace limits for general and advisor, cannon with zero, one, and two screens, cannon cannot capture without a screen, soldier before and after the river, flying general illegal move, move that exposes own general illegal.

### M2: Baselines and match harness
Random bot, greedy-material bot, match runner, tournament runner.

Acceptance:
- Bot interface: `choose_move(board, budget) -> SearchResult`, where `SearchResult` is a dataclass in `bots/base.py` holding `move`, `score`, `depth`, `nodes`, and `elapsed_ms`. Simple bots (random, greedy) return `depth=0, nodes=0`. `score` is in centipawns from the mover's point of view and is what the API exposes as `eval`. Declared at M2 so the M8 game state never needs a refactor.
- Tournament plays N games between two bots, swapping colors, with seeded random openings so runs are reproducible.
- Every game terminates (300-ply cap). Output per game: result, reason, plies, move list, per-move time.
- Greedy bot beats random bot in at least 90% of 100 games.
- Games saved as JSON in `games/` (schema below).

### M3: Alpha-beta bot
Build incrementally, verifying after each step.

1. Plain minimax (depth-limited) with material-only eval.
2. Alpha-beta. Test: returns the same root value as plain minimax on 50+ random positions at depth 3.
3. Move ordering: captures first by MVV-LVA, then killer/history moves.
4. Iterative deepening with a time or depth budget.
5. Transposition table keyed by Zobrist hash (store depth, value, bound type, best move).
6. Quiescence search on captures (optional, behind a flag).

Terminal scores: win = +large, loss = -large, with mate-distance adjustment so faster wins are preferred. A true win or loss always outranks any heuristic score. Report nodes searched and depth reached per move.

Acceptance (each item measured on a fixed set of positions, not on ad-hoc runs):
- Alpha-beta returns the same root value as plain minimax on 50+ random positions at depth 3, and searches strictly fewer nodes.
- Move ordering (MVV-LVA + killers/history) searches fewer nodes than unordered alpha-beta at fixed depth.
- The transposition table searches fewer nodes at fixed depth on a position set that contains transpositions.
- Iterative deepening is judged by budget, not by nodes: it always returns a move within the time budget (tested with a tight budget such as 50 ms).
- Quiescence is judged by result stability, not by nodes: it does not change root values on quiet positions.
- Alpha-beta at depth 3+ beats the greedy bot in at least 95% of 100 games.

### M4: Evaluation function
Weighted sum of features, each in its own module, weights in a config dict so experiments can vary them.

- Material: chariot 900, cannon 450, horse 400, advisor 200, elephant 200, soldier 100 (200 after crossing the river). Tunable.
- Piece-square tables per piece type and side (mirrored for Black): soldiers gain value as they advance, horses prefer the center, chariots prefer open files.
- Mobility: difference in legal move counts (use pseudo-legal counts for speed).
- King safety: bonus for intact advisors and elephants near the general; penalty for exposed general.

Evaluation is from the side to move's perspective (negamax-compatible). Acceptance:
- The same position evaluated from Red's view and from Black's view gives exact negatives.
- A mirrored, color-swapped position with the side to move also swapped gives an identical score. "Mirrored" means the board is flipped vertically (rank r becomes rank 9-r) and the colors are swapped. Black's piece-square tables are defined as Red's tables flipped by rank, which is what makes this test hold even when a table is not left-right symmetric.
- The start position evaluates to 0 from Red's view unless a tempo bonus is enabled, and any tempo bonus is a named constant (`TEMPO_BONUS`, default 0 = off).

### M5: Plain MCTS bot
UCT selection, expansion, rollout, backpropagation.

- Rollouts: random with a depth cap, with the evaluation function used at the cutoff (squashed to [-1, 1], e.g. tanh(score / scale)). Optional light heuristic guidance (prefer captures) behind a flag.
- Configurable: iteration count or time budget, exploration constant, rollout depth.
- Terminal nodes use exact utility +1/0/-1.

Acceptance: MCTS beats the random bot in at least 95% of 100 games, and every game terminates within the 300-ply cap.

### M6: Experiments
Both run through the tournament harness and write CSV plus plots.

1. Depth scaling: alpha-beta with the full evaluation, depth d vs depth d+1, seeded games per pairing. Fixed depth, no per-move time cap, so each depth really is the depth claimed. Default pairings are 1 vs 2 and 2 vs 3; 3 vs 4 is available via an explicit flag because it is much slower. Report win/draw/loss, average depth reached, average nodes and average time per move.
2. Heuristic comparison: alpha-beta at one fixed depth (default 3) with different evaluators (material only, material + PST, + mobility, full). Round-robin, colors swapped. Depth is the fixed variable, so use fixed depth with only a generous safety time cap (for example 5 s per move) to prevent stalls; record how often the cap was hit and the average depth reached. Also include MCTS vs alpha-beta as a comparison row; this row alone uses equal time per move (500-1000 ms), because MCTS has no depth.

Both scripts take `--games` (default 10, must be even so colors split evenly, CLI errors otherwise), `--quick` (2 games at low depth, for smoke tests), and `--seed`. Games run in parallel processes (`ProcessPoolExecutor`; threads do not help because of the GIL). Every report includes average depth reached, average nodes and average time per move alongside win/draw/loss. Full runs such as `--games 100` are only launched deliberately with explicit arguments.

Save raw results to `experiments/results/` (CSV) and plots as PNG. If matplotlib is not installed, write the CSV and skip the plots with a warning rather than failing. Write a short `experiments/README.md` explaining how to reproduce.

### M7: Replay viewer
Static page in `web/`. Loads a game JSON (file picker or `?game=path`), draws the 9x10 board with river and palace, steps forward and back through moves, shows move number, result, and the bots' names. No rules logic; it only applies recorded moves. Render pieces as text characters or simple SVG; no image assets required.

Acceptance: replays a saved game correctly end to end, both served via `python -m http.server` and opened by double-clicking `index.html`. A page opened from `file://` cannot `fetch()` JSON files, so standalone mode must use the file picker; the `?game=path` parameter only works when the page is served over HTTP. In M8 this page becomes the Replay screen of the full app (`replay.js`); keep it working standalone.

### M8: Live play server and play UI
Add human vs bot and bot vs bot play on top of the existing engine and bots. Human vs human is out of scope: a game must have at least one bot. The server is a thin layer: it never reimplements rules, it calls `engine/` and `bots/`.

**Player spec.** Each side is described by a player spec:

```json
{"type": "human"}
{"type": "alphabeta", "params": {"depth": 4, "time_limit_ms": 2000, "evaluator": "full"}}
{"type": "mcts", "params": {"iterations": 2000, "exploration": 1.4, "rollout_depth": 30}}
{"type": "greedy", "params": {}}
{"type": "random", "params": {}}
```

`server/registry.py` is the single source of truth for available bots: name, label, parameter list with types, ranges, and defaults, and a factory that builds the bot. Adding a new bot to the registry must make it appear in the UI with no frontend changes.

**API** (JSON, all endpoints are plain `def` so FastAPI runs bot search in its threadpool):
- `GET /api/bots`: list bot types with parameter schemas and defaults.
- `POST /api/games`: body `{red, black, seed?, opening_plies?, start_fen?}` where `red` and `black` are player specs. Creates a game (random opening applied as in the harness) and returns the game state. Returns 400 if both sides are `human`.
- `GET /api/games`: list saved and in-progress games (id, players, result, plies, created) for the Replay screen.
- `GET /api/games/{id}`: current state.
- `POST /api/games/{id}/move`: body `{move: "h2e2"}`. Only valid when the side to move is a human. Validates legality server-side, applies it, returns the new state. Illegal move or wrong turn returns HTTP 400 with a clear reason.
- `POST /api/games/{id}/step`: asks the bot on the side to move to play one move, applies it, returns the new state including the bot's depth, nodes, time, and eval. Returns 400 if a human is to move or the game is over.
- `POST /api/games/{id}/undo`: undo the last human move and the bot reply (human vs bot) or the last ply (otherwise). Never goes below the post-opening position; allowed after a terminal result, clearing `result`/`reason`.
- `POST /api/games/{id}/resign`: body `{color}`.
- `GET /api/games/{id}/export`: the finished or in-progress game in the Game JSON schema.

**Game state response:**

```json
{
  "id": "string",
  "fen": "string",
  "side_to_move": "red | black",
  "players": {"red": {"type": "human"}, "black": {"type": "alphabeta", "params": {}}},
  "legal_moves": ["h2e2"],
  "in_check": false,
  "last_move": "h2e2",
  "moves": [{"move": "h2e2", "time_ms": 12.3, "nodes": 4521, "depth": 4, "eval": 35}],
  "result": null,
  "reason": null,
  "plies": 0
}
```

`result` and `reason` use the same values as the Game JSON schema once the game ends. Finished games are automatically written to `games/` so the Replay screen can load them.

**Server behavior:**
- In-memory store keyed by game id, with a lock per game so two requests cannot mutate one game at the same time. Separate games must be fully independent.
- Bot search must respect a time or iteration budget so a request cannot hang. Enforce a server-side maximum (for example 10 seconds per move) regardless of the requested parameters.
- Validate player spec parameters against the registry schema; reject out-of-range values with 400.
- The same repetition, perpetual check, stalemate, and move-cap rules apply to human games and are reported in `reason`.
- `python -m server` starts uvicorn on localhost and serves `web/` as static files, so one command runs the whole app.

**Frontend screens:**
1. Setup: for Red and Black separately, choose Human or a bot; for a bot, choose the algorithm from `GET /api/bots` and edit its parameters (depth, time limit, evaluator, iterations, exploration constant, and so on, generated from the schema). Optional seed and opening plies. Do not allow Human for both sides (disable the second Human option or the Start button). "Start game" creates the game.
2. Play: 9x10 board with river and palace. When a human is to move, clicking a piece highlights it and its legal destination squares (from `legal_moves`); clicking a highlighted square sends the move. Highlight the last move and show a check indicator. Show a thinking indicator while a bot moves, plus the bot's last depth, nodes, time, and eval. Move list, undo, resign, new game, and a banner with result and reason at game end. If a human plays Black, flip the board so they play from the bottom.
3. Bot vs bot controls on the Play screen: play, pause, step, and speed (delay between moves). Implemented in the frontend by repeatedly calling `/step`; there is no server-side background loop.
4. Replay: the M7 viewer, with a button to load the game that just finished.

Acceptance (tests with FastAPI `TestClient`, no browser required):
- Creating a game with `human` on both sides returns 400.
- `GET /api/bots` returns every registered bot with defaults, and creating a game with defaults for each bot succeeds.
- Illegal move, move on the wrong turn, and `/move` while a bot is to move all return 400 and leave the state unchanged.
- Human vs bot: after a legal human move and a `/step`, the state shows both moves and a legal bot reply.
- Bot vs bot: calling `/step` repeatedly reaches a terminal state within the 300-ply cap for random vs random, and alphabeta (depth 2) vs greedy.
- Undo restores the previous FEN exactly.
- Two games running concurrently do not affect each other.
- A finished game appears in `games/` and loads in the replay viewer.
- Manual check, written in the README: start `python -m server`, play a few moves as a human against alphabeta, then run a bot-vs-bot game between MCTS and alphabeta to completion.

## Game JSON schema

```json
{
  "id": "string",
  "red": {"type": "alphabeta", "params": {"depth": 4}},
  "black": {"type": "human"},
  "seed": 123,
  "start_fen": "FEN after the random opening",
  "opening_moves": ["h2e2"],
  "moves": [
    {"move": "h2e2", "time_ms": 12.3, "nodes": 4521, "depth": 4, "eval": 35}
  ],
  "result": "red_win | black_win | draw",
  "reason": "checkmate | stalemate | repetition | perpetual_check | move_cap | resign",
  "plies": 87
}
```

Moves use ICCS-style coordinates: file a-i, rank 0-9, for example `h2e2`. Red starts on ranks 0-4, Black on ranks 5-9.

## Conventions

- Type hints throughout. Small, single-purpose functions.
- Tests live in `tests/`, run with `pytest`. Every milestone adds tests; do not move on with failing tests.
- Determinism: every random choice takes an explicit seed or `random.Random` instance. No global randomness.
- Search code is performance-sensitive: avoid allocation in hot loops, no board copies, no logging inside the search.
- The server and frontend never duplicate rules logic. If the UI needs a fact about the position (legal moves, check, game over), the engine provides it through the API.
- Commit after each milestone with a message naming it. Tag the commit where M1 perft passes.
- If a rule is ambiguous, pick the simplest reading, document it in `README.md`, and keep it in `engine/rules.py`.
- If perft or any test disagrees with the reference, fix the engine; never edit the expected numbers to match.

## Code readability (required, applies to every milestone)

The code will be read, explained, and graded by a person who is learning the algorithms. Optimize for clarity first, and speed second, except inside the search hot loop where performance matters (still comment it).

**Comments and docstrings:**
- Every module starts with a short docstring saying what it is for and how it fits into the project (for example, "Generates legal moves; used by the board, the bots, and perft").
- Every public class and function has a docstring covering what it does, its parameters, what it returns, and any side effects. Keep them short.
- Comment the why, not the what. Do not restate obvious code (`# increment i`). Do explain rules, tricks, and non-obvious decisions.
- Rule-specific code must cite the rule in plain English. Example: `# Horse leg rule: the horse cannot move if the adjacent orthogonal square is occupied.`
- Algorithm code must be annotated step by step so a reader can map it to the textbook version:
  - Alpha-beta: comment what alpha and beta mean, where cutoffs happen and why, and how the transposition table bounds are used.
  - MCTS: label the four phases (selection, expansion, rollout, backpropagation) with comments or separate functions, and explain the UCT formula next to where it is computed.
  - Evaluation: explain each feature, its sign convention, and the units of its weight.
- Any magic number (piece values, table entries, exploration constant) is a named constant with a comment explaining it.
- Mark known shortcuts or limitations with `# NOTE:` and unfinished ideas with `# TODO:`.

**Structure:**
- One clear responsibility per module and per function. Keep functions short (aim for under about 40 lines); split long ones into well-named helpers.
- Descriptive names over abbreviations (`legal_moves`, not `lm`). Use the same terms as the rules and the proposal (general, advisor, elephant, horse, chariot, cannon, soldier; ply; alpha, beta).
- Group each file in a consistent order: module docstring, imports, constants, types and classes, public functions, private helpers. Use short section comment banners in longer files.
- Use `dataclass`es or small typed classes instead of bare tuples or dicts for anything passed between modules.
- Keep search code readable even when optimized: if a fast version is hard to read, keep a simple reference version beside it (for example a slow `perft` or plain minimax) and test that they agree.
- Avoid clever one-liners, deep nesting, and long chains of comprehensions. Prefer early returns and plain loops where they read better.
- Format with a consistent style (use `ruff format` or `black` if available, otherwise follow PEP 8) and keep lines reasonably short.

**Documentation:**
- `README.md` includes a short architecture overview: a diagram or list showing how engine, bots, evaluation, harness, server, and web fit together, and the data flow of one game.
- Add a brief `docs/algorithms.md` explaining, in plain language, how alpha-beta (with its enhancements) and MCTS work in this codebase, with pointers to the files that implement each part.
- Each milestone ends with a short summary in the commit message or a changelog entry saying what was added and where.

## Out of scope

AlphaZero-style training, online multiplayer, user accounts, authentication, persistent database storage, opening books, endgame tablebases. The server is local single-user. Mention AlphaZero as future work in the README only.

## Definition of done

All milestone acceptance criteria pass, the code follows the readability rules above (module and function docstrings, commented algorithms, named constants, `docs/algorithms.md` written), `pytest` is green, both experiments run end to end from the command line, and `python -m server` serves an app where a person can start human vs bot and bot vs bot games with any registered algorithm and settings, then replay finished games. The README explains setup, running a tournament, running the experiments, and starting the app.
