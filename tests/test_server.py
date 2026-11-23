"""Tests for the live-play server (M8 part 1).

Fast, in-process tests with FastAPI's ``TestClient``: no browser, no real uvicorn.
Each test gets its own ``GameStore`` writing into ``tmp_path``, so nothing touches the
committed ``games/`` folder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.board import Board
from server import app as app_module
from server import games

ROOT = Path(__file__).resolve().parent.parent
PERPETUAL_FEN = "4k4/9/9/9/9/9/9/3R5/9/5K3 w - - 0 1"


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """A client whose store is isolated in ``tmp_path/games``."""
    monkeypatch.setattr(games, "STORE", games.GameStore(save_dir=tmp_path / "games"))
    with TestClient(app_module.app) as test_client:
        yield test_client


def human() -> dict:
    return {"type": "human"}


def bot(bot_type: str, **params) -> dict:
    return {"type": bot_type, "params": params}


def create(client: TestClient, red: dict, black: dict, **extra) -> dict:
    response = client.post("/api/games", json={"red": red, "black": black, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def step(client: TestClient, game_id: str):
    return client.post(f"/api/games/{game_id}/step")


def move(client: TestClient, game_id: str, iccs: str):
    return client.post(f"/api/games/{game_id}/move", json={"move": iccs})


def state_of(client: TestClient, game_id: str) -> dict:
    response = client.get(f"/api/games/{game_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Registry and validation
# ---------------------------------------------------------------------------


def test_bots_endpoint_lists_every_bot_with_defaults(client: TestClient) -> None:
    payload = client.get("/api/bots").json()
    by_type = {entry["type"]: entry for entry in payload["bots"]}
    assert set(by_type) == {"random", "greedy", "alphabeta", "mcts"}

    alphabeta = {param["name"]: param for param in by_type["alphabeta"]["params"]}
    assert alphabeta["depth"]["default"] is None
    assert alphabeta["depth"]["min"] == 1 and alphabeta["depth"]["max"] == 6
    assert alphabeta["time_limit_ms"]["default"] == 2000.0
    assert alphabeta["evaluator"]["default"] == "material"
    assert alphabeta["evaluator"]["choices"] == [
        "material",
        "material_pst",
        "material_pst_mobility",
        "full",
    ]
    assert alphabeta["quiescence"]["default"] is False

    mcts = {param["name"]: param for param in by_type["mcts"]["params"]}
    assert mcts["iterations"]["default"] == 1000
    assert mcts["exploration"]["default"] == 1.4
    assert mcts["rollout_depth"]["default"] == 30
    assert mcts["evaluator"]["default"] == "full"
    assert mcts["capture_bias"]["default"] == 0.0


def test_every_bot_can_be_created_with_defaults(client: TestClient) -> None:
    for bot_type in ("random", "greedy", "alphabeta", "mcts"):
        state = create(client, bot(bot_type), bot("random"), seed=1, opening_plies=0)
        assert state["players"]["red"]["type"] == bot_type
        # Defaults were filled in, so the applied spec is complete.
        if bot_type == "alphabeta":
            assert state["players"]["red"]["params"]["evaluator"] == "material"


def test_both_human_sides_are_rejected(client: TestClient) -> None:
    response = client.post("/api/games", json={"red": human(), "black": human()})
    assert response.status_code == 400
    assert "human" in response.json()["detail"]


def test_bad_bot_types_and_parameters_are_rejected(client: TestClient) -> None:
    cases = [
        ({"type": "nope", "params": {}}, "unknown bot type"),
        ({"type": "alphabeta", "params": {"nope": 1}}, "nope"),
        ({"type": "alphabeta", "params": {"depth": "three"}}, "depth"),
        ({"type": "alphabeta", "params": {"depth": 9}}, "depth"),
        ({"type": "alphabeta", "params": {"evaluator": "nope"}}, "evaluator"),
        ({"type": "mcts", "params": {"exploration": 99}}, "exploration"),
        ({"type": "mcts", "params": {"capture_bias": 2}}, "capture_bias"),
        ({"type": "mcts", "params": {"quiescence": True}}, "quiescence"),
    ]
    for spec, expected in cases:
        response = client.post("/api/games", json={"red": spec, "black": bot("random")})
        assert response.status_code == 400, (spec, response.text)
        detail = response.json()["detail"]
        assert expected in detail, (spec, detail)
        assert detail.startswith("red:"), detail


def test_unknown_game_id_is_404(client: TestClient) -> None:
    assert client.get("/api/games/nope").status_code == 404
    assert step(client, "nope").status_code == 404
    assert move(client, "nope", "h2e2").status_code == 404


def test_state_response_shape(client: TestClient) -> None:
    state = create(client, human(), bot("random"), seed=3, opening_plies=0)
    assert set(state) == {
        "id",
        "fen",
        "side_to_move",
        "players",
        "legal_moves",
        "in_check",
        "last_move",
        "moves",
        "result",
        "reason",
        "plies",
    }
    assert state["side_to_move"] == "red"
    assert len(state["legal_moves"]) == 44
    assert state["in_check"] is False
    assert state["last_move"] is None
    assert state["plies"] == 0
    assert state["result"] is None and state["reason"] is None
    assert state["players"]["black"]["type"] == "random"


# ---------------------------------------------------------------------------
# Moves and turns
# ---------------------------------------------------------------------------


def test_human_move_then_bot_step(client: TestClient) -> None:
    state = create(client, human(), bot("alphabeta", depth=1), seed=4, opening_plies=0)
    game_id = state["id"]
    legal = state["legal_moves"]
    assert "h2e2" in legal

    after_human = move(client, game_id, "h2e2")
    assert after_human.status_code == 200, after_human.text
    after_human = after_human.json()
    assert after_human["moves"][0]["move"] == "h2e2"
    assert after_human["last_move"] == "h2e2"
    assert after_human["side_to_move"] == "black"
    bot_replies = after_human["legal_moves"]

    after_bot = step(client, game_id)
    assert after_bot.status_code == 200, after_bot.text
    after_bot = after_bot.json()
    assert len(after_bot["moves"]) == 2
    assert after_bot["moves"][1]["move"] in bot_replies
    assert after_bot["side_to_move"] == "red"
    assert after_bot["search"]["depth"] >= 1
    assert after_bot["search"]["applied_time_limit_ms"] > 0


def test_illegal_and_wrong_turn_moves_leave_the_state_unchanged(client: TestClient) -> None:
    state = create(client, human(), bot("random"), seed=5, opening_plies=0)
    game_id = state["id"]

    before = state_of(client, game_id)
    illegal = move(client, game_id, "a0a9")
    assert illegal.status_code == 400
    assert "illegal move" in illegal.json()["detail"]
    assert state_of(client, game_id) == before

    # A move by the bot's side is refused even though it is legal for Black.
    assert move(client, game_id, "h9g7").status_code == 400

    assert move(client, game_id, "h2e2").status_code == 200
    # It is now Black's turn, and Black is a bot.
    wrong_turn = move(client, game_id, "h9g7")
    assert wrong_turn.status_code == 400
    assert "human" in wrong_turn.json()["detail"]


def test_step_rejected_for_human_turn_and_after_the_game(client: TestClient) -> None:
    state = create(client, human(), bot("random"), seed=6, opening_plies=0)
    game_id = state["id"]
    human_turn = step(client, game_id)
    assert human_turn.status_code == 400
    assert "human" in human_turn.json()["detail"]
    assert state_of(client, game_id)["plies"] == 0

    assert client.post(f"/api/games/{game_id}/resign", json={"color": "red"}).status_code == 200
    after = step(client, game_id)
    assert after.status_code == 400
    assert "over" in after.json()["detail"]


def test_bot_vs_bot_random_reaches_a_terminal_state(client: TestClient) -> None:
    state = create(client, bot("random"), bot("random"), seed=7, opening_plies=4)
    game_id = state["id"]
    steps = 0
    while state["result"] is None:
        response = step(client, game_id)
        assert response.status_code == 200, response.text
        state = response.json()
        steps += 1
        assert state["plies"] <= 300
        assert steps <= 300
    assert state["reason"] in {
        "checkmate",
        "stalemate",
        "repetition",
        "perpetual_check",
        "move_cap",
    }


def test_bot_vs_bot_alphabeta_beats_greedy_to_a_result(client: TestClient) -> None:
    state = create(client, bot("alphabeta", depth=2), bot("greedy"), seed=8, opening_plies=4)
    game_id = state["id"]
    for _ in range(300):
        if state["result"] is not None:
            break
        response = step(client, game_id)
        assert response.status_code == 200, response.text
        state = response.json()
    assert state["result"] is not None
    assert state["reason"] in {
        "checkmate",
        "stalemate",
        "repetition",
        "perpetual_check",
        "move_cap",
    }


# ---------------------------------------------------------------------------
# Undo, resign, export and autosave
# ---------------------------------------------------------------------------


def test_undo_restores_the_exact_previous_position(client: TestClient) -> None:
    state = create(client, human(), bot("alphabeta", depth=1), seed=9, opening_plies=0)
    game_id = state["id"]
    stored = games.STORE.get(game_id)
    before = (stored.board.to_fen(), stored.board.hash, stored.board.ply_count)

    assert move(client, game_id, "h2e2").status_code == 200
    assert step(client, game_id).status_code == 200
    assert len(state_of(client, game_id)["moves"]) == 2

    undone = client.post(f"/api/games/{game_id}/undo")
    assert undone.status_code == 200, undone.text
    undone = undone.json()
    assert undone["moves"] == []
    assert undone["plies"] == 0
    assert (stored.board.to_fen(), stored.board.hash, stored.board.ply_count) == before


def test_undo_after_a_finish_clears_the_result(client: TestClient) -> None:
    state = create(client, human(), bot("random"), seed=10, opening_plies=0)
    game_id = state["id"]
    assert move(client, game_id, "h2e2").status_code == 200
    assert step(client, game_id).status_code == 200
    resigned = client.post(f"/api/games/{game_id}/resign", json={"color": "red"})
    assert resigned.json()["result"] == "black_win"
    assert resigned.json()["reason"] == "resign"

    undone = client.post(f"/api/games/{game_id}/undo")
    assert undone.status_code == 200
    undone = undone.json()
    assert undone["result"] is None
    assert undone["reason"] is None
    assert undone["plies"] == 0


def test_undo_floor_is_the_post_opening_position(client: TestClient) -> None:
    state = create(client, human(), bot("random"), seed=11, opening_plies=6)
    response = client.post(f"/api/games/{state['id']}/undo")
    assert response.status_code == 400
    assert "nothing to undo" in response.json()["detail"]


def test_resign_sets_the_reason_and_saves(client: TestClient, tmp_path) -> None:
    state = create(client, human(), bot("random"), seed=12, opening_plies=0)
    game_id = state["id"]
    response = client.post(f"/api/games/{game_id}/resign", json={"color": "black"})
    assert response.status_code == 200
    assert response.json()["result"] == "red_win"
    assert response.json()["reason"] == "resign"

    exported = client.get(f"/api/games/{game_id}/export").json()
    assert exported["reason"] == "resign"
    assert (tmp_path / "games" / f"{game_id}.json").is_file()

    again = client.post(f"/api/games/{game_id}/resign", json={"color": "black"})
    assert again.status_code == 400
    assert again.json()["detail"] == "the game is over"


def test_export_matches_the_schema_and_replays_in_the_viewer(client: TestClient, tmp_path) -> None:
    state = create(client, bot("alphabeta", depth=1), bot("greedy"), seed=13, opening_plies=4)
    game_id = state["id"]
    for _ in range(300):
        if state["result"] is not None:
            break
        state = step(client, game_id).json()
    assert state["result"] is not None, "game should finish"

    exported = client.get(f"/api/games/{game_id}/export").json()
    assert exported["red"]["type"] == "alphabeta"
    assert exported["black"]["type"] == "greedy"
    assert exported["plies"] == len(exported["opening_moves"]) + len(exported["moves"])
    assert exported["result"] in {"red_win", "black_win", "draw"}
    assert set(exported["moves"][0]) == {"move", "time_ms", "nodes", "depth", "eval"}

    # The saved file is the same JSON.
    saved = json.loads((tmp_path / "games" / f"{game_id}.json").read_text(encoding="utf-8"))
    assert saved == exported

    # Validate the export with the M7 viewer logic: Node if available, else Python.
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(exported), encoding="utf-8")
    node = shutil.which("node")
    final_fen = state["fen"]
    if node:
        core = ROOT / "web" / "replay-core.js"
        script = (
            "const core = require(process.argv[1]);"
            "const game = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));"
            "const t = core.buildTimeline(game);"
            "console.log(t.states.length + ' ' + core.stateToFen(t.states[t.states.length - 1]));"
        )
        result = subprocess.run(
            [node, "-e", script, str(core), str(export_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        parts = result.stdout.strip().split()
        assert int(parts[0]) == exported["plies"] + 1
        assert " ".join(parts[1:]) == final_fen
    else:
        board = Board.from_fen(exported["start_fen"])
        for record in exported["moves"]:
            board.make_move(next(m for m in board.legal_moves() if m.to_iccs() == record["move"]))
        assert board.to_fen() == final_fen


def test_a_finished_game_is_written_once(client: TestClient, tmp_path) -> None:
    state = create(client, human(), bot("random"), seed=14, opening_plies=0)
    game_id = state["id"]
    assert move(client, game_id, "h2e2").status_code == 200
    assert step(client, game_id).status_code == 200
    client.post(f"/api/games/{game_id}/resign", json={"color": "red"})

    saved = list((tmp_path / "games").glob("*.json"))
    assert [path.name for path in saved] == [f"{game_id}.json"]

    # Further reads and a new finish after undo do not create extra files.
    client.get(f"/api/games/{game_id}")
    client.get(f"/api/games/{game_id}/export")
    assert len(list((tmp_path / "games").glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# Server clamp
# ---------------------------------------------------------------------------


def test_server_time_clamp_applies_to_every_bot(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(games, "SERVER_MAX_MOVE_MS", 50.0)

    alpha_state = create(
        client,
        bot("alphabeta", depth=6, time_limit_ms=10_000),
        bot("random"),
        seed=15,
        opening_plies=0,
    )
    alpha_step = step(client, alpha_state["id"])
    assert alpha_step.status_code == 200
    search = alpha_step.json()["search"]
    assert search["requested_time_limit_ms"] == 10_000
    assert search["applied_time_limit_ms"] == 50.0
    assert search["elapsed_ms"] < 2000

    mcts_state = create(
        client,
        bot("mcts", iterations=100_000),
        bot("random"),
        seed=16,
        opening_plies=0,
    )
    mcts_step = step(client, mcts_state["id"])
    assert mcts_step.status_code == 200
    search = mcts_step.json()["search"]
    assert search["requested_time_limit_ms"] is None
    assert search["applied_time_limit_ms"] == 50.0
    assert search["iterations"] is not None and search["iterations"] < 100_000
    assert search["elapsed_ms"] < 2000


# ---------------------------------------------------------------------------
# Concurrency, list and perpetual check
# ---------------------------------------------------------------------------


def test_two_games_advanced_in_parallel_do_not_interfere(client: TestClient) -> None:
    first = create(client, bot("random"), bot("random"), seed=21, opening_plies=0)
    second = create(client, bot("random"), bot("random"), seed=22, opening_plies=0)

    def advance(game_id: str) -> int:
        done = 0
        for _ in range(10):
            response = step(client, game_id)
            assert response.status_code == 200
            done += 1
        return done

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(advance, [first["id"], second["id"]]))
    assert results == [10, 10]

    first_state = state_of(client, first["id"])
    second_state = state_of(client, second["id"])
    assert first_state["plies"] == len(first_state["moves"]) + 0
    assert second_state["plies"] == len(second_state["moves"])
    assert first_state["fen"] != second_state["fen"]


def test_game_list_is_newest_first(client: TestClient) -> None:
    first = create(client, human(), bot("random"), seed=31, opening_plies=0)
    second = create(client, human(), bot("random"), seed=32, opening_plies=0)
    listed = client.get("/api/games").json()["games"]
    assert [entry["id"] for entry in listed[:2]] == [second["id"], first["id"]]
    assert set(listed[0]) == {
        "id",
        "players",
        "result",
        "reason",
        "plies",
        "created",
        "saved",
    }
    assert listed[0]["saved"] is False


def _write_saved_game(
    save_dir: Path,
    game_id: str,
    *,
    modified: float = 1_700_000_100.0,
    **overrides,
) -> Path:
    """Write a valid finished Game JSON under ``save_dir`` with a fixed mtime."""
    save_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": game_id,
        "red": bot("alphabeta", depth=2),
        "black": human(),
        "seed": 7,
        "start_fen": Board.start().to_fen(),
        "opening_moves": [],
        "moves": [],
        "result": "red_win",
        "reason": "checkmate",
        "plies": 9,
    }
    payload.update(overrides)
    path = save_dir / f"{game_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, (modified, modified))
    return path


def test_game_list_includes_saved_games_after_a_restart(client: TestClient, tmp_path) -> None:
    # A "restart" is a fresh in-memory store over the same save directory: the game
    # exists only as games/<id>.json, as far as the server is concerned.
    save_dir = tmp_path / "games"
    game_id = "saved0000001"
    _write_saved_game(save_dir, game_id, modified=1_700_000_100.0)

    listed = client.get("/api/games").json()["games"]
    entry = next(row for row in listed if row["id"] == game_id)
    assert entry["saved"] is True
    assert entry["players"] == {"red": bot("alphabeta", depth=2), "black": human()}
    assert entry["result"] == "red_win"
    assert entry["reason"] == "checkmate"
    assert entry["plies"] == 9
    # created comes from the file's modification time (2023-11-14T22:15:00Z).
    assert entry["created"] == "2023-11-14T22:15:00Z"


def test_saved_scan_skips_invalid_files_and_live_duplicates(client: TestClient, tmp_path) -> None:
    save_dir = tmp_path / "games"
    game = create(client, human(), bot("random"), seed=3, opening_plies=0)
    response = client.post(f"/api/games/{game['id']}/resign", json={"color": "red"})
    assert response.status_code == 200, response.text
    assert (save_dir / f"{game['id']}.json").is_file()

    (save_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (save_dir / "notagame.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    listed = client.get("/api/games").json()["games"]
    ids = [row["id"] for row in listed]
    assert ids.count(game["id"]) == 1
    assert next(row for row in listed if row["id"] == game["id"])["saved"] is False
    assert "broken" not in ids
    assert "notagame" not in ids


def test_game_list_is_capped_at_25_newest(client: TestClient, tmp_path) -> None:
    save_dir = tmp_path / "games"
    for index in range(30):
        _write_saved_game(save_dir, f"saved{index:07d}", modified=1_700_000_000.0 + index)

    listed = client.get("/api/games").json()["games"]
    assert len(listed) == 25
    assert {row["id"] for row in listed} == {f"saved{index:07d}" for index in range(5, 30)}
    assert listed[0]["id"] == "saved0000029"


class _ScriptedBot:
    """A bot that plays a fixed list of ICCS moves (test helper)."""

    type_name = "scripted"

    def __init__(self, move_names: list[str]) -> None:
        self._names = list(move_names)

    @property
    def spec(self) -> dict:
        return {"type": "scripted", "params": {}}

    def choose_move(self, board, budget):
        from bots.base import SearchResult

        name = self._names.pop(0)
        for move in board.legal_moves():
            if move.to_iccs() == name:
                return SearchResult(move=move)
        raise AssertionError(f"{name} is not legal in {board.to_fen()}")


def test_perpetual_check_through_the_api(client: TestClient) -> None:
    # A game whose bots follow the M2 perpetual-check sequence. The store's
    # bot-builder seam keeps the registry untouched while the API's /step drives it.
    red_spec = games.registry.validate_player_spec(bot("alphabeta"), "red")
    black_spec = games.registry.validate_player_spec(bot("alphabeta"), "black")
    red_bot = _ScriptedBot(["d2e2", "e2d2", "d2e2", "e2d2"])
    black_bot = _ScriptedBot(["e9d9", "d9e9", "e9d9", "d9e9"])

    def builder(spec, seed):
        return red_bot if spec is red_spec else black_bot

    game = games.STORE.create(
        red_spec,
        black_spec,
        seed=41,
        opening_plies=0,
        start_fen=PERPETUAL_FEN,
        bot_builder=builder,
    )
    state = game.state()
    for _ in range(8):
        response = step(client, game.id)
        assert response.status_code == 200, response.text
        state = response.json()
    assert state["result"] == "black_win"
    assert state["reason"] == "perpetual_check"
    assert state["plies"] == 8


def test_store_cap_evicts_finished_games_first(tmp_path) -> None:
    store = games.GameStore(save_dir=tmp_path / "games", max_games=2)
    red = games.registry.validate_player_spec(bot("random"), "red")
    black = games.registry.validate_player_spec(bot("random"), "black")

    first = store.create(red, black, seed=1, opening_plies=0)
    second = store.create(red, black, seed=2, opening_plies=0)
    first.resign("red")  # mark the oldest as finished
    third = store.create(red, black, seed=3, opening_plies=0)

    ids = {entry["id"]: entry for entry in store.list()}
    # The finished game left memory but its saved file is still listed.
    assert ids[first.id]["saved"] is True
    assert ids[second.id]["saved"] is False
    assert ids[third.id]["saved"] is False
    assert {second.id, third.id} <= set(ids)
