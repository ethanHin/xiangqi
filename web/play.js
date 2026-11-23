/*
 * play.js -- the live play screen.
 *
 * One controller per game. It renders the board from the API's `fen`, sends human
 * moves, drives bot turns by calling /step, and shows what the last search used.
 * It never decides legality, check, or the result: the board's legal destinations,
 * `in_check`, `result` and `reason` all come from the server.
 *
 * Requests are strictly serialized by a `busy` flag (the requirement is one in-flight
 * call per game), and auto-stepping is limited to human-vs-bot games and never runs
 * after an undo, so a deterministic bot cannot be stepped in a loop with undo.
 */
(function (global) {
  "use strict";

  const PlayCore = global.PlayCore;
  const ReplayCore = global.ReplayCore;
  const Board = global.XiangqiBoard;

  const DEFAULT_SPEED_MS = 300;
  const MAX_SPEED_MS = 2000;
  const THINKING_REFRESH_MS = 200;

  /** True when a /step ``search`` block has numbers worth showing (searching bots only). */
  function hasSearchStats(search) {
    return (
      Boolean(search) &&
      ((search.depth || 0) > 0 || (search.nodes || 0) > 0 || (search.iterations || 0) > 0)
    );
  }

  function markup() {
    return [
      '<div class="viewer-body">',
      '  <div class="board-column">',
      '    <div class="board-holder" aria-label="Game board"></div>',
      '    <label class="letter-toggle"><input type="checkbox" class="letter-toggle-input"> Letter pieces</label>',
      "  </div>",
      '  <div class="side-column">',
      '    <p class="banner" hidden></p>',
      '    <p class="names-line"></p>',
      '    <p class="turn-line"></p>',
      '    <p class="check-line" hidden>Check</p>',
      '    <p class="thinking" hidden></p>',
      '    <p class="message" hidden></p>',
      '    <div class="play-controls">',
      '      <button type="button" data-action="play" hidden>▶ Play</button>',
      '      <button type="button" data-action="step" hidden>Step</button>',
      '      <button type="button" data-action="bot-move" hidden>Bot move</button>',
      '      <button type="button" data-action="undo">Undo</button>',
      '      <button type="button" data-action="resign">Resign</button>',
      '      <button type="button" data-action="new-game">New game</button>',
      '      <button type="button" data-action="replay">Open in replay</button>',
      '      <button type="button" data-action="download">Download game JSON</button>',
      '      <label class="speed-control" hidden>Speed',
      '        <input type="range" class="speed-range" min="0" max="2000" step="50">',
      '        <span class="speed-value"></span>',
      "      </label>",
      "    </div>",
      '    <p class="search-info"></p>',
      '    <ul class="move-list" aria-label="Moves"></ul>',
      "  </div>",
      "</div>",
    ].join("");
  }

  class PlayController {
    constructor(container, options) {
      this.container = container;
      this.options = options;
      this.api = options.api;
      this.humanSide = options.humanSide; // "red" | "black" | null (bot vs bot)
      this.game = null;
      this.busy = false;
      this.playing = false;
      this.gone = false;
      this.speedMs = DEFAULT_SPEED_MS;
      this.selected = null;
      this.lastSearch = null;
      this.letterMode = false;
      this.flipped = this.humanSide === "black";
      this.pausePending = false;
      this.stepTimer = null;
      this.thinkingTimer = null;
      this.thinkingSince = 0;

      container.innerHTML = markup();
      this.boardHolder = container.querySelector(".board-holder");
      this.banner = container.querySelector(".banner");
      this.namesLine = container.querySelector(".names-line");
      this.turnLine = container.querySelector(".turn-line");
      this.checkLine = container.querySelector(".check-line");
      this.thinking = container.querySelector(".thinking");
      this.messageBox = container.querySelector(".message");
      this.searchInfo = container.querySelector(".search-info");
      this.moveList = container.querySelector(".move-list");
      this.speedControl = container.querySelector(".speed-control");
      this.speedRange = container.querySelector(".speed-range");
      this.speedValue = container.querySelector(".speed-value");
      this.playButton = container.querySelector('[data-action="play"]');
      this.stepButton = container.querySelector('[data-action="step"]');
      this.botMoveButton = container.querySelector('[data-action="bot-move"]');
      this.undoButton = container.querySelector('[data-action="undo"]');
      this.resignButton = container.querySelector('[data-action="resign"]');
      this.newGameButton = container.querySelector('[data-action="new-game"]');
      this.replayButton = container.querySelector('[data-action="replay"]');
      this.downloadButton = container.querySelector('[data-action="download"]');
      this.letterToggle = container.querySelector(".letter-toggle-input");
      this.buttons = [
        this.playButton,
        this.stepButton,
        this.botMoveButton,
        this.undoButton,
        this.resignButton,
        this.newGameButton,
        this.replayButton,
        this.downloadButton,
      ];

      this.onBoardClick = (event) => this.handleBoardClick(event);
      this.boardHolder.addEventListener("click", this.onBoardClick);
      container.querySelector(".play-controls").addEventListener("click", (event) => this.handleControl(event));
      this.letterToggle.addEventListener("change", () => {
        this.letterMode = this.letterToggle.checked;
        this.render();
      });
      this.speedRange.addEventListener("input", () => {
        this.speedMs = Number(this.speedRange.value);
        this.updateSpeedLabel();
        if (this.playing && !this.busy) {
          this.scheduleStep();
        }
      });
      this.speedRange.max = String(MAX_SPEED_MS);
      this.speedRange.value = String(DEFAULT_SPEED_MS);
      this.updateSpeedLabel();
    }

    // -- public ----------------------------------------------------------

    update(game) {
      this.game = game;
      this.selected = null;
      this.render();
      if (this.options.onStateChange) {
        this.options.onStateChange(game);
      }
    }

    destroy() {
      this.pause();
      if (this.thinkingTimer) {
        clearInterval(this.thinkingTimer);
        this.thinkingTimer = null;
      }
      this.boardHolder.removeEventListener("click", this.onBoardClick);
    }

    // -- rendering -------------------------------------------------------

    render() {
      if (!this.game) {
        return;
      }
      this.renderBoard();
      this.renderStatus();
      this.renderBanner();
      this.renderSearch();
      this.renderMoveList();
      this.renderButtons();
    }

    renderBoard() {
      const parsed = ReplayCore.parseFen(this.game.fen);
      const lastMove = this.game.last_move ? PlayCore.parseMove(this.game.last_move) : null;
      const destinations =
        this.selected === null
          ? []
          : PlayCore.destinationsFor(this.game.legal_moves, this.selected).map((entry) => entry.to);
      const checkSquare = this.game.in_check
        ? PlayCore.generalSquare(parsed.squares, this.game.side_to_move)
        : -1;
      Board.render(this.boardHolder, parsed.squares, {
        lastMove: lastMove,
        letterMode: this.letterMode,
        flipped: this.flipped,
        selected: this.selected,
        destinations: destinations,
        checkSquare: checkSquare,
      });
    }

    renderStatus() {
      const players = this.game.players;
      this.namesLine.textContent =
        "Red: " + ReplayCore.playerName(players.red) +
        " · Black: " + ReplayCore.playerName(players.black);
      if (this.game.result) {
        this.turnLine.textContent = "Game over";
      } else {
        const turn = this.game.side_to_move;
        this.turnLine.textContent =
          (turn === "red" ? "Red" : "Black") + " to move" + (this.humanToMove() ? " (your move)" : "");
      }
      this.checkLine.hidden = !this.game.in_check;
    }

    renderBanner() {
      const finished = Boolean(this.game.result);
      this.banner.hidden = !finished;
      if (finished) {
        this.banner.textContent = ReplayCore.bannerText(this.game);
        this.banner.className = "banner " + (this.game.result === "draw" ? "is-draw" : "is-win");
      }
    }

    renderSearch() {
      if (this.lastSearch && hasSearchStats(this.lastSearch)) {
        this.searchInfo.textContent = "Last search: " + PlayCore.formatSearch(this.lastSearch);
        return;
      }
      const last = this.game.moves[this.game.moves.length - 1];
      if (last && (last.nodes || last.depth)) {
        this.searchInfo.textContent =
          "Last move: depth " + last.depth + " · " + last.nodes + " nodes · eval " +
          last.eval + " · " + last.time_ms + " ms";
      } else {
        this.searchInfo.textContent = "";
      }
    }

    renderMoveList() {
      this.moveList.innerHTML = "";
      this.moveList.hidden = this.game.moves.length === 0;
      this.game.moves.forEach((move, index) => {
        const item = document.createElement("li");
        item.textContent = index + 1 + ". " + move.move;
        this.moveList.appendChild(item);
      });
      this.moveList.scrollTop = this.moveList.scrollHeight;
    }

    renderButtons() {
      const over = Boolean(this.game.result);
      const botVsBot = this.humanSide === null;
      const blocked = this.busy || this.gone;

      this.playButton.hidden = !botVsBot;
      this.stepButton.hidden = !botVsBot;
      this.speedControl.hidden = !botVsBot;
      this.playButton.textContent = this.pausePending
        ? "⏸ Pausing…"
        : this.playing
          ? "❚❚ Pause"
          : "▶ Play";
      this.resignButton.hidden = botVsBot;
      this.botMoveButton.hidden = botVsBot || over || !this.botToMove();

      this.undoButton.disabled = blocked;
      this.resignButton.disabled = blocked || over;
      this.newGameButton.disabled = this.busy;
      this.replayButton.disabled = blocked;
      this.downloadButton.disabled = blocked;
      // Pause stays clickable while a bot is thinking: it stops the loop as soon as
      // the in-flight move returns (see PlayCore.playToggleEnabled).
      this.playButton.disabled = !PlayCore.playToggleEnabled({
        playing: this.playing,
        busy: this.busy,
        gone: this.gone,
        over: over,
      });
      this.stepButton.disabled = blocked || over;
      this.botMoveButton.disabled = blocked || over;
      this.boardHolder.classList.toggle("is-busy", this.busy);
    }

    updateSpeedLabel() {
      this.speedValue.textContent = this.speedMs + " ms";
    }

    // -- state helpers ---------------------------------------------------

    humanToMove() {
      return this.humanSide !== null && this.game && this.game.side_to_move === this.humanSide;
    }

    botToMove() {
      return Boolean(this.game) && !this.game.result && !this.humanToMove();
    }

    showMessage(text, isError) {
      this.messageBox.textContent = text || "";
      this.messageBox.hidden = !text;
      this.messageBox.classList.toggle("is-error", Boolean(isError));
    }

    // -- requests --------------------------------------------------------

    /** Run one API call with the single-in-flight rule and the thinking indicator. */
    async call(action, lastAction) {
      if (this.busy) {
        return null;
      }
      this.setBusy(true);
      let next = null;
      try {
        next = await action();
      } catch (error) {
        this.showMessage(PlayCore.describeError(error), true);
        if (error.status === 404) {
          this.markGone();
        }
      } finally {
        this.setBusy(false);
      }
      if (next) {
        this.game = next;
        this.lastSearch = next.search || null;
        this.selected = null;
        this.showMessage("", false);
        this.render();
        if (this.options.onStateChange) {
          this.options.onStateChange(next);
        }
        this.afterStateChange(lastAction);
      } else {
        this.render();
      }
      return next;
    }

    setBusy(value) {
      this.busy = value;
      if (value) {
        this.thinkingSince = performance.now();
        this.thinking.hidden = false;
        this.updateThinking();
        this.thinkingTimer = setInterval(() => this.updateThinking(), THINKING_REFRESH_MS);
      } else {
        if (this.thinkingTimer) {
          clearInterval(this.thinkingTimer);
          this.thinkingTimer = null;
        }
        this.thinking.hidden = true;
        this.pausePending = false;
      }
      this.render();
    }

    updateThinking() {
      const seconds = ((performance.now() - this.thinkingSince) / 1000).toFixed(1);
      this.thinking.textContent = "Thinking… " + seconds + " s";
    }

    markGone() {
      this.gone = true;
      this.pause();
      this.showMessage(
        "That game no longer exists (the server may have been restarted). Choose New game.",
        true,
      );
    }

    afterStateChange(lastAction) {
      if (!this.game || this.game.result) {
        this.pause();
        return;
      }
      const autoStep = PlayCore.shouldAutoStep({
        humanVsBot: this.humanSide !== null,
        botToMove: this.botToMove(),
        gameOver: false,
        lastAction: lastAction,
      });
      if (autoStep) {
        this.stepOnce();
        return;
      }
      if (this.humanSide === null && this.playing) {
        this.scheduleStep();
      }
    }

    // -- actions ---------------------------------------------------------

    handleBoardClick(event) {
      const target = event.target.closest("[data-square]");
      if (!target || this.busy || this.gone || !this.game || this.game.result) {
        return;
      }
      if (!this.humanToMove()) {
        return;
      }
      const square = Number(target.getAttribute("data-square"));
      const froms = PlayCore.fromSquares(this.game.legal_moves);

      if (this.selected !== null) {
        const destination = PlayCore.destinationsFor(this.game.legal_moves, this.selected).find(
          (entry) => entry.to === square,
        );
        if (destination) {
          this.selected = null;
          this.sendMove(destination.move);
          return;
        }
      }
      if (froms.indexOf(square) >= 0) {
        this.selected = square;
        this.render();
        return;
      }
      if (this.selected !== null) {
        this.selected = null;
        this.render();
      }
    }

    handleControl(event) {
      const button = event.target.closest("button[data-action]");
      if (!button) {
        return;
      }
      const action = button.getAttribute("data-action");
      if (action === "play") {
        this.playing ? this.pause() : this.play();
      } else if (action === "step") {
        this.pause();
        this.stepOnce();
      } else if (action === "bot-move") {
        this.stepOnce();
      } else if (action === "undo") {
        this.undo();
      } else if (action === "resign") {
        this.resign();
      } else if (action === "new-game") {
        this.options.onNewGame();
      } else if (action === "replay") {
        this.openReplay();
      } else if (action === "download") {
        this.download();
      }
    }

    sendMove(move) {
      if (!this.game) {
        return;
      }
      const gameId = this.game.id;
      this.call(() => this.api.post("games/" + gameId + "/move", { move: move }), "human_move");
    }

    stepOnce() {
      if (this.busy || !this.game || this.game.result) {
        return;
      }
      const gameId = this.game.id;
      this.call(() => this.api.post("games/" + gameId + "/step"), "step");
    }

    play() {
      if (this.humanSide !== null || !this.game || this.game.result || this.busy) {
        return;
      }
      this.playing = true;
      this.pausePending = false;
      this.renderButtons();
      this.stepOnce();
    }

    pause() {
      const wasPlaying = this.playing;
      this.playing = false;
      // Pressing Pause during a request: keep showing "Pausing…" until it returns.
      this.pausePending = Boolean(wasPlaying && this.busy);
      if (this.stepTimer) {
        clearTimeout(this.stepTimer);
        this.stepTimer = null;
      }
      if (this.game) {
        this.renderButtons();
      }
    }

    scheduleStep() {
      if (this.stepTimer) {
        clearTimeout(this.stepTimer);
      }
      this.stepTimer = setTimeout(() => {
        this.stepTimer = null;
        if (this.playing && !this.busy) {
          this.stepOnce();
        }
      }, this.speedMs);
    }

    undo() {
      if (!this.game) {
        return;
      }
      this.pause();
      this.lastSearch = null;
      const gameId = this.game.id;
      this.call(() => this.api.post("games/" + gameId + "/undo"), "undo");
    }

    resign() {
      if (this.humanSide === null || !this.game) {
        return;
      }
      const gameId = this.game.id;
      this.call(
        () => this.api.post("games/" + gameId + "/resign", { color: this.humanSide }),
        "resign",
      );
    }

    async openReplay() {
      if (this.busy || !this.game) {
        return;
      }
      this.setBusy(true);
      try {
        const exported = await this.api.get("games/" + this.game.id + "/export");
        this.options.onOpenReplay(exported);
      } catch (error) {
        this.showMessage(PlayCore.describeError(error), true);
        if (error.status === 404) {
          this.markGone();
        }
      } finally {
        this.setBusy(false);
      }
    }

    async download() {
      if (this.busy || !this.game) {
        return;
      }
      this.setBusy(true);
      try {
        const exported = await this.api.get("games/" + this.game.id + "/export");
        const blob = new Blob([JSON.stringify(exported, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "xiangqi-" + this.game.id + ".json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) {
        this.showMessage(PlayCore.describeError(error), true);
      } finally {
        this.setBusy(false);
      }
    }
  }

  /**
   * Mount the play screen and start it.
   *
   * Options: ``api``, ``game`` (a state from the API), ``humanSide``
   * ("red" | "black" | null), ``onNewGame()`` and ``onOpenReplay(exportedGame)``.
   */
  function mount(container, options) {
    const controller = new PlayController(container, options);
    controller.update(options.game);
    controller.afterStateChange("create");
    return controller;
  }

  global.Play = { mount: mount, PlayController: PlayController };
})(typeof globalThis !== "undefined" ? globalThis : this);
