/*
 * board.js -- draws a Xiangqi position as an SVG board and reports square clicks.
 *
 * Classic script, no modules, no external resources. Loaded after replay-core.js and
 * play-core.js: the coordinate transform for the flipped (Black at the bottom)
 * orientation comes from PlayCore.screenSquare, so the drawing and the click handling
 * agree by construction.
 *
 * The replay screen calls `render(container, squares, {lastMove, letterMode})` and
 * gets exactly the M7 board. The play screen additionally passes `flipped`,
 * `selected`, `destinations` and `checkSquare`, and listens for clicks on elements
 * carrying `data-square`.
 */
(function (root) {
  "use strict";

  const PlayCore = root.PlayCore;

  const BOARD_FILES = 9;
  const BOARD_RANKS = 10;
  const CELL = 48; // px between grid lines
  const MARGIN = 44; // border for the coordinate labels
  const PIECE_RADIUS = 19; // smaller than half a cell, so pieces do not touch
  const DESTINATION_RADIUS = 8; // dot inside a legal destination square
  const CAPTURE_RING_RADIUS = PIECE_RADIUS + 4; // ring around a piece that can be taken
  const GRID_WIDTH = 1.2;
  const BORDER_WIDTH = 2.4;
  const LABEL_SIZE = 13;
  const RIVER_SIZE = 21;
  const PIECE_SIZE = 24;
  const BOARD_WIDTH = MARGIN * 2 + (BOARD_FILES - 1) * CELL;
  const BOARD_HEIGHT = MARGIN * 2 + (BOARD_RANKS - 1) * CELL;

  const COLORS = {
    paper: "#f2d9a6",
    paperEdge: "#c9a15c",
    grid: "#6b4a1d",
    label: "#5b4520",
    river: "#7a5a25",
    pieceFill: "#f8ecd2",
    redPiece: "#a32a1f",
    blackPiece: "#20201c",
    highlight: "rgba(255, 196, 0, 0.55)",
    highlightTarget: "rgba(56, 142, 60, 0.55)",
    destination: "rgba(30, 110, 45, 0.55)",
    selected: "#1f5fa8",
    check: "#c62828",
  };

  // Chinese piece characters; `letterMode` falls back to the FEN letters for fonts
  // that lack these glyphs.
  const GLYPHS = {
    K: "帥",
    A: "仕",
    B: "相",
    N: "傌",
    R: "俥",
    C: "炮",
    P: "兵",
    k: "將",
    a: "士",
    b: "象",
    n: "馬",
    r: "車",
    c: "砲",
    p: "卒",
  };

  const FILE_LABELS = "abcdefghi";

  function xOf(file) {
    return MARGIN + file * CELL;
  }

  function yOf(rank) {
    return MARGIN + (BOARD_RANKS - 1 - rank) * CELL;
  }

  /** Screen position of a board square, in px, honoring the flipped orientation. */
  function pointOf(square, flipped) {
    if (!PlayCore) {
      throw new Error("board.js needs play-core.js loaded first");
    }
    const file = square % BOARD_FILES;
    const rank = Math.floor(square / BOARD_FILES);
    const spot = PlayCore.screenSquare(file, rank, flipped);
    return { x: MARGIN + spot.column * CELL, y: MARGIN + spot.row * CELL };
  }

  function line(x1, y1, x2, y2, width, color) {
    return (
      '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="' + color + '" stroke-width="' + width + '" stroke-linecap="round"/>'
    );
  }

  /**
   * The static furniture: background, grid, palaces, river and coordinate labels.
   *
   * A 180-degree rotation leaves the grid, both palaces and the river where they are,
   * so only the labels change with the orientation.
   */
  function staticBoard(flipped) {
    const parts = [];
    parts.push(
      '<rect x="0" y="0" width="' + BOARD_WIDTH + '" height="' + BOARD_HEIGHT +
        '" fill="' + COLORS.paper + '" stroke="' + COLORS.paperEdge + '" stroke-width="' +
        BORDER_WIDTH + '" rx="6"/>',
    );

    for (let rank = 0; rank < BOARD_RANKS; rank += 1) {
      parts.push(line(xOf(0), yOf(rank), xOf(8), yOf(rank), GRID_WIDTH, COLORS.grid));
    }

    // Edge files run the full height; interior files stop at the river.
    for (let file = 0; file < BOARD_FILES; file += 1) {
      if (file === 0 || file === 8) {
        parts.push(line(xOf(file), yOf(9), xOf(file), yOf(0), GRID_WIDTH, COLORS.grid));
      } else {
        parts.push(line(xOf(file), yOf(9), xOf(file), yOf(5), GRID_WIDTH, COLORS.grid));
        parts.push(line(xOf(file), yOf(4), xOf(file), yOf(0), GRID_WIDTH, COLORS.grid));
      }
    }

    for (const [low, high] of [
      [0, 2],
      [7, 9],
    ]) {
      parts.push(line(xOf(3), yOf(high), xOf(5), yOf(low), GRID_WIDTH, COLORS.grid));
      parts.push(line(xOf(5), yOf(high), xOf(3), yOf(low), GRID_WIDTH, COLORS.grid));
    }

    const riverY = yOf(5) + CELL / 2 + RIVER_SIZE / 3;
    parts.push(
      '<text x="' + xOf(2) + '" y="' + riverY + '" text-anchor="middle" font-size="' +
        RIVER_SIZE + '" fill="' + COLORS.river + '" letter-spacing="6">楚 河</text>',
    );
    parts.push(
      '<text x="' + xOf(6) + '" y="' + riverY + '" text-anchor="middle" font-size="' +
        RIVER_SIZE + '" fill="' + COLORS.river + '" letter-spacing="6">漢 界</text>',
    );

    // Coordinates. Each label is the square the line belongs to, so flipping the
    // board also flips the labels.
    for (let column = 0; column < BOARD_FILES; column += 1) {
      const file = PlayCore.squareAt(column, 0, flipped).file;
      parts.push(
        '<text x="' + (MARGIN + column * CELL) + '" y="' + (yOf(0) + 28) +
          '" text-anchor="middle" font-size="' + LABEL_SIZE + '" fill="' + COLORS.label +
          '">' + FILE_LABELS[file] + "</text>",
      );
    }
    for (let row = 0; row < BOARD_RANKS; row += 1) {
      const rank = PlayCore.squareAt(0, row, flipped).rank;
      parts.push(
        '<text x="' + (MARGIN - 26) + '" y="' + (MARGIN + row * CELL + 5) +
          '" text-anchor="middle" font-size="' + LABEL_SIZE + '" fill="' + COLORS.label +
          '">' + rank + "</text>",
      );
    }
    return parts.join("");
  }

  function lastMoveMarkup(lastMove, flipped) {
    if (!lastMove) {
      return "";
    }
    const from = pointOf(lastMove.from, flipped);
    const to = pointOf(lastMove.to, flipped);
    const size = CELL * 0.86;
    return (
      '<rect x="' + (from.x - size / 2) + '" y="' + (from.y - size / 2) +
        '" width="' + size + '" height="' + size + '" rx="6" fill="' + COLORS.highlight + '"/>' +
      '<rect x="' + (to.x - size / 2) + '" y="' + (to.y - size / 2) +
        '" width="' + size + '" height="' + size + '" rx="6" fill="' + COLORS.highlightTarget + '"/>'
    );
  }

  /** Dots on empty destination squares; drawn under the pieces. */
  function destinationMarkup(destinations, squares, flipped) {
    const marks = [];
    for (const square of destinations || []) {
      if (squares[square]) {
        continue; // a capture destination gets a ring instead, see captureMarkup
      }
      const point = pointOf(square, flipped);
      marks.push(
        '<circle class="destination" cx="' + point.x + '" cy="' + point.y +
          '" r="' + DESTINATION_RADIUS + '" fill="' + COLORS.destination + '"/>',
      );
    }
    return marks.join("");
  }

  /** Rings around occupied destination squares (captures); drawn over the pieces. */
  function captureMarkup(destinations, squares, flipped) {
    const marks = [];
    for (const square of destinations || []) {
      if (!squares[square]) {
        continue;
      }
      const point = pointOf(square, flipped);
      marks.push(
        '<circle class="destination is-capture" cx="' + point.x + '" cy="' + point.y +
          '" r="' + CAPTURE_RING_RADIUS + '" fill="none" stroke="' + COLORS.destination +
          '" stroke-width="3"/>',
      );
    }
    return marks.join("");
  }

  function selectedMarkup(selected, flipped) {
    if (selected === null || selected === undefined) {
      return "";
    }
    const point = pointOf(selected, flipped);
    const size = CELL * 0.9;
    return (
      '<rect x="' + (point.x - size / 2) + '" y="' + (point.y - size / 2) +
      '" width="' + size + '" height="' + size + '" rx="7" fill="none" stroke="' +
      COLORS.selected + '" stroke-width="3"/>'
    );
  }

  function checkMarkup(checkSquare, flipped) {
    if (checkSquare === null || checkSquare === undefined || checkSquare < 0) {
      return "";
    }
    const point = pointOf(checkSquare, flipped);
    return (
      '<circle cx="' + point.x + '" cy="' + point.y + '" r="' + (PIECE_RADIUS + 4) +
      '" fill="none" stroke="' + COLORS.check + '" stroke-width="3"/>'
    );
  }

  function pieceMarkup(piece, square, letterMode, flipped) {
    const isRed = piece === piece.toUpperCase();
    const color = isRed ? COLORS.redPiece : COLORS.blackPiece;
    const glyph = letterMode ? piece : GLYPHS[piece] || piece;
    const fontFamily = letterMode ? "ui-monospace, monospace" : "serif";
    const point = pointOf(square, flipped);
    return (
      '<g class="piece ' + (isRed ? "is-red" : "is-black") + '">' +
      '<circle cx="' + point.x + '" cy="' + point.y + '" r="' + PIECE_RADIUS +
      '" fill="' + COLORS.pieceFill + '" stroke="' + color + '" stroke-width="2"/>' +
      '<text x="' + point.x + '" y="' + (point.y + 2) + '" text-anchor="middle" ' +
      'dominant-baseline="middle" font-size="' + PIECE_SIZE + '" font-family="' + fontFamily +
      '" fill="' + color + '">' + glyph + "</text>" +
      "</g>"
    );
  }

  /** Transparent hit targets, drawn last, one per square, carrying ``data-square``. */
  function hitMarkup(flipped) {
    const hits = [];
    for (let square = 0; square < BOARD_FILES * BOARD_RANKS; square += 1) {
      const point = pointOf(square, flipped);
      hits.push(
        '<rect class="square-hit" data-square="' + square + '" x="' + (point.x - CELL / 2) +
          '" y="' + (point.y - CELL / 2) + '" width="' + CELL + '" height="' + CELL +
          '" fill="transparent"/>',
      );
    }
    return hits.join("");
  }

  /**
   * Draw a position into `container`, replacing its contents.
   *
   * Square indices are the engine's (`rank * 9 + file`). Options:
   * * `lastMove` -- `{from, to}` to tint;
   * * `letterMode` -- draw FEN letters instead of the Chinese characters;
   * * `flipped` -- Black at the bottom (a 180-degree rotation);
   * * `selected` -- one square outlined as selected;
   * * `destinations` -- squares marked with a dot (or ringed when a piece stands there);
   * * `checkSquare` -- a square ringed in red (the checked general).
   */
  function render(container, squares, options) {
    const settings = options || {};
    const flipped = Boolean(settings.flipped);
    const pieces = [];
    for (let square = 0; square < BOARD_FILES * BOARD_RANKS; square += 1) {
      const piece = squares[square];
      if (piece) {
        pieces.push(pieceMarkup(piece, square, Boolean(settings.letterMode), flipped));
      }
    }
    container.innerHTML =
      '<svg class="board" viewBox="0 0 ' + BOARD_WIDTH + " " + BOARD_HEIGHT +
      '" role="img" aria-label="Xiangqi board">' +
      staticBoard(flipped) +
      lastMoveMarkup(settings.lastMove, flipped) +
      destinationMarkup(settings.destinations, squares, flipped) +
      selectedMarkup(settings.selected, flipped) +
      pieces.join("") +
      captureMarkup(settings.destinations, squares, flipped) +
      checkMarkup(settings.checkSquare, flipped) +
      hitMarkup(flipped) +
      "</svg>";
  }

  const api = {
    render: render,
    CONSTANTS: {
      CELL: CELL,
      MARGIN: MARGIN,
      PIECE_RADIUS: PIECE_RADIUS,
      BOARD_WIDTH: BOARD_WIDTH,
      BOARD_HEIGHT: BOARD_HEIGHT,
    },
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.XiangqiBoard = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
