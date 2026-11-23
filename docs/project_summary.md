# Xiangqi search project: a plain-language summary

## 1. Overview

This project is a computer player for **Xiangqi** (Chinese chess) plus experiments comparing two ways of choosing moves. A small engine implements the rules; four bots play (random, greedy, **alpha-beta** and **Monte Carlo tree search**, or MCTS); a shared **evaluation function** scores positions; and a match harness plays many seeded games so bots can be compared fairly. Two experiments measure how search depth and evaluation features affect strength; the same engine also drives a replay viewer and a local web app.

## 2. Problem definition

### State and representation

A Xiangqi position consists of where every piece stands on the 9×10 board (90 intersections, 16 pieces per side at the start), whose turn it is, and enough history to recognise repetition. Internally, `engine/board.py` stores the board as a flat array of 90 squares (`rank * 9 + file`) with a small integer code per piece. A **Zobrist hash** — a large number computed from the pieces and side to move and updated incrementally — acts as a fast fingerprint used as a cache key. Text interchange uses **FEN**, the standard one-line position format. `make_move` and `unmake_move` restore the position, hash and move list exactly; tests check this over random games.

### State space

The **state space** is every position that could ever occur; no program enumerates it. A loose upper bound gives each square 15 possible contents (empty, seven Red or seven Black piece types), yielding 15^90 ≈ 7.05×10^105 (an estimate computed for illustration, not a count of legal positions). It overcounts: it ignores piece counts, and most arrangements are illegal. The real number is still far beyond any search, so bots look a few moves ahead and score positions.

### Actions and branching

An **action** is a legal move. Red has **44** at the start. Across the 150 alpha-beta self-play games of the final depth-scaling run, `tools/branching_stats.py` counted **11,297 positions** averaging **31.9** legal moves (median 34, minimum 1, maximum 88), falling from 37.85 in plies (half-moves) 0–19 to 23.00 after ply 60 (MEASURED).

**Perft** counts leaf positions at a given depth and verifies the move generator: 44 at depth 1, 1,920 at depth 2, 79,666 at depth 3 and 3,290,240 at depth 4 (MEASURED), multiplying by roughly 41–44 per extra ply near the opening. Plain minimax to depth 3 therefore visits tens of thousands of positions, which is why pruning matters. Source: `docs/experiment_log.md` (problem definition) and `README.md` (perft table).

### Transition model

The model is deterministic and fully known. A move updates the piece placement (removing any captured piece), the hash and the history; `unmake_move()` reverses it exactly. There is no chance element and no hidden information; the only randomness is the seeded opening and the random bot's choice. The server validates human moves against `board.legal_moves()`; the search generates candidate moves and filters them for legality itself.

### Terminal states and utility

A game ends in one of these ways, and the rules tests cover each case:

- **Checkmate** — side to move has no legal move and is in check; it loses.
- **Stalemate** — no legal move, not in check; in Xiangqi that side also loses.
- **Repetition** — the same position and side to move occur three times: draw, unless one side checked on every one of its moves in the cycle and the other did not, in which case the checker loses (perpetual check).
- **Move cap** — 300 plies: draw; **resignation** (server games): the resigning side loses.

Utility is +1 for a win, 0 for a draw and −1 for a loss. The search uses **centipawns** (hundredths of a soldier) and a large mate score adjusted by distance, so faster mates are preferred.

### Game tree

The **game tree** alternates Red (“Max”) and Black (“Min”) layers. There are no chance nodes and no hidden information; depth is bounded by the search horizon or the 300-ply rule. Alpha-beta prunes the tree; MCTS samples it with random playouts.

## 3. Algorithms

### Baselines

**Random** picks a uniformly random legal move. **Greedy** scores each move by captured material, prefers the cheapest attacker among equal captures (MVV-LVA, “most valuable victim, least valuable attacker”), plays mate in one if available, and otherwise picks randomly among ties. Greedy beat random in **98%** of 100 games; alpha-beta beat greedy in **99%** (MEASURED).

