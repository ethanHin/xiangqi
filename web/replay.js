/*
 * replay.js -- the replay screen: load a Game JSON and step through it.
 *
 * Classic script (no modules), so the page works from `file://`. The reusable part is
 * `Replay.mount(container, game)`, which builds the whole viewer inside `container`
 * and returns a handle with `destroy()`; that is what M8's replay screen will call.
 * File-picker, drag-and-drop and `?game=` loading live in their own functions, so the
 * mount never touches the network.
 *
 * The viewer does not decide anything about the game: the timeline comes from
 * `ReplayCore.buildTimeline`, the result and reason come from the file.
 */
(function (global) {
  "use strict";

  const Core = global.ReplayCore;
  const Board = global.XiangqiBoard;

  // Playback speeds, as milliseconds per ply. 1x is the default.
  const SPEEDS = [
    { label: "0.5×", delayMs: 1200 },
    { label: "1×", delayMs: 600 },
    { label: "2×", delayMs: 300 },
    { label: "4×", delayMs: 150 },
  ];
  const DEFAULT_SPEED_INDEX = 1;
  const SIDE_LABELS = { w: "Red", b: "Black" };
  const RESULT_CLASSES = { red_win: "is-win", black_win: "is-win", draw: "is-draw" };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
    });
  }

  function sideName(side) {
    return SIDE_LABELS[side] || side;
  }

  // ---------------------------------------------------------------------------
  // Markup
  // ---------------------------------------------------------------------------

  function openingMarkup(game, timeline) {
    const opening = Array.isArray(game.opening_moves) ? game.opening_moves : [];
    if (opening.length === 0) {
      return "";
    }
    const moves = opening.map(escapeHtml).join(" ");
    if (timeline.openingCount === opening.length) {
      return (
        '<p class="opening">Opening moves, replayed from the standard start: <code>' +
        moves + "</code></p>"
      );
    }
    return (
      '<p class="opening">Opening moves (not replayed; the board starts after them): <code>' +
      moves + "</code></p>"
    );
  }

  function metaMarkup(game, timeline) {
    const plies = game.plies === undefined ? timeline.plies.length : game.plies;
    return (
      '<dl class="meta">' +
      '<div><dt>Game</dt><dd>' + escapeHtml(game.id || "—") + "</dd></div>" +
      '<div><dt>Seed</dt><dd>' + escapeHtml(game.seed === undefined ? "—" : game.seed) + "</dd></div>" +
      '<div><dt>Result</dt><dd>' + escapeHtml(Core.resultLabel(game.result)) + "</dd></div>" +
      '<div><dt>Reason</dt><dd>' + escapeHtml(Core.reasonLabel(game.reason) || "—") + "</dd></div>" +
      '<div><dt>Plies</dt><dd>' + escapeHtml(plies) + "</dd></div>" +
      '<div class="wide"><dt>Start FEN</dt><dd><code class="fen">' +
      escapeHtml(game.start_fen || "") + "</code></dd></div>" +
      "</dl>"
    );
  }

  function viewerMarkup(game, timeline) {
    const speedOptions = SPEEDS.map(function (speed, index) {
      return (
        '<option value="' + index + '"' + (index === DEFAULT_SPEED_INDEX ? " selected" : "") +
        ">" + speed.label + "</option>"
      );
    }).join("");
    return (
      '<section class="game-header">' +
      '<h2 class="match-title">' + escapeHtml(Core.playerName(game.red)) +
      ' <span class="vs">vs</span> ' + escapeHtml(Core.playerName(game.black)) + "</h2>" +
      metaMarkup(game, timeline) +
      openingMarkup(game, timeline) +
      "</section>" +
      '<div class="viewer-body">' +
      '<div class="board-column">' +
      '<div class="board-holder"></div>' +
      '<label class="letter-toggle"><input type="checkbox"> Letter pieces</label>' +
      "</div>" +
      '<div class="side-column">' +
      '<p class="banner" hidden></p>' +
      '<p class="status" aria-live="polite"></p>' +
      '<p class="details"></p>' +
      '<div class="controls">' +
      '<button type="button" data-action="first" aria-label="First ply">⏮ First</button>' +
      '<button type="button" data-action="prev" aria-label="Previous ply">◀ Prev</button>' +
      '<button type="button" data-action="play" aria-label="Play or pause">▶ Play</button>' +
      '<button type="button" data-action="next" aria-label="Next ply">Next ▶</button>' +
      '<button type="button" data-action="last" aria-label="Last ply">Last ⏭</button>' +
      '<label class="speed">Speed <select class="speed-select">' + speedOptions + "</select></label>" +
      "</div>" +
      '<input class="slider" type="range" min="0" max="' + (timeline.states.length - 1) +
      '" value="0" aria-label="Ply">' +
      '<ul class="move-list" aria-label="Moves"></ul>' +
      '<p class="keyboard-hint">Keyboard: ← → step · Home / End · Space play/pause</p>' +
      "</div>" +
      "</div>"
    );
  }

  // ---------------------------------------------------------------------------
  // Viewer
  // ---------------------------------------------------------------------------

  class Viewer {
    constructor(container, game, timeline) {
      this.container = container;
      this.game = game;
      this.timeline = timeline;
      this.lastIndex = timeline.states.length - 1;
      this.index = 0;
      this.speedIndex = DEFAULT_SPEED_INDEX;
      this.delayMs = SPEEDS[DEFAULT_SPEED_INDEX].delayMs;
      this.playing = false;
      this.timer = null;
      this.letterMode = false;

      this.boardHolder = container.querySelector(".board-holder");
      this.status = container.querySelector(".status");
      this.details = container.querySelector(".details");
      this.banner = container.querySelector(".banner");
      this.slider = container.querySelector(".slider");
      this.moveList = container.querySelector(".move-list");
      this.playButton = container.querySelector('[data-action="play"]');
      this.letterToggle = container.querySelector(".letter-toggle input");

      this.controls = container.querySelector(".controls");
      this._onControlClick = this._handleAction.bind(this);
      this.controls.addEventListener("click", this._onControlClick);
      this.slider.addEventListener("input", () => {
        this.pause();
        this.show(Number(this.slider.value));
      });
      this.controls.querySelector(".speed-select").addEventListener("change", (event) => {
        this.speedIndex = Number(event.target.value);
        this.delayMs = SPEEDS[this.speedIndex].delayMs;
        if (this.playing) {
          window.clearTimeout(this.timer);
          this._schedule();
        }
      });
      this.letterToggle.addEventListener("change", () => {
        this.letterMode = this.letterToggle.checked;
        this.show(this.index);
      });
      this._onMoveClick = (event) => {
        const item = event.target.closest("li[data-index]");
        if (item) {
          this.pause();
          this.show(Number(item.dataset.index));
        }
      };
      this.moveList.addEventListener("click", this._onMoveClick);
      this._onKeyDown = (event) => {
        if (event.target && /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) {
          return;
        }
        if (event.key === "ArrowLeft") {
          this.pause();
          this.show(this.index - 1);
          event.preventDefault();
        } else if (event.key === "ArrowRight") {
          this.pause();
          this.show(this.index + 1);
          event.preventDefault();
        } else if (event.key === "Home") {
          this.pause();
          this.show(0);
          event.preventDefault();
        } else if (event.key === "End") {
          this.pause();
          this.show(this.lastIndex);
          event.preventDefault();
        } else if (event.key === " ") {
          this.togglePlay();
          event.preventDefault();
        }
      };
      document.addEventListener("keydown", this._onKeyDown);
      this._buildMoveList();
    }

    show(index) {
      const next = Math.max(0, Math.min(this.lastIndex, Number(index) || 0));
      this.index = next;
      const state = this.timeline.states[next];
      const ply = next > 0 ? this.timeline.plies[next - 1] : null;
      Board.render(this.boardHolder, state.squares, {
        lastMove: ply ? { from: ply.from, to: ply.to } : null,
        letterMode: this.letterMode,
      });
      this.slider.value = String(next);
      this._updateStatus(ply, next);
      this._updateMoveList(next);
      this._updateBanner(next);
      this._updateButtons(next);
    }

    play() {
      if (this.playing || this.lastIndex === 0) {
        return;
      }
      if (this.index >= this.lastIndex) {
        this.show(0);
      }
      this.playing = true;
      this._updatePlayButton();
      this._schedule();
    }

    pause() {
      this.playing = false;
      if (this.timer !== null) {
        window.clearTimeout(this.timer);
        this.timer = null;
      }
      this._updatePlayButton();
    }

    togglePlay() {
      if (this.playing) {
        this.pause();
      } else {
        this.play();
      }
    }

    destroy() {
      this.pause();
      document.removeEventListener("keydown", this._onKeyDown);
      this.controls.removeEventListener("click", this._onControlClick);
      this.moveList.removeEventListener("click", this._onMoveClick);
      this.container.innerHTML = "";
    }

    // -- private ---------------------------------------------------------

    _schedule() {
      this.timer = window.setTimeout(() => {
        if (!this.playing) {
          return;
        }
        if (this.index >= this.lastIndex) {
          this.pause();
          return;
        }
        this.show(this.index + 1);
        if (this.index >= this.lastIndex) {
          this.pause();
        } else {
          this._schedule();
        }
      }, this.delayMs);
    }

    _handleAction(event) {
      const button = event.target.closest("button[data-action]");
      if (!button) {
        return;
      }
      const action = button.dataset.action;
      if (action === "first") {
        this.pause();
        this.show(0);
      } else if (action === "prev") {
        this.pause();
        this.show(this.index - 1);
      } else if (action === "next") {
        this.pause();
        this.show(this.index + 1);
      } else if (action === "last") {
        this.pause();
        this.show(this.lastIndex);
      } else if (action === "play") {
        this.togglePlay();
      }
    }

    _updateStatus(ply, index) {
      if (!ply) {
        this.status.textContent = "Start position (ply 0 / " + this.lastIndex + ")";
        this.details.textContent =
          this.timeline.openingCount > 0
            ? "The opening moves above are part of the timeline."
            : "";
        return;
      }
      this.status.textContent =
        "Ply " + index + " / " + this.lastIndex + " · " + sideName(ply.side) +
        " moved · " + ply.iccs;
      const fields = Core.moveDetails(ply.record);
      if (fields.length > 0) {
        this.details.textContent = fields.join(" · ");
      } else {
        this.details.textContent = ply.record ? "No per-move data in the file." : "Opening move.";
      }
    }

    _updateBanner(index) {
      const finished = index === this.lastIndex;
      this.banner.hidden = !finished;
      if (!finished) {
        return;
      }
      this.banner.textContent = Core.bannerText(this.game);
      this.banner.className = "banner " + (RESULT_CLASSES[this.game.result] || "");
    }

    _updateButtons(index) {
      const first = this.container.querySelector('[data-action="first"]');
      const prev = this.container.querySelector('[data-action="prev"]');
      const next = this.container.querySelector('[data-action="next"]');
      const last = this.container.querySelector('[data-action="last"]');
      first.disabled = index === 0;
      prev.disabled = index === 0;
      next.disabled = index === this.lastIndex;
      last.disabled = index === this.lastIndex;
      this._updatePlayButton();
    }

    _updatePlayButton() {
      this.playButton.textContent = this.playing ? "❚❚ Pause" : "▶ Play";
    }

    _buildMoveList() {
      const fragment = document.createDocumentFragment();
      this.timeline.plies.forEach((ply, offset) => {
        const item = document.createElement("li");
        item.className = "move";
        item.dataset.index = String(offset + 1);
        const marker = ply.side === "w" ? "R" : "B";
        item.textContent =
          ply.number + ". " + marker + " " + ply.iccs + (ply.record ? "" : " (opening)");
        fragment.appendChild(item);
      });
      this.moveList.appendChild(fragment);
    }

    _updateMoveList(index) {
      const items = this.moveList.children;
      for (let i = 0; i < items.length; i += 1) {
        items[i].classList.toggle("is-active", Number(items[i].dataset.index) === index);
      }
      const active = this.moveList.querySelector(".move.is-active");
      if (active) {
        active.scrollIntoView({ block: "nearest" });
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Loading (file picker, drag and drop, ?game=)
  // ---------------------------------------------------------------------------

  /**
   * Mount a game object into `container`. Throws before touching the DOM if the game
   * is not replayable, so a failed load never blanks an existing viewer.
   */
  function mount(container, game) {
    if (!container) {
      throw new Error("Replay.mount needs a container element");
    }
    const previous = container.__replayViewer;
    if (previous) {
      previous.destroy();
    }
    const timeline = Core.buildTimeline(game);
    container.innerHTML = viewerMarkup(game, timeline);
    const viewer = new Viewer(container, game, timeline);
    container.__replayViewer = viewer;
    viewer.show(0);
    return viewer;
  }

  function setStatus(element, message, isError) {
    if (!element) {
      return;
    }
    element.textContent = message;
    element.classList.toggle("is-error", Boolean(isError));
  }

  /** Parse JSON text and mount it; on any error, show a message and keep the page. */
  function loadGameText(text, sourceName, container, status) {
    let game;
    try {
      game = JSON.parse(text);
    } catch (error) {
      setStatus(status, "Could not parse " + sourceName + ": " + error.message, true);
      return null;
    }
    try {
      const viewer = mount(container, game);
      setStatus(
        status,
        "Loaded " + sourceName + " (" + viewer.timeline.plies.length + " plies)",
        false,
      );
      return viewer;
    } catch (error) {
      setStatus(status, "Could not replay " + sourceName + ": " + error.message, true);
      return null;
    }
  }

  /**
   * Load `?game=<path>` with fetch. Browsers block reading local files from
   * `file://`, so that case only explains how to serve the folder instead.
   */
  function loadFromUrl(container, status, url) {
    if (window.location.protocol === "file:") {
      setStatus(
        status,
        "This page is open from a file, so the browser will not read " + url +
          ". Use the file picker or drag-and-drop, or serve the project folder with " +
          '"python -m http.server" in the project root and open web/index.html with the ?game= parameter.',
        true,
      );
      return;
    }
    fetch(url)
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.text();
      })
      .then(function (text) {
        loadGameText(text, url, container, status);
      })
      .catch(function (error) {
        setStatus(status, "Could not fetch " + url + ": " + error.message, true);
      });
  }

  /** Wire the picker, drag-and-drop and `?game=` into the page's placeholder elements. */
  function init() {
    if (init.initialized) {
      return;
    }
    const container = document.getElementById("replay-root");
    const input = document.getElementById("file-input");
    const status = document.getElementById("load-status");
    if (!container || !input || !status) {
      return;
    }
    init.initialized = true;

    function readFile(file) {
      const reader = new FileReader();
      reader.onload = function () {
        loadGameText(String(reader.result), file.name, container, status);
      };
      reader.onerror = function () {
        setStatus(status, "Could not read " + file.name, true);
      };
      reader.readAsText(file);
    }

    input.addEventListener("change", function () {
      const file = input.files && input.files[0];
      if (file) {
        readFile(file);
      }
      input.value = "";
    });

    document.body.addEventListener("dragover", function (event) {
      event.preventDefault();
      document.body.classList.add("is-dragging");
    });
    document.body.addEventListener("dragleave", function () {
      document.body.classList.remove("is-dragging");
    });
    document.body.addEventListener("drop", function (event) {
      event.preventDefault();
      document.body.classList.remove("is-dragging");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) {
        readFile(file);
      }
    });

    const requested = new URLSearchParams(window.location.search).get("game");
    if (requested) {
      loadFromUrl(container, status, requested);
    }
  }

  global.Replay = {
    mount: mount,
    loadGameText: loadGameText,
    loadFromUrl: loadFromUrl,
    init: init,
  };

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
