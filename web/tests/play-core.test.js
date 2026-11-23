/*
 * play-core.test.js -- plain-Node tests for the live-play logic (no framework).
 *
 * Run from the repository root:
 *
 *     node web/tests/play-core.test.js
 *
 * Covers the pieces of the play screen that are pure logic: building a player spec
 * from form values, selectable squares and destinations from the API's legal moves,
 * the coordinate mapping for both board orientations, the auto-step decision, and
 * the API-error messages.
 */
"use strict";

const assert = require("node:assert");
const core = require("../play-core.js");
const replayCore = require("../replay-core.js");

let checks = 0;

function check(condition, message) {
  assert.ok(condition, message);
  checks += 1;
}

// ---------------------------------------------------------------------------
// Squares, moves, selection
// ---------------------------------------------------------------------------

check(core.squareFromName("a0") === 0, "a0 is square 0");
check(core.squareFromName("e2") === 22, "e2 is square 22");
check(core.squareFromName("i9") === 89, "i9 is the last square");
check(core.squareToName(22) === "e2", "square 22 is e2");
check(core.squareToName(89) === "i9", "square 89 is i9");
assert.throws(() => core.squareFromName("j1"), /off the board/);
assert.throws(() => core.squareFromName("e9x"), /bad square/);
assert.throws(() => core.parseMove("e2"), /bad move/);

const parsed = core.parseMove("h2e2");
check(parsed.from === 25 && parsed.to === 22, "h2e2 parses to 25 -> 22");

const legal = ["h2e2", "h2h6", "b0c2", "b2b9"];
check(
  JSON.stringify(core.fromSquares(legal)) === JSON.stringify([25, 1, 19]),
  "from-squares are unique and in order",
);
const destinations = core.destinationsFor(legal, 25);
check(destinations.length === 2, "two destinations from h2");
check(destinations[0].move === "h2e2" && destinations[0].to === 22, "h2e2 destination");
check(core.destinationsFor(legal, 1)[0].move === "b0c2", "b0c2 destination");
check(core.destinationsFor(legal, 0).length === 0, "no destinations for an empty list entry");

// ---------------------------------------------------------------------------
// Orientation
// ---------------------------------------------------------------------------

check(
  JSON.stringify(core.screenSquare(0, 0, false)) === JSON.stringify({ column: 0, row: 9 }),
  "unflipped a0 is bottom-left",
);
check(
  JSON.stringify(core.screenSquare(0, 0, true)) === JSON.stringify({ column: 8, row: 0 }),
  "flipped a0 is top-right",
);
check(
  JSON.stringify(core.screenSquare(8, 9, false)) === JSON.stringify({ column: 8, row: 0 }),
  "unflipped i9 is top-right",
);
check(
  JSON.stringify(core.squareAt(0, 9, false)) === JSON.stringify({ file: 0, rank: 0 }),
  "squareAt inverts unflipped",
);
check(
  JSON.stringify(core.squareAt(8, 0, true)) === JSON.stringify({ file: 0, rank: 0 }),
  "squareAt inverts flipped",
);

let roundTrips = 0;
for (let square = 0; square < 90; square += 1) {
  const file = square % 9;
  const rank = Math.floor(square / 9);
  for (const flipped of [false, true]) {
    const spot = core.screenSquare(file, rank, flipped);
    const back = core.squareAt(spot.column, spot.row, flipped);
    check(
      back.file === file && back.rank === rank,
      "orientation round trip for " + file + "," + rank + " flipped=" + flipped,
    );
    roundTrips += 1;
  }
}
check(roundTrips === 180, "both orientations cover all squares");

const startSquares = replayCore.parseFen(replayCore.START_FEN).squares;
check(core.generalSquare(startSquares, "red") === 4, "red general on e0");
check(core.generalSquare(startSquares, "black") === 85, "black general on e9");
check(core.generalSquare(["", "k"], "red") === -1, "missing general is -1");

// ---------------------------------------------------------------------------
// Player specs from form values
// ---------------------------------------------------------------------------

const alphabeta = {
  type: "alphabeta",
  label: "Alpha-beta",
  params: [
    { name: "depth", type: "int", default: null, min: 1, max: 6, nullable: true },
    { name: "time_limit_ms", type: "float", default: 2000, min: 50, max: 10000, nullable: true },
    { name: "evaluator", type: "enum", default: "material", choices: ["material", "full"] },
    { name: "quiescence", type: "bool", default: false },
  ],
};