### Alpha-beta search

**Minimax** assumes perfect play, looks ahead to a fixed depth, and chooses the move whose worst outcome is best; it is correct but expensive. **Alpha-beta pruning** returns the same answer while skipping work: alpha is the best score the mover has already secured, beta the best the opponent can force. A branch is cut when a move cannot beat alpha, or the opponent would never allow it (beta) — safe, because such branches could never be chosen. On 50 middlegame positions at depth 3, minimax visited 3,056,865 nodes and alpha-beta 430,538 with identical values (MEASURED).

The implementation adds:
- **Move ordering** — captures first (MVV-LVA), then “killer” moves that recently caused cut-offs, then history scores. On the same 50 positions nodes fell from 430,538 to 84,905 with unchanged values (MEASURED).
- **Iterative deepening** — search depth 1, 2, 3… until the time budget runs out, keeping the last completed depth. With a 50 ms budget on the start position it returns in 50–67 ms having completed depth 2 (MEASURED).
- **Transposition table (TT)** — a cache keyed by Zobrist hash storing depth, value, whether it is exact or a bound, and the best move. On a sparse-endgame set at depth 4 it cut 10,260 nodes to 8,476; on middlegame depth 3 it found nothing to reuse (MEASURED).
- **Quiescence** — behind a flag, searches captures at the horizon so positions are not judged mid-exchange; it visits more nodes and leaves quiet positions unchanged (MEASURED).
- **Mate-distance scoring** — a forced mate is a huge score reduced by the number of plies, so faster mates win.
- **Repetitions inside the search** — a position on the path scores 0 (draw); the perpetual-check loss is decided by `engine/rules.py`, not the search. Path-dependent scores never enter the TT.

Correctness is checked against `reference_minimax`, a plain depth-limited negamax with no pruning, ordering or table that shares the evaluation, terminal and repetition rules. The optimized search returns **identical root values** on 6 positions at depth 2 and **50 middlegame positions at depth 3**, with fewer nodes (MEASURED). Ordering and the TT are separately checked to reduce nodes without changing values; quiescence leaves quiet positions unchanged.

### MCTS

**Monte Carlo tree search** trades exact lookahead for many random simulations. Each iteration has four phases:

1. **Selection** — from the root, repeatedly pick a child using **UCT**: best average reward plus a bonus for being rarely visited.
2. **Expansion** — add one untried child.
3. **Rollout** — play random legal moves to a depth cap; terminal results are 1/0.5/0, otherwise the position is evaluated and squashed into [0,1] with `0.5 + 0.5·tanh(score/600)`.
4. **Backpropagation** — pass the reward up, flipping it at each edge (one player's win is the other's loss).

The **exploration constant** (default 1.4) sets how much UCT favours rarely tried moves; the final move is the **most-visited** root child. This is plain UCT — no tree reuse, virtual loss or learned policy — and it is weak at pure-Python budgets: at 750 ms it completed about **346 iterations**, a most-visited line of depth **1.27** plies, and lost 100–0 to alpha-beta at equal time (MEASURED; row A). At its acceptance setting (200 iterations vs random) it won 100–0 (MEASURED).

## 4. Heuristics (the evaluation function)

The evaluation function is a static estimate of who is better, in centipawns, from the side to move's point of view. Alpha-beta uses it at leaves; MCTS uses it when a rollout reaches its depth cap.

- **Material** — the balance of forces: chariot 900, cannon 450, horse 400, advisor 200, elephant 200, soldier 100, with a soldier worth **200** after crossing the river; the general is 0 because mate is scored separately. Material dominates: positional terms can never outweigh a chariot.
- **Piece-square tables (PSTs)** — a bonus for *where* a piece stands: soldiers gain as they advance and jump across the river; horses prefer the centre; chariots and cannons get small central bonuses; advisors and elephants are rewarded for staying home; the general is slightly penalised for moving forward. Tables are 10×9 centipawn grids, and Black's are Red's **flipped by rank**, so mirrored colour-swapped positions score identically. Full tables are in `README.md`.
- **Mobility** — your pseudo-legal move count minus the opponent's, 2 centipawns per extra move.
- **King safety** — bonuses for advisors and elephants near the general; penalties for missing guards, a bare or exposed general, or a general on a forward rank.

