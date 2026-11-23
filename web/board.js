/*
 * board.js -- draws a Xiangqi position as an SVG board.
 *
 * Classic script, no modules, no external resources. Exposes
 * `window.XiangqiBoard.render(container, squares, options)`, where `squares` is the
 * 90-entry array from `ReplayCore.parseFen` (FEN letters, "" for empty). The whole
 * SVG is rebuilt on each call; a position has at most 32 pieces, so that is cheaper
 * than diffing and it keeps the code simple.
 *
 * Layout constants and colours are named here so the drawing can be adjusted in one
 * place. Red is at the bottom, rank 0 is the bottom row, files run a-i left to
 * right.
 */
(function (root) {
  "use strict";

  const BOARD_FILES = 9;
  const BOARD_RANKS = 10;
  const CELL = 48; // px between grid lines
  const MARGIN = 44; // border for the coordinate labels
  const PIECE_RADIUS = 19; // smaller than half a cell, so pieces do not touch
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

  function line(x1, y1, x2, y2, width, color) {
    return (
      '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="' + color + '" stroke-width="' + width + '" stroke-linecap="round"/>'
    );
  }

  function staticBoard() {
    const parts = [];
    parts.push(
      '<rect x="0" y="0" width="' + BOARD_WIDTH + '" height="' + BOARD_HEIGHT +
        '" fill="' + COLORS.paper + '" stroke="' + COLORS.paperEdge + '" stroke-width="' +
        BORDER_WIDTH + '" rx="6"/>',
    );

    // Horizontal lines: one per rank.
    for (let rank = 0; rank < BOARD_RANKS; rank += 1) {
      parts.push(line(xOf(0), yOf(rank), xOf(8), yOf(rank), GRID_WIDTH, COLORS.grid));
    }

    // Vertical lines: the two edge files run the full height; the interior files stop
    // at the river, which is how a xiangqi board is drawn.
    for (let file = 0; file < BOARD_FILES; file += 1) {
      if (file === 0 || file === 8) {
        parts.push(line(xOf(file), yOf(9), xOf(file), yOf(0), GRID_WIDTH, COLORS.grid));
      } else {
        parts.push(line(xOf(file), yOf(9), xOf(file), yOf(5), GRID_WIDTH, COLORS.grid));
        parts.push(line(xOf(file), yOf(4), xOf(file), yOf(0), GRID_WIDTH, COLORS.grid));
      }
    }

    // Palace diagonals, Red (ranks 0-2) and Black (ranks 7-9).
    const palaces = [
      [0, 2],
      [7, 9],
    ];
    for (const [low, high] of palaces) {
      parts.push(line(xOf(3), yOf(high), xOf(5), yOf(low), GRID_WIDTH, COLORS.grid));
      parts.push(line(xOf(5), yOf(high), xOf(3), yOf(low), GRID_WIDTH, COLORS.grid));
    }

    // River label, split left/right like a printed board.
    const riverY = yOf(5) + CELL / 2 + RIVER_SIZE / 3;
    parts.push(
      '<text x="' + xOf(2) + '" y="' + riverY + '" text-anchor="middle" font-size="' +
        RIVER_SIZE + '" fill="' + COLORS.river + '" letter-spacing="6">楚 河</text>',
    );
    parts.push(
      '<text x="' + xOf(6) + '" y="' + riverY + '" text-anchor="middle" font-size="' +
        RIVER_SIZE + '" fill="' + COLORS.river + '" letter-spacing="6">漢 界</text>',
    );

    // Coordinates: files under the board, ranks to the left.
    for (let file = 0; file < BOARD_FILES; file += 1) {
      parts.push(
        '<text x="' + xOf(file) + '" y="' + (yOf(0) + 28) + '" text-anchor="middle" font-size="' +
          LABEL_SIZE + '" fill="' + COLORS.label + '">' + FILE_LABELS[file] + "</text>",
      );
    }
    for (let rank = 0; rank < BOARD_RANKS; rank += 1) {
      parts.push(
        '<text x="' + (MARGIN - 26) + '" y="' + (yOf(rank) + 5) + '" text-anchor="middle" font-size="' +
          LABEL_SIZE + '" fill="' + COLORS.label + '">' + rank + "</text>",
      );
    }
    return parts.join("");
  }

  function highlightMarkup(lastMove) {
    if (!lastMove) {
      return "";
    }
    const marks = [];
    const fromFile = lastMove.from % BOARD_FILES;
    const fromRank = Math.floor(lastMove.from / BOARD_FILES);
    const toFile = lastMove.to % BOARD_FILES;
    const toRank = Math.floor(lastMove.to / BOARD_FILES);
    const size = CELL * 0.86;
    marks.push(
      '<rect x="' + (xOf(fromFile) - size / 2) + '" y="' + (yOf(fromRank) - size / 2) +
        '" width="' + size + '" height="' + size + '" rx="6" fill="' + COLORS.highlight + '"/>',
    );
    marks.push(
      '<rect x="' + (xOf(toFile) - size / 2) + '" y="' + (yOf(toRank) - size / 2) +
        '" width="' + size + '" height="' + size + '" rx="6" fill="' + COLORS.highlightTarget + '"/>',
    );
    return marks.join("");
  }

  function pieceMarkup(piece, file, rank, letterMode) {
    const isRed = piece === piece.toUpperCase();
    const color = isRed ? COLORS.redPiece : COLORS.blackPiece;
    const glyph = letterMode ? piece : GLYPHS[piece] || piece;
    const fontFamily = letterMode ? "ui-monospace, monospace" : "serif";
    return (
      '<g class="piece ' + (isRed ? "is-red" : "is-black") + '">' +
      '<circle cx="' + xOf(file) + '" cy="' + yOf(rank) + '" r="' + PIECE_RADIUS +
      '" fill="' + COLORS.pieceFill + '" stroke="' + color + '" stroke-width="2"/>' +
      '<text x="' + xOf(file) + '" y="' + (yOf(rank) + 2) + '" text-anchor="middle" ' +
      'dominant-baseline="middle" font-size="' + PIECE_SIZE + '" font-family="' + fontFamily +
      '" fill="' + color + '">' + glyph + "</text>" +
      "</g>"
    );
  }

  /**
   * Draw a position into `container`, replacing its contents.
   *
   * `options.lastMove` is `{ from, to }` in square indices (or null); the from and to
   * squares are highlighted. `options.letterMode` draws FEN letters instead of the
   * Chinese characters.
   */
  function render(container, squares, options) {
    const settings = options || {};
    const pieces = [];
    for (let rank = 0; rank < BOARD_RANKS; rank += 1) {
      for (let file = 0; file < BOARD_FILES; file += 1) {
        const piece = squares[rank * BOARD_FILES + file];
        if (piece) {
          pieces.push(pieceMarkup(piece, file, rank, Boolean(settings.letterMode)));
        }
      }
    }
    container.innerHTML =
      '<svg class="board" viewBox="0 0 ' + BOARD_WIDTH + " " + BOARD_HEIGHT +
      '" role="img" aria-label="Xiangqi board, Red at the bottom">' +
      staticBoard() +
      highlightMarkup(settings.lastMove) +
      pieces.join("") +
      "</svg>";
  }

  const api = { render: render, CONSTANTS: { CELL: CELL, MARGIN: MARGIN, PIECE_RADIUS: PIECE_RADIUS } };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.XiangqiBoard = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
