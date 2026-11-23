/*
 * play-core.js -- pure logic for the live-play screen.
 *
 * Kept free of DOM and network code so it can be tested with plain Node
 * (web/tests/play-core.test.js). It turns ICCS moves into squares and back, maps a
 * square to a position on the drawn board for both orientations, builds a player spec
 * from form values, decides when the UI should auto-step a bot, and turns API errors
 * into sentences the user can act on.
 *
 * The UI never decides legality: every list it uses comes from the API.
 */
(function (root) {
  "use strict";

  const FILE_LETTERS = "abcdefghi";
  const BOARD_FILES = 9;
  const BOARD_RANKS = 10;

  // ---------------------------------------------------------------------------
  // Moves and squares
  // ---------------------------------------------------------------------------

  /** Square index (rank * 9 + file) from an ICCS square such as "e2". */
  function squareFromName(name) {
    if (typeof name !== "string" || name.length !== 2) {
      throw new Error("bad square '" + name + "'");
    }
    const file = FILE_LETTERS.indexOf(name[0]);
    const rank = Number(name[1]);
    if (file < 0 || !Number.isInteger(rank) || rank < 0 || rank >= BOARD_RANKS) {
      throw new Error("square '" + name + "' is off the board");
    }
    return rank * BOARD_FILES + file;
  }

  /** ICCS square name for a square index, for example 22 -> "e2". */
  function squareToName(square) {
    const file = square % BOARD_FILES;
    const rank = Math.floor(square / BOARD_FILES);
    return FILE_LETTERS[file] + rank;
  }

  /** Parse "h2e2" into `{from, to}` square indices. */
  function parseMove(iccs) {
    if (typeof iccs !== "string" || iccs.length !== 4) {
      throw new Error("bad move '" + iccs + "'");
    }
    return { from: squareFromName(iccs.slice(0, 2)), to: squareFromName(iccs.slice(2, 4)) };
  }

  /** Unique from-squares of a legal-move list: these are the pieces a human may pick. */
  function fromSquares(legalMoves) {
    const seen = new Set();
    const result = [];
    for (const move of legalMoves) {
      const from = squareFromName(String(move).slice(0, 2));
      if (!seen.has(from)) {
        seen.add(from);
        result.push(from);
      }
    }
    return result;
  }

  /** Destinations reachable from one from-square, as `{move, to}` pairs. */
  function destinationsFor(legalMoves, fromSquare) {
    const result = [];
    for (const move of legalMoves) {
      const parsed = parseMove(String(move));
      if (parsed.from === fromSquare) {
        result.push({ move: String(move), to: parsed.to });
      }
    }
    return result;
  }

  // ---------------------------------------------------------------------------
  // Board orientation
  // ---------------------------------------------------------------------------

  /**
   * Where a board square is drawn, as 0-based `{column, row}` in the grid.
   *
   * Unflipped: files a-i run left to right, rank 0 is the bottom row. Flipped (the
   * human plays Black): the board is rotated 180 degrees, so rank 0 is the top row
   * and file a is on the right.
   */
  function screenSquare(file, rank, flipped) {
    if (flipped) {
      return { column: BOARD_FILES - 1 - file, row: rank };
    }
    return { column: file, row: BOARD_RANKS - 1 - rank };
  }

  /** The board square drawn at a grid position (the inverse of ``screenSquare``). */
  function squareAt(column, row, flipped) {
    if (flipped) {
      return { file: BOARD_FILES - 1 - column, rank: row };
    }
    return { file: column, rank: BOARD_RANKS - 1 - row };
  }

  /** Square of the general of ``side`` ("red" or "black") in a parsed position. */
  function generalSquare(squares, side) {
    const target = side === "red" ? "K" : "k";
    const index = squares.indexOf(target);
    return index >= 0 ? index : -1;
  }

  // ---------------------------------------------------------------------------
  // Player specs from form values
  // ---------------------------------------------------------------------------

  /**
   * Coerce one form value to the parameter's type, using the registry schema.
   *
   * Values arrive from inputs as strings or booleans; a nullable parameter whose
   * "no limit" box is checked arrives as ``null``.
   */
  function coerceParam(param, raw) {
    if (raw === null || raw === undefined) {
      if (param.nullable) {
        return null;
      }
      throw new Error(param.name + ": a value is required");
    }
    if (param.type === "bool") {
      return Boolean(raw);
    }
    if (param.type === "enum") {
      const choices = param.choices || [];
      if (choices.indexOf(raw) < 0) {
        throw new Error(param.name + ": choose one of " + choices.join(", "));
      }
      return raw;
    }
    const text = String(raw).trim();
    if (text === "") {
      throw new Error(param.name + ": enter a number");
    }
    const number = Number(text);
    if (!Number.isFinite(number)) {
      throw new Error(param.name + ": enter a number");
    }
    if (param.type === "int" && !Number.isInteger(number)) {
      throw new Error(param.name + ": enter a whole number");
    }
    if (param.min !== null && param.min !== undefined && number < param.min) {
      throw new Error(param.name + ": minimum is " + param.min);
    }
    if (param.max !== null && param.max !== undefined && number > param.max) {
      throw new Error(param.name + ": maximum is " + param.max);
    }
    return number;
  }

  /**
   * Build a player spec from the setup form.
   *
   * ``choice`` is ``"human"`` or a bot type; ``definition`` is that bot's registry
   * entry (with its ``params`` schema); ``values`` maps parameter names to raw form
   * values. Throws an ``Error`` naming the field when a value cannot be used, so the
   * form can show it next to the side.
   */
  function buildPlayerSpec(choice, definition, values) {
    if (choice === "human") {
      return { type: "human", params: {} };
    }
    if (!definition || definition.type !== choice) {
      throw new Error("unknown player type '" + choice + "'");
    }
    const params = {};
    for (const param of definition.params) {
      const raw = Object.prototype.hasOwnProperty.call(values || {}, param.name)
        ? values[param.name]
        : param.default;
      params[param.name] = coerceParam(param, raw);
    }
    return { type: definition.type, params: params };
  }

  // ---------------------------------------------------------------------------
  // UI decisions
  // ---------------------------------------------------------------------------

  /**
   * Whether the play screen should call /step immediately after a state change.
   *
   * Only a human-vs-bot game auto-steps, never after an undo (that would make an
   * undo/step loop with a deterministic bot), and never once the game is over.
   */
  function shouldAutoStep(context) {
    if (!context.humanVsBot) {
      return false;
    }
    if (context.gameOver) {
      return false;
    }
    if (!context.botToMove) {
      return false;
    }
    return context.lastAction !== "undo";
  }

  /**
   * Whether the Play/Pause button may be pressed.
   *
   * Pause must stay live while a bot is thinking, so the loop stops as soon as the
   * in-flight move returns; that is the only control allowed during a request.
   * Starting another move while one is already pending is never allowed.
   */
  function playToggleEnabled(context) {
    if (context.gone || context.over) {
      return false;
    }
    if (!context.playing && context.busy) {
      return false;
    }
    return true;
  }

  /** A sentence for an API failure, so the message area is always useful. */
  function describeError(error) {
    if (!error) {
      return "Something went wrong.";
    }
    if (error.kind === "network") {
      return "Could not reach the server. Is it running? Start it with python -m server.";
    }
    if (error.status === 404) {
      return "That game no longer exists (the server may have been restarted).";
    }
    if (error.status === 400) {
      return error.detail || "The server rejected that request.";
    }
    if (error.status) {
      return "Server error " + error.status + (error.detail ? ": " + error.detail : ".");
    }
    return error.message || String(error);
  }

  /** One line describing what the last search used, from a /step ``search`` block. */
  function formatSearch(search) {
    if (!search) {
      return "";
    }
    const bits = [];
    if (search.requested_time_limit_ms !== undefined) {
      const requested =
        search.requested_time_limit_ms === null ? "none" : search.requested_time_limit_ms + " ms";
      bits.push("requested " + requested + " → applied " + search.applied_time_limit_ms + " ms");
    }
    if (search.depth) {
      bits.push("depth " + search.depth);
    }
    if (search.iterations !== null && search.iterations !== undefined) {
      bits.push(search.iterations + " iterations");
    } else if (search.nodes) {
      bits.push(search.nodes + " nodes");
    }
    if (search.elapsed_ms !== undefined) {
      bits.push(search.elapsed_ms + " ms");
    }
    return bits.join(" · ");
  }

  const api = {
    squareFromName: squareFromName,
    squareToName: squareToName,
    parseMove: parseMove,
    fromSquares: fromSquares,
    destinationsFor: destinationsFor,
    screenSquare: screenSquare,
    squareAt: squareAt,
    generalSquare: generalSquare,
    coerceParam: coerceParam,
    buildPlayerSpec: buildPlayerSpec,
    shouldAutoStep: shouldAutoStep,
    playToggleEnabled: playToggleEnabled,
    describeError: describeError,
    formatSearch: formatSearch,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.PlayCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