Features combine as a weighted sum: `material 1, pst 1, mobility 2, king_safety 1`. Positional parts are clamped after weighting (PST ±150, mobility ±200, king safety ±150), so their total never exceeds 500, less than a chariot. The score is always from the side to move's perspective. Four evaluators add features cumulatively: `material`, `material_pst`, `material_pst_mobility`, `full`.

The values were set by **reasoning, not tuning**: no entry or weight was fitted, and small-sample tuning is avoided (section 5e shows why).

## 5. Experiments

Experiments use colour-swapped pairs from one seeded opening and multiple processes; results are under `experiments/results/<run>/`. **Score** = `(wins + 0.5·draws)/games` in [0,1]; intervals are 95% **Wilson intervals**, plus an opening-clustered interval.

### (a) Depth scaling

**Setup (does deeper search help?):** alpha-beta with `full`, no clock; pairings 1v2, 2v3, 3v4; 50 games/pairing (150), 25 openings each twice; seed 1, 6-ply openings, 8 workers; 495 s.

| pairing | deeper W–D–L | score [95%] | deeper nodes / ms | shallower nodes / ms |
|---|---|---|---|---|
| 1 vs 2 | 50–0–0 | 1.00 [0.93–1.00] | 192 / 23.7 | 22 / 3.4 |
| 2 vs 3 | 48–0–2 | 0.96 [0.87–0.99] | 2,665 / 379 | 210 / 30.2 |
| 3 vs 4 | 37–7–6 | 0.81 [0.68–0.89] | 6,395 / 836 | 1,338 / 189 |

Source: `experiments/results/depth-scaling-20261123-053517/depth-scaling-20261123-053517-summary.csv`; clustered intervals from `docs/experiment_log.md` (“M6 depth scaling, 50 games per pairing”).

**What it shows:** deeper wins every pairing, margin shrinking (1.00 → 0.96 → 0.81) at 4–13× cost per ply (MEASURED); clustered intervals exclude 0.5. **Caveat:** repeats from 25 openings make games dependent; use the clustered interval.

### (b) Heuristic comparison

**Setup (which evaluator is stronger at equal depth?):** depth 3 with a 5 s cap, four-evaluator round-robin; 100 games/pairing (800), 50 openings each twice, seed 1, 6-ply openings, 8 workers; 3,028 s; cap hit **0 of 65,158 moves**.

| richer vs `material` | richer score [95%] | material score [95%] | ms/move (richer / material) |
|---|---|---|---|
| `material_pst` | 0.75 [0.66–0.82] | 0.25 [0.18–0.34] | 124–130 / 95–100 |
| `material_pst_mobility` | 0.675 [0.58–0.76] | 0.325 [0.24–0.42] | 210–225 / 95–100 |
| `full` | 0.745 [0.65–0.82] | 0.255 [0.18–0.35] | 231–260 / 95–100 |

Source: `experiments/results/heuristic-comparison-20261123-054636/heuristic-comparison-20261123-054636-summary.csv`; per-move costs from `docs/experiment_log.md` (“M6 heuristic comparison, 100 games per pairing”).

| richer pair | first side's score [95%] |
|---|---|
| `material_pst` vs `material_pst_mobility` | 0.49 [0.39–0.59] |
| `material_pst` vs `full` | 0.54 [0.44–0.63] |
| `material_pst_mobility` vs `full` | 0.57 [0.48–0.67] |

Source: `experiments/results/heuristic-comparison-20261123-054636/heuristic-comparison-20261123-054636-summary.csv` (the same file).

**What it shows:** every richer evaluator beats material (intervals above 0.5 including clustered), but the three richer ones are **statistically indistinguishable** — each pair's interval includes 0.5 (MEASURED). Cost at depth 3 over 60 positions:

