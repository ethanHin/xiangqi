/*
 * replay-core.js -- pure replay logic for the Xiangqi replay viewer.
 *
 * Loaded as a classic script (no ES modules, so `file://` works) and attached to
 * `window.ReplayCore`; under Node it also sets `module.exports`, which is what the
 * tests use. It contains no DOM code, no network code, and no rules: it parses FEN,
 * applies the recorded moves, and builds the list of positions the viewer shows.
 *
 * The viewer is deliberately not a referee. It checks only what is needed to avoid
 * displaying nonsense (the move string is four characters, both squares are on the
 * board, they are different, and the from-square is not empty). Legality and the
 * game result come from the file.
 */
(function (root) {
  "use strict";

  const START_FEN =
    "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1";
  const FILE_LETTERS = "abcdefghi";
  const BOARD_FILES = 9;
  const BOARD_RANKS = 10;
  const PIECE_CHARACTERS = /^[rnbakcpRNBAKCP]$/;

  const RESULT_LABELS = {
    red_win: "Red wins",
    black_win: "Black wins",
    draw: "Draw",
  };

  // Friendly names for the player-spec types; anything else is shown as its raw type.
  const TYPE_LABELS = {
    human: "Human",
    random: "Random",
    greedy: "Greedy",
    alphabeta: "Alpha-beta",
    mcts: "MCTS",
    scripted: "Scripted",
  };

  const REASON_LABELS = {
    checkmate: "checkmate",
    stalemate: "stalemate",
    repetition: "repetition",
    perpetual_check: "perpetual check",
    move_cap: "move cap",
    resign: "resignation",
  };

  // ---------------------------------------------------------------------------
  // FEN
  // ---------------------------------------------------------------------------

  /**
   * Parse a FEN string into `{ squares, sideToMove }`.
   *
   * `squares` has 90 entries indexed `rank * 9 + file`, holding the FEN letter of
   * the piece (`""` for empty). Rank 0 is Red's back rank. Only the placement and
   * the side-to-move fields are read; the rest is ignored, as in the engine.
   */
  function parseFen(fen) {
    if (typeof fen !== "string" || !fen.trim()) {
      throw new Error("start_fen is missing or empty");
    }
    const fields = fen.trim().split(/\s+/);
    const rows = fields[0].split("/");
    if (rows.length !== BOARD_RANKS) {
      throw new Error("FEN must have " + BOARD_RANKS + " ranks");
    }
    const squares = new Array(BOARD_FILES * BOARD_RANKS).fill("");
    rows.forEach(function (row, fenRank) {
      const rank = BOARD_RANKS - 1 - fenRank;
      let file = 0;
      for (const character of row) {
        if (character >= "1" && character <= "9") {
          file += Number(character);
          continue;
        }
        if (!PIECE_CHARACTERS.test(character)) {
          throw new Error("unknown piece '" + character + "' in FEN");
        }
        if (file >= BOARD_FILES) {
          throw new Error("rank " + rank + " has too many squares in FEN");
        }
        squares[rank * BOARD_FILES + file] = character;
        file += 1;
      }
      if (file !== BOARD_FILES) {
        throw new Error("rank " + rank + " has " + file + " squares, expected " + BOARD_FILES);
      }
    });
    return {
      squares: squares,
      sideToMove: fields[1] === "b" ? "b" : "w",
    };
  }

  /**
   * Serialise a state the way the engine's `Board.to_fen()` does, so the opening
   * segment of a game can be verified against `start_fen` by string equality.
   */
  function stateToFen(state) {
    const rows = [];
    for (let rank = BOARD_RANKS - 1; rank >= 0; rank -= 1) {
      let row = "";
      let empty = 0;
      for (let file = 0; file < BOARD_FILES; file += 1) {
        const piece = state.squares[rank * BOARD_FILES + file];
        if (!piece) {
          empty += 1;
          continue;
        }
        if (empty > 0) {
          row += String(empty);
          empty = 0;
        }
        row += piece;
      }
      if (empty > 0) {
        row += String(empty);
      }
      rows.push(row);
    }
    return rows.join("/") + " " + state.sideToMove + " - - 0 1";
  }

  /** Square index from an ICCS square name such as "e0". */
  function squareIndex(name) {
    const file = FILE_LETTERS.indexOf(name[0]);
    const rank = Number(name[1]);
    if (
      name.length !== 2 ||
      file < 0 ||
      !Number.isInteger(rank) ||
      rank < 0 ||
      rank >= BOARD_RANKS
    ) {
      throw new Error("square '" + name + "' is off the board");
    }
    return rank * BOARD_FILES + file;
  }

  /** Parse an ICCS move such as "h2e2" into `{ from, to }` square indices. */
  function parseIccs(iccs) {
    if (typeof iccs !== "string" || iccs.length !== 4) {
      throw new Error("move '" + iccs + "' is not four characters like h2e2");
    }
    return {
      from: squareIndex(iccs.slice(0, 2)),
      to: squareIndex(iccs.slice(2, 4)),
    };
  }

  /**
   * Apply one recorded move and return the next state (the input is not modified).
   *
   * The only checks are the ones the viewer needs: on the board, not the same
   * square, and the from-square is occupied. Capture, side to move, and legality are
   * deliberately not checked -- the engine that produced the file decided all of it.
   */
  function applyMove(state, iccs) {
    const move = parseIccs(iccs);
    if (move.from === move.to) {
      throw new Error("the move starts and ends on the same square");
    }
    const squares = state.squares.slice();
    const piece = squares[move.from];
    if (!piece) {
      throw new Error("the from square is empty");
    }
    squares[move.to] = piece;
    squares[move.from] = "";
    return {
      squares: squares,
      sideToMove: state.sideToMove === "w" ? "b" : "w",
    };
  }

  // ---------------------------------------------------------------------------
  // Timeline
  // ---------------------------------------------------------------------------

  function appendMove(states, plies, iccs, record) {
    const fromState = states[states.length - 1];
    try {
      const next = applyMove(fromState, iccs);
      plies.push({
        number: states.length,
        side: fromState.sideToMove,
        iccs: iccs,
        record: record,
        from: parseIccs(iccs).from,
        to: parseIccs(iccs).to,
      });
      states.push(next);
    } catch (error) {
      throw new Error("ply " + states.length + ": " + error.message);
    }
  }

  /**
   * Build the viewer's timeline for a game object.
   *
   * `start_fen` is the position *after* the random opening (that is what the harness
   * writes). If `opening_moves` are present and replaying them from the standard
   * start position lands exactly on `start_fen`, they are included at the front of
   * the timeline so the viewer can show the whole game; otherwise the timeline starts
   * at `start_fen` and the opening is only displayed as text.
   *
   * Returns `{ states, plies, openingCount, source }`, where `states[i]` is the
   * position at ply `i`, `plies[i - 1]` describes the move that produced `states[i]`,
   * and `source` is "opening" or "start_fen".
   */
  function buildTimeline(game) {
    if (!game || typeof game !== "object" || Array.isArray(game)) {
      throw new Error("the JSON file is not a game object");
    }
    const startState = parseFen(game.start_fen);
    if (!Array.isArray(game.moves)) {
      throw new Error("the game has no moves array");
    }
    const opening = Array.isArray(game.opening_moves) ? game.opening_moves : [];
    let states = [];
    let plies = [];
    let openingCount = 0;
    let source = "start_fen";

    if (opening.length > 0) {
      try {
        states = [parseFen(START_FEN)];
        plies = [];
        opening.forEach(function (iccs) {
          appendMove(states, plies, iccs, null);
        });
        if (stateToFen(states[states.length - 1]) === stateToFen(startState)) {
          openingCount = opening.length;
          source = "opening";
        } else {
          states = [];
          plies = [];
        }
      } catch (error) {
        // A broken opening segment must not hide the recorded game: fall back to
        // start_fen, where the game itself begins.
        states = [];
        plies = [];
      }
    }
    if (states.length === 0) {
      states = [startState];
      plies = [];
    }

    game.moves.forEach(function (record, index) {
      const iccs = typeof record === "string" ? record : record && record.move;
      if (typeof iccs !== "string") {
        throw new Error("move " + (index + 1) + " of the game has no ICCS string");
      }
      appendMove(states, plies, iccs, typeof record === "object" ? record : null);
    });

    return {
      states: states,
      plies: plies,
      openingCount: openingCount,
      source: source,
    };
  }

  // ---------------------------------------------------------------------------
  // Display helpers (pure, so the tests can cover them)
  // ---------------------------------------------------------------------------

  /** Human-readable name for a player spec, a plain string, or "human". */
  function playerName(spec) {
    if (spec === undefined || spec === null) {
      return "unknown";
    }
    if (typeof spec === "string") {
      return spec;
    }
    const params = spec.params || {};
    const type = String(spec.type || "unknown");
    const bits = [TYPE_LABELS[type] || type];
    if (params.evaluator) {
      bits.push(String(params.evaluator));
    }
    if (params.depth !== undefined && params.depth !== null) {
      bits.push("depth " + params.depth);
    }
    if (params.iterations !== undefined && params.iterations !== null) {
      bits.push(params.iterations + " iterations");
    }
    if (params.time_limit_ms !== undefined && params.time_limit_ms !== null) {
      bits.push(params.time_limit_ms + " ms");
    }
    if (params.exploration !== undefined && params.exploration !== null) {
      bits.push("c=" + params.exploration);
    }
    return bits.join(" · ");
  }

  function resultLabel(result) {
    return RESULT_LABELS[result] || (result ? String(result) : "no result");
  }

  function reasonLabel(reason) {
    return REASON_LABELS[reason] || (reason ? String(reason) : "");
  }

  /** Banner text such as "Black wins · stalemate". */
  function bannerText(game) {
    const reason = reasonLabel(game.reason);
    return reason ? resultLabel(game.result) + " · " + reason : resultLabel(game.result);
  }

  /** The per-move fields the viewer can display, when the file has them. */
  function moveDetails(record) {
    if (!record || typeof record !== "object") {
      return [];
    }
    const fields = [];
    if (typeof record.time_ms === "number") {
      fields.push("time " + record.time_ms.toFixed(1) + " ms");
    }
    if (typeof record.nodes === "number") {
      fields.push(record.nodes + " nodes");
    }
    if (typeof record.depth === "number") {
      fields.push("depth " + record.depth);
    }
    if (typeof record.eval === "number") {
      fields.push("eval " + record.eval);
    }
    return fields;
  }

  const api = {
    START_FEN: START_FEN,
    parseFen: parseFen,
    stateToFen: stateToFen,
    parseIccs: parseIccs,
    applyMove: applyMove,
    buildTimeline: buildTimeline,
    playerName: playerName,
    resultLabel: resultLabel,
    reasonLabel: reasonLabel,
    bannerText: bannerText,
    moveDetails: moveDetails,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.ReplayCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
