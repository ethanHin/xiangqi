/*
 * app.js -- application shell: API access, screen routing, and shared state.
 *
 * The three screens live in index.html; this file wires the nav, talks to the server
 * through one relative base (API_BASE) so no absolute URL appears anywhere, detects
 * the file:// case where the API does not exist, and hands games to the setup, play
 * and replay screens.
 *
 * The UI never decides legality or results: it renders what the API returns.
 */
(function (global) {
  "use strict";

  const PlayCore = global.PlayCore;

  // Relative to the document, so it works on any host and port: "api/bots" resolves
  // against the page URL. This is the only place a path prefix is named.
  const API_BASE = "api";

  // Finished games are also served as static JSON files (/games/<id>.json), so the
  // Replay screen can open one after the server has forgotten it.
  const GAMES_BASE = "games";
  const SCREENS = ["setup", "play", "replay"];

  const state = {
    bots: [],
    botsError: null,
    livePlay: true,
    screen: "setup",
    game: null,
    humanSide: null,
    play: null,
  };

  // ---------------------------------------------------------------------------
  // API client
  // ---------------------------------------------------------------------------

  function apiUrl(path) {
    return API_BASE + "/" + String(path).replace(/^\/+/, "");
  }

  class ApiError extends Error {
    constructor(kind, status, detail) {
      super(detail || kind);
      this.kind = kind;
      this.status = status;
      this.detail = detail;
    }
  }

  /** Fetch JSON, turning transport and HTTP failures into ``ApiError``. */
  async function request(method, path, body) {
    let response;
    try {
      response = await fetch(apiUrl(path), {
        method: method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new ApiError("network", 0, error.message);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : response.statusText;
      throw new ApiError("http", response.status, detail);
    }
    return payload;
  }

  const api = {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
  };

  /** Fetch a saved game's JSON from the static /games/ mount (no API involved). */
  async function fetchSavedGame(gameId) {
    let response;
    try {
      response = await fetch(GAMES_BASE + "/" + gameId + ".json");
    } catch (error) {
      throw new ApiError("network", 0, error.message);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok || !payload) {
      throw new ApiError("http", response.status, response.statusText);
    }
    return payload;
  }

  /**
   * Load a game for the Replay screen.
   *
   * A game still in memory is fetched through `/export`. A saved-only game (the
   * server was restarted, or the live copy was evicted) is read from
   * `games/<id>.json`; that fallback also covers a live game that disappears between
   * the list and the click.
   */
  async function loadGameForReplay(gameId, savedOnly) {
    if (!savedOnly) {
      try {
        return await api.get("games/" + gameId + "/export");
      } catch (error) {
        if (error.status !== 404) {
          throw error;
        }
      }
    }
    return fetchSavedGame(gameId);
  }

  // ---------------------------------------------------------------------------
  // Screens
  // ---------------------------------------------------------------------------

  function screenElement(name) {
    return document.getElementById("screen-" + name);
  }

  function showScreen(name) {
    if (SCREENS.indexOf(name) < 0) {
      return;
    }
    state.screen = name;
    for (const candidate of SCREENS) {
      screenElement(candidate).hidden = candidate !== name;
    }
    for (const tab of document.querySelectorAll("[data-screen]")) {
      tab.classList.toggle("is-active", tab.getAttribute("data-screen") === name);
    }
  }

  function wireTabs() {
    for (const tab of document.querySelectorAll("[data-screen]")) {
      tab.addEventListener("click", () => {
        const target = tab.getAttribute("data-screen");
        if (target === "play" && !state.game) {
          return; // nothing to play yet
        }
        showScreen(target);
      });
    }
  }

  function humanSideOf(game) {
    if (!game || !game.players) {
      return null;
    }
    if (game.players.red.type === "human") {
      return "red";
    }
    if (game.players.black.type === "human") {
      return "black";
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // Games
  // ---------------------------------------------------------------------------

  /** Create a game on the server and open it. Throws ``ApiError`` for the setup form. */
  async function startGame(redSpec, blackSpec, extra) {
    const body = { red: redSpec, black: blackSpec };
    if (extra && extra.seed !== undefined && extra.seed !== null) {
      body.seed = extra.seed;
    }
    if (extra && extra.opening_plies !== undefined && extra.opening_plies !== null) {
      body.opening_plies = extra.opening_plies;
    }
    const game = await api.post("games", body);
    openGame(game);
    return game;
  }

  /** Mount the play screen for a game state returned by the API. */
  function openGame(game) {
    state.game = game;
    state.humanSide = humanSideOf(game);
    if (state.play) {
      state.play.destroy();
    }
    document.querySelector('[data-screen="play"]').disabled = false;
    showScreen("play");
    state.play = global.Play.mount(document.getElementById("play-root"), {
      api: api,
      game: game,
      humanSide: state.humanSide,
      onNewGame: () => {
        showScreen("setup");
        if (state.setup) {
          state.setup.refreshRecent();
        }
      },
      onOpenReplay: (exported) => openReplay(exported, true),
      onStateChange: (current) => {
        state.game = current;
        state.humanSide = humanSideOf(current);
      },
    });
  }

  /** Mount the replay screen; ``fromPlay`` shows the "Back to game" button. */
  function openReplay(game, fromPlay) {
    document.getElementById("back-to-game").hidden = !fromPlay;
    try {
      global.Replay.mount(document.getElementById("replay-root"), game);
    } catch (error) {
      const status = document.getElementById("load-status");
      status.textContent = "Could not replay this game: " + error.message;
      status.classList.add("is-error");
    }
    showScreen("replay");
  }

  // ---------------------------------------------------------------------------
  // Startup
  // ---------------------------------------------------------------------------

  async function init() {
    wireTabs();
    document.getElementById("back-to-game").addEventListener("click", () => showScreen("play"));

    const params = new URLSearchParams(global.location.search);
    const requested = params.get("game");
    state.livePlay = global.location.protocol !== "file:";

    if (!state.livePlay) {
      // Opened by double-click: there is no server, so only replay is possible.
      document.querySelector('[data-screen="setup"]').hidden = true;
      document.querySelector('[data-screen="play"]').hidden = true;
      const notice = document.getElementById("app-notice");
      notice.hidden = false;
      notice.textContent =
        "Live play needs the server: run python -m server and open the address it prints. " +
        "Replay of saved game files still works here.";
      showScreen("replay");
      return;
    }

    showScreen(requested ? "replay" : "setup");

    try {
      const payload = await api.get("bots");
      state.bots = payload.bots || [];
    } catch (error) {
      state.botsError = error;
    }

    state.setup = global.Setup.mount(document.getElementById("setup-root"), {
      api: api,
      bots: state.bots,
      botsError: state.botsError,
      onStart: startGame,
      onOpenReplay: (exported) => openReplay(exported, false),
      loadGame: loadGameForReplay,
    });
  }

  global.App = {
    API_BASE: API_BASE,
    apiUrl: apiUrl,
    api: api,
    state: state,
    showScreen: showScreen,
    openGame: openGame,
    openReplay: openReplay,
    startGame: startGame,
    loadGameForReplay: loadGameForReplay,
    humanSideOf: humanSideOf,
  };

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
  // PlayCore is only referenced for the error text used above; keep the import
  // explicit so a missing script is caught early.
  if (!PlayCore) {
    throw new Error("app.js needs play-core.js loaded first");
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