| evaluator | µs per evaluation | nodes/s | ms per move |
|---|---|---|---|
| `material` | 2.31 | 15,366 | 110.3 |
| `material_pst` | 4.20 | 14,753 | 121.2 |
| `material_pst_mobility` | 43.38 | 9,407 | 192.4 |
| `full` | 62.29 | 8,113 | 221.3 |

Source: `README.md` (“What the evaluators cost”, from `tools/eval_cost --limit 60 --depth 3`); `docs/experiment_log.md` (“Evaluator cost on the full middlegame set”). `full` is about twice as slow as material. **Caveats:** fixed depth ≠ equal time; shared deterministic openings; 100 games cannot resolve small differences.

### (c) Row A: MCTS vs alpha-beta at equal time

**Setup:** 100 games, 750 ms/move, colours swapped, seeded openings; both use `full`; MCTS exploration 1.4.

| bot | W–D–L | score [95%] | depth | nodes / iterations | ms/move |
|---|---|---|---|---|---|
| alpha-beta | 100–0–0 | 1.00 [0.96–1.00] | 3.20 | 5,897 nodes | 707 |
| MCTS | 0–100–0 | 0.00 | 1.27 (line) | 346 iterations | 769 |

Source: `experiments/results/heuristic-comparison-20261123-054636/heuristic-comparison-20261123-054636-summary.csv`; `docs/experiment_log.md` (row A).

**What it shows:** a clearly separated win; pure-Python MCTS gets only ~350 iterations in 750 ms (MEASURED). **Caveat:** this says nothing about more iterations or a better rollout policy.

### (d) Row B: `full` vs `material` at equal time

**Setup:** 100 games, 750 ms/move.

| bot | W–D–L | score [95%] | depth | nodes/move | ms/move |
|---|---|---|---|---|---|
| `full` | 47–13–40 | 0.54 [0.44–0.63] | 4.13 | 7,967 | 746 |
| `material` | 40–13–47 | 0.46 | 4.85 | 12,033 | 738 |

Source: `docs/experiment_log.md` (row B); `experiments/results/heuristic-comparison-20261123-054636/heuristic-comparison-20261123-054636-summary.csv`.

**What it shows:** the estimate favours `full`, but the interval includes 0.5, so it is **open**; `material` reaches 0.72 plies deeper, and LIKELY the positional terms compensate. **Caveat:** time-limited games vary (pilot 0.15 for `full`).

### (e) Pilots versus final runs