const spec = core.buildPlayerSpec("alphabeta", alphabeta, {
  depth: "3",
  time_limit_ms: "1500",
  evaluator: "full",
  quiescence: true,
});
check(spec.type === "alphabeta", "spec type");
check(spec.params.depth === 3, "integer string becomes a number");
check(spec.params.time_limit_ms === 1500, "float string becomes a number");
check(spec.params.evaluator === "full" && spec.params.quiescence === true, "enum and bool");

const noneSpec = core.buildPlayerSpec("alphabeta", alphabeta, {
  depth: null,
  time_limit_ms: null,
  evaluator: "material",
  quiescence: false,
});
check(noneSpec.params.depth === null && noneSpec.params.time_limit_ms === null, "nullable none");

const defaults = core.buildPlayerSpec("alphabeta", alphabeta, {});
check(defaults.params.depth === null, "missing nullable default is null");
check(defaults.params.time_limit_ms === 2000, "missing default is used");
check(defaults.params.evaluator === "material", "missing enum default is used");
check(core.buildPlayerSpec("human", null, {}).type === "human", "human spec");

assert.throws(
  () => core.buildPlayerSpec("alphabeta", alphabeta, { depth: "", time_limit_ms: "100", evaluator: "material", quiescence: false }),
  /depth: enter a number/,
);
assert.throws(
  () => core.buildPlayerSpec("alphabeta", alphabeta, { depth: "2.5", time_limit_ms: "100", evaluator: "material", quiescence: false }),
  /whole number/,
);
assert.throws(
  () => core.buildPlayerSpec("alphabeta", alphabeta, { depth: "9", time_limit_ms: "100", evaluator: "material", quiescence: false }),
  /maximum is 6/,
);
assert.throws(
  () => core.buildPlayerSpec("alphabeta", alphabeta, { depth: "2", time_limit_ms: "100", evaluator: "nope", quiescence: false }),
  /choose one of/,
);

// ---------------------------------------------------------------------------
// Auto-step and errors
// ---------------------------------------------------------------------------

const contexts = [
  [{ humanVsBot: true, botToMove: true, gameOver: false, lastAction: "create" }, true],
  [{ humanVsBot: true, botToMove: true, gameOver: false, lastAction: "human_move" }, true],
  [{ humanVsBot: true, botToMove: true, gameOver: false, lastAction: "undo" }, false],
  [{ humanVsBot: true, botToMove: true, gameOver: true, lastAction: "create" }, false],
  [{ humanVsBot: true, botToMove: false, gameOver: false, lastAction: "create" }, false],
  [{ humanVsBot: false, botToMove: true, gameOver: false, lastAction: "create" }, false],
];
for (const [context, expected] of contexts) {
  check(core.shouldAutoStep(context) === expected, "auto-step " + JSON.stringify(context));
}

const toggleCases = [
  // Pause must work while the bot is thinking (the in-flight move still lands).
  [{ playing: true, busy: true, gone: false, over: false }, true],
  // Never start another move while one is pending.
  [{ playing: false, busy: true, gone: false, over: false }, false],
  [{ playing: true, busy: false, gone: false, over: false }, true],
  [{ playing: false, busy: false, gone: false, over: false }, true],
  [{ playing: true, busy: true, gone: true, over: false }, false],
  [{ playing: true, busy: false, gone: false, over: true }, false],
  [{ playing: false, busy: false, gone: false, over: true }, false],
];
for (const [context, expected] of toggleCases) {
  check(
    core.playToggleEnabled(context) === expected,
    "play toggle " + JSON.stringify(context),
  );
}

check(core.describeError({ kind: "network" }).indexOf("python -m server") >= 0, "network message");
check(core.describeError({ status: 404 }).indexOf("no longer exists") >= 0, "404 message");
check(core.describeError({ status: 400, detail: "illegal move 'x'" }) === "illegal move 'x'", "400 detail");
check(core.describeError({ status: 500, detail: "boom" }).indexOf("500") >= 0, "500 message");

const searchLine = core.formatSearch({
  requested_time_limit_ms: 2000,
  applied_time_limit_ms: 2000,
  depth: 3,
  nodes: 1413,
  elapsed_ms: 221.5,
});
check(searchLine.indexOf("depth 3") >= 0 && searchLine.indexOf("1413 nodes") >= 0, "search line");
const mctsLine = core.formatSearch({
  requested_time_limit_ms: null,
  applied_time_limit_ms: 50,
  iterations: 300,
  nodes: 300,
});
check(mctsLine.indexOf("300 iterations") >= 0, "mcts iterations in search line");

console.log("play-core: " + checks + " checks passed");
