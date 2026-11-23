/*
 * replay-mount.test.js -- plain-Node test for Replay.mount's failure ordering.
 *
 * The bug this guards: loading an invalid game used to destroy the replay already on
 * the screen before the game was validated, leaving a blank pane. `mount` now builds
 * the timeline first, so a validation error must leave `container.innerHTML` and
 * `container.__replayViewer` exactly as they were.
 *
 * The good-game path needs a real DOM; this test uses a fake container and only
 * exercises the failure path, which is the one the ordering fix is about.
 *
 *     node web/tests/replay-mount.test.js
 */
"use strict";

const assert = require("node:assert");

require("../replay-core.js"); // sets globalThis.ReplayCore for replay.js
require("../replay.js"); // defines globalThis.Replay
const replay = globalThis.Replay;

let checks = 0;

function check(condition, message) {
  assert.ok(condition, message);
  checks += 1;
}

const BAD_GAME = { start_fen: "not a fen", moves: [] };

check(typeof replay.mount === "function", "Replay.mount is exported");

// Stand-in for a mounted viewer: destroy() is the thing that must not run.
let destroyed = 0;
const container = { innerHTML: "OLD REPLAY MARKUP", __replayViewer: null };
const previous = {
  destroy() {
    destroyed += 1;
    container.innerHTML = "";
  },
};
container.__replayViewer = previous;

assert.throws(() => replay.mount(container, BAD_GAME));
check(destroyed === 0, "the previous viewer was not destroyed");
check(container.__replayViewer === previous, "__replayViewer still points at the old viewer");
check(container.innerHTML === "OLD REPLAY MARKUP", "the old markup is untouched");

// A missing container still fails early and cleanly.
assert.throws(() => replay.mount(null, BAD_GAME), /container/);
check(destroyed === 0, "no viewer was destroyed for a missing container");

console.log("replay-mount: " + checks + " checks passed");