| comparison | 10-game pilot | final larger run |
|---|---|---|
| depth 3 vs 4 (deeper score) | 0.70 [0.40–0.89] | 0.81 [0.68–0.89] (50 games) |
| `full` vs `material_pst` (full's score) | 0.20 (2–0–8) | 0.465 (44–5–51) (100 games) |
| `full` vs `material` at 750 ms (full's score) | 0.15 | 0.54 [0.44–0.63] (100 games) |

Source: `docs/experiment_log.md` pilot and final entries; `experiments/results/depth-scaling-20261123-050011/depth-scaling-20261123-050011-summary.csv`; `experiments/results/heuristic-comparison-20261123-051132/heuristic-comparison-20261123-051132-summary.csv`; final CSVs above.

The pilot made `full` look much worse than `material_pst` (0.20); at 100 games it was 0.465 vs 0.535, interval spanning 0.5. Ten games are a smoke test.

### (f) Supporting measurements

- **Perft (M1):** 44 / 1,920 / 79,666 / 3,290,240, verifying move generation.
- **Node counts (M3):** depth 3, 50 middlegame positions: minimax 3,056,865 → alpha-beta 430,538 → ordering 84,905, values identical; sparse-endgame depth 4 TT 10,260 → 8,476; middlegame depth 3 TT neutral. Quiescence changes values only with captures.
- **Greedy tuning (M2):** fixed first move 0% vs random (10); seeded random 80% (20); checks **hurt** (80% → 70%); MVV-LVA 95%; mate-in-one 98% (100, 98W 2D 0L).
- **Evaluator smoke check (M4 v2):** depth 2, 10 games each: win rate `material_pst` 50%, `material_pst_mobility` 80%, `full` 90% — wiring only.
- **MCTS sweeps (M5):** vs greedy, 10 games per arm: 100 iterations 10%, 400 40%, 1,600 70%; exploration 0.7 80%, 1.4 40%, 2.0 30% (400 iterations). Defaults kept: 10 games cannot rank settings.
- **Alpha-beta at the 200 ms cap (M3):** 201 ms/move (p95 226), depth 2.59, 2,291 nodes/move; 1,070 of 2,116 moves reached depth 3+.

Source: `README.md` (perft, node counts, MCTS sweeps, M3 acceptance) and `docs/experiment_log.md` (summary and entries).

## 6. What helped and what hurt

| tweak | effect | verdict |
|---|---|---|
| Greedy tie-break: fixed → seeded random | vs random 0% (10) → 80% (20) | HELPED |
| Greedy: prefer checks | 80% → 70% | HURT |
| Greedy: MVV-LVA | 70% → 95% | HELPED |
| Greedy: mate-in-one | 95% (20 games) → 98% (100 games) | HELPED |
| Cannon-screen guard | perft 4 time 22.9 s → 5.0 s | HELPED |
| Alpha-beta | depth-3 nodes 3,056,865 → 430,538 | HELPED |
| Move ordering | 430,538 → 84,905 | HELPED |
| TT on sparse endgames (d4) | 10,260 → 8,476 | HELPED |
| `full` evaluator cost | 2.31 → 62.29 µs per evaluation | HURT (cost) |
| One extra ply | deeper score 1.00 / 0.96 / 0.81 | HELPED |
| Richer evaluators vs material | 0.75 / 0.68 / 0.74 | HELPED |
| Richer evaluators vs each other | 0.49 / 0.54 / 0.57, intervals include 0.5 | INCONCLUSIVE |
| MCTS vs alpha-beta at 750 ms | alpha-beta 1.00 | HELPED (for alpha-beta) |
| `full` vs `material` at 750 ms | 0.54 [0.44–0.63] | INCONCLUSIVE |

Source: `docs/experiment_log.md`, “Summary” table.

## 7. Limitations and honest caveats

- **No external-engine cross-check:** perft verifies move generation and a reference minimax verifies search, but no external engine was available.
- **Same-author tests:** regression guards and textbook checks, not independent validation.
- **Small samples:** 10-game pilots and 100-game finals cannot resolve differences of a few percent.
- **Shared deterministic openings:** fixed-depth games from one opening repeat, so game-level intervals understate uncertainty; clustered intervals are quoted too.
- **Time-limited variance:** row B scored 0.15 in the pilot and 0.54 in the final run.
- **Pure-Python speed and plain MCTS:** about depth 2–3 at 200 ms and 346 MCTS iterations at 750 ms, with no virtual loss or learned policy; MCTS loses 100–0 at equal time.
- **Open equal-time evaluator question:** row B does not separate `full` from `material`; a larger run or equal-node comparison is needed.

## 8. Glossary

- **Ply** — one half-move (one player's move).
- **Perft** — a count of leaf positions at a given depth, used to verify move generation.
- **Centipawn** — one hundredth of a soldier; the evaluation's unit.
- **Minimax** — exact best-play search over alternating maximising and minimising layers.
- **Alpha / beta** — the best score the mover can guarantee and the best the opponent can force; the bounds that make pruning safe.
- **Transposition table** — a cache keyed by the Zobrist hash, reusing earlier search results.
- **Zobrist hash** — a compact position fingerprint updated incrementally.
- **Rollout** — a random playout from a node to a terminal state or depth cap.
- **UCT** — the tree policy balancing exploitation and exploration.
- **Wilson interval** — a confidence interval for a proportion, robust with small samples.
- **Score** — `(wins + 0.5·draws)/games`, with 0.5 even.
