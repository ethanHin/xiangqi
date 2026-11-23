/*
 * setup.js -- the setup screen: pick a player for each side and start a game.
 *
 * The bot list and every player control come from GET /api/bots; nothing here knows
 * the name of a bot or a parameter. Parameter inputs are generated from the schema:
 * number inputs with min/max, selects for enums, checkboxes for booleans, and a
 * "none / no limit" checkbox for nullable parameters. Defaults are the registry's.
 */
(function (global) {
  "use strict";

  const PlayCore = global.PlayCore;
  const ReplayCore = global.ReplayCore;

  const HUMAN = "human";
  const DEFAULT_OPENING_PLIES = 6;
  const OPENING_MIN = 0;
  const OPENING_MAX = 8;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function labelled(labelText, input) {
    const wrapper = element("label", "field");
    wrapper.appendChild(element("span", "field-label", labelText));
    wrapper.appendChild(input);
    return wrapper;
  }

  /**
   * Mount the setup screen into ``container``.
   *
   * Options: ``api``, ``bots`` (from /api/bots), ``botsError``,
   * ``onStart(redSpec, blackSpec, extra)`` and ``onOpenReplay(exportedGame)``.
   * Returns a controller with ``refreshRecent()``.
   */
  function mount(container, options) {
    const bots = options.bots || [];
    const byType = {};
    for (const bot of bots) {
      byType[bot.type] = bot;
    }

    const sides = {
      red: { choice: HUMAN, values: {} },
      black: { choice: bots.length ? bots[0].type : HUMAN, values: {} },
    };
    sides.red.values = defaultsFor(HUMAN);
    sides.black.values = defaultsFor(sides.black.choice);

    let recentGames = [];
    let startButton = null;
    let startNote = null;
    let seedInput = null;
    let openingInput = null;

    const root = element("div", "setup");
    const formArea = element("div", "setup-form");
    const messageBox = element("p", "message");
    messageBox.hidden = true;
    const recentBox = element("section", "recent");
    root.append(formArea, messageBox, recentBox);
    container.innerHTML = "";
    container.appendChild(root);

    if (options.botsError) {
      showMessage(PlayCore.describeError(options.botsError), true);
    } else if (!bots.length) {
      showMessage("No bots were returned by the server.", true);
    }

    // -- form -----------------------------------------------------------

    function defaultsFor(choice) {
      const definition = byType[choice];
      const values = {};
      if (definition) {
        for (const param of definition.params) {
          values[param.name] = param.default;
        }
      }
      return values;
    }

    function buildSide(name) {
      const side = sides[name];
      try {
        return PlayCore.buildPlayerSpec(side.choice, byType[side.choice], side.values);
      } catch (error) {
        showMessage((name === "red" ? "Red: " : "Black: ") + error.message, true);
        return null;
      }
    }

    function renderSide(name) {
      const side = sides[name];
      const fieldset = element("fieldset", "player-side");
      fieldset.appendChild(element("legend", "", name === "red" ? "Red" : "Black"));

      const select = element("select", "player-type");
      select.appendChild(new Option("Human", HUMAN));
      for (const bot of bots) {
        select.appendChild(new Option(bot.label || bot.type, bot.type));
      }
      select.value = side.choice;
      select.addEventListener("change", () => {
        side.choice = select.value;
        side.values = defaultsFor(side.choice);
        showMessage("");
        renderForm();
      });
      fieldset.appendChild(labelled("Player", select));

      const definition = byType[side.choice];
      if (!definition) {
        fieldset.appendChild(element("p", "hint", "This side is played by a person."));
        return fieldset;
      }
      if (!definition.params.length) {
        fieldset.appendChild(element("p", "hint", "This bot has no parameters."));
        return fieldset;
      }

      const params = element("div", "params");
      for (const param of definition.params) {
        params.appendChild(renderParam(side, param));
      }
      fieldset.appendChild(params);
      return fieldset;
    }

    function renderParam(side, param) {
      const row = element("div", "param");
      row.appendChild(element("span", "param-name", param.name));

      let noneBox = null;
      let input = null;

      if (param.type === "enum") {
        input = element("select", "param-input");
        for (const choice of param.choices || []) {
          input.appendChild(new Option(choice, choice));
        }
        input.value = side.values[param.name];
        input.addEventListener("change", () => {
          side.values[param.name] = input.value;
        });
      } else if (param.type === "bool") {
        input = element("input", "param-input");
        input.type = "checkbox";
        input.checked = Boolean(side.values[param.name]);
        input.addEventListener("change", () => {
          side.values[param.name] = input.checked;
        });
      } else {
        input = element("input", "param-input");
        input.type = "number";
        input.step = param.type === "int" ? "1" : "any";
        if (param.min !== null && param.min !== undefined) {
          input.min = String(param.min);
        }
        if (param.max !== null && param.max !== undefined) {
          input.max = String(param.max);
        }
        const current = side.values[param.name];
        input.value = current === null || current === undefined ? "" : String(current);
        input.addEventListener("input", () => {
          if (!(noneBox && noneBox.checked)) {
            side.values[param.name] = input.value;
          }
        });
      }

      row.appendChild(input);

      if (param.nullable) {
        noneBox = element("input", "param-none");
        noneBox.type = "checkbox";
        noneBox.checked = side.values[param.name] === null;
        input.disabled = noneBox.checked;
        noneBox.addEventListener("change", () => {
          input.disabled = noneBox.checked;
          side.values[param.name] = noneBox.checked ? null : input.value;
        });
        row.appendChild(labelled("none / no limit", noneBox));
      }

      row.appendChild(element("p", "param-help", param.help || ""));
      return row;
    }

    function renderOptions() {
      const fieldset = element("fieldset", "game-options");
      fieldset.appendChild(element("legend", "", "Game options"));

      seedInput = element("input", "option-input");
      seedInput.type = "number";
      seedInput.placeholder = "random";
      const seedField = labelled("Seed", seedInput);
      seedField.appendChild(element("p", "param-help", "Optional; makes the opening repeatable."));
      fieldset.appendChild(seedField);

      openingInput = element("input", "option-input");
      openingInput.type = "number";
      openingInput.min = String(OPENING_MIN);
      openingInput.max = String(OPENING_MAX);
      openingInput.placeholder = String(DEFAULT_OPENING_PLIES);
      const openingField = labelled("Opening plies", openingInput);
      openingField.appendChild(
        element("p", "param-help", "Random opening moves, 0-8. Empty means the default of 6."),
      );
      fieldset.appendChild(openingField);
      return fieldset;
    }

    function renderActions() {
      const bar = element("div", "setup-actions");

      const swap = element("button", "button-secondary", "Swap sides");
      swap.type = "button";
      swap.addEventListener("click", () => {
        const red = sides.red;
        sides.red = sides.black;
        sides.black = red;
        showMessage("");
        renderForm();
      });

      startButton = element("button", "button-primary", "Start game");
      startButton.type = "button";
      startButton.dataset.action = "start";
      startButton.addEventListener("click", startGame);

      startNote = element("p", "hint");
      bar.append(swap, startButton, startNote);
      return bar;
    }

    function updateStartState() {
      const bothHuman = sides.red.choice === HUMAN && sides.black.choice === HUMAN;
      if (startButton) {
        startButton.disabled = bothHuman;
      }
      if (startNote) {
        startNote.textContent = bothHuman ? "A game needs at least one bot: pick a bot on one side." : "";
      }
    }

    function renderForm() {
      formArea.innerHTML = "";
      formArea.appendChild(renderSide("red"));
      formArea.appendChild(renderSide("black"));
      formArea.appendChild(renderOptions());
      formArea.appendChild(renderActions());
      updateStartState();
    }

    function showMessage(text, isError) {
      messageBox.textContent = text || "";
      messageBox.hidden = !text;
      messageBox.classList.toggle("is-error", Boolean(isError));
    }

    async function startGame() {
      showMessage("");
      const red = buildSide("red");
      if (!red) {
        return;
      }
      const black = buildSide("black");
      if (!black) {
        return;
      }

      const extra = {};
      const seedText = seedInput.value.trim();
      if (seedText !== "") {
        const seed = Number(seedText);
        if (!Number.isInteger(seed)) {
          showMessage("Seed: enter a whole number.", true);
          return;
        }
        extra.seed = seed;
      }
      const openingText = openingInput.value.trim();
      if (openingText !== "") {
        const plies = Number(openingText);
        if (!Number.isInteger(plies) || plies < OPENING_MIN || plies > OPENING_MAX) {
          showMessage("Opening plies: enter a whole number from 0 to 8.", true);
          return;
        }
        extra.opening_plies = plies;
      }

      if (startButton) {
        startButton.disabled = true;
      }
      try {
        await options.onStart(red, black, extra);
      } catch (error) {
        showMessage(PlayCore.describeError(error), true);
      } finally {
        if (startButton) {
          startButton.disabled = false;
        }
        updateStartState();
      }
    }

    // -- recent games ---------------------------------------------------

    async function openRecent(gameId, button) {
      button.disabled = true;
      showMessage("");
      try {
        const exported = await options.api.get("games/" + gameId + "/export");
        options.onOpenReplay(exported);
      } catch (error) {
        showMessage(PlayCore.describeError(error), true);
      } finally {
        button.disabled = false;
      }
    }

    function renderRecent() {
      recentBox.innerHTML = "";
      const head = element("div", "recent-head");
      head.appendChild(element("h2", "", "Recent games"));
      const refresh = element("button", "button-secondary", "Refresh");
      refresh.type = "button";
      refresh.addEventListener("click", () => refreshRecent());
      head.appendChild(refresh);
      recentBox.appendChild(head);

      if (!recentGames.length) {
        recentBox.appendChild(element("p", "hint", "No games on this server yet."));
        return;
      }

      const list = element("div", "recent-list");
      for (const game of recentGames) {
        const row = element("div", "recent-game");
        row.appendChild(
          element(
            "div",
            "recent-label",
            ReplayCore.playerName(game.players.red) + " vs " + ReplayCore.playerName(game.players.black),
          ),
        );
        const meta =
          (game.result ? ReplayCore.resultLabel(game.result) : "in progress") +
          (game.reason ? " · " + ReplayCore.reasonLabel(game.reason) : "") +
          " · " + game.plies + " plies";
        row.appendChild(element("div", "recent-meta", meta));
        const open = element("button", "button-secondary", "Open in replay");
        open.type = "button";
        open.addEventListener("click", () => openRecent(game.id, open));
        row.appendChild(open);
        list.appendChild(row);
      }
      recentBox.appendChild(list);
    }

    async function refreshRecent() {
      try {
        const payload = await options.api.get("games");
        recentGames = payload.games || [];
        renderRecent();
      } catch (error) {
        recentBox.innerHTML = "";
        const box = element("p", "message is-error", PlayCore.describeError(error));
        recentBox.appendChild(box);
      }
    }

    renderForm();
    refreshRecent();
    return { refreshRecent: refreshRecent };
  }

  global.Setup = { mount: mount };
})(typeof globalThis !== "undefined" ? globalThis : this);
