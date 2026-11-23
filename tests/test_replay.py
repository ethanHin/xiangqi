"""Tests for the M7 replay viewer's data and its offline constraints.

The browser code itself is tested in Node (``node web/tests/replay-core.test.js``);
these tests check the data it consumes: every sample game is a valid record, the
samples cover each ending the viewer must render, and the fixtures produced by the
engine match a replay of the same moves. They also enforce the "works from file://"
constraints mechanically.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.board import START_FEN, Board

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = sorted((ROOT / "games" / "samples").glob("*.json"))
FIXTURES = ROOT / "tests" / "fixtures" / "replay" / "replay_fixtures.json"
WEB = ROOT / "web"

REQUIRED_KEYS = {
    "id",
    "red",
    "black",
    "seed",
    "start_fen",
    "opening_moves",
    "moves",
    "result",
    "reason",
    "plies",
}
REASONS = {"checkmate", "stalemate", "repetition", "perpetual_check", "move_cap", "resign"}


def _move(board: Board, iccs: str):
    """The legal move with this ICCS name."""
    for move in board.legal_moves():
        if move.to_iccs() == iccs:
            return move
    raise AssertionError(f"{iccs} is not legal in {board.to_fen()}")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_are_enough_samples() -> None:
    assert len(SAMPLES) >= 6, [path.name for path in SAMPLES]


def test_every_sample_is_a_valid_game_record() -> None:
    for path in SAMPLES:
        game = _load(path)
        assert REQUIRED_KEYS <= set(game), path.name
        assert isinstance(game["moves"], list) and game["moves"], path.name
        assert game["result"] in {"red_win", "black_win", "draw"}, path.name
        assert game["reason"] in REASONS, path.name
        for record in game["moves"]:
            assert isinstance(record["move"], str) and len(record["move"]) == 4, path.name


def test_samples_cover_every_ending_the_viewer_renders() -> None:
    reasons = set()
    has_alpha_beta_stats = False
    for path in SAMPLES:
        game = _load(path)
        reasons.add(game["reason"])
        types = {game["red"].get("type"), game["black"].get("type")}
        if "alphabeta" in types:
            stat_moves = [
                move
                for move in game["moves"]
                if move.get("depth") and move.get("nodes") and move.get("eval") is not None
            ]
            has_alpha_beta_stats = has_alpha_beta_stats or bool(stat_moves)
    assert {"checkmate", "stalemate", "perpetual_check", "move_cap"} <= reasons
    assert has_alpha_beta_stats, "need an alpha-beta game with depth/nodes/eval"


def test_fixtures_match_an_engine_replay_of_the_same_moves() -> None:
    fixtures = _load(FIXTURES)
    assert len(fixtures["samples"]) == len(SAMPLES)
    for sample in fixtures["samples"]:
        game = _load(ROOT / sample["file"])
        if sample["opening_count"]:
            board = Board.from_fen(START_FEN)
            fens = [board.to_fen()]
            for name in game["opening_moves"]:
                board.make_move(_move(board, name))
                fens.append(board.to_fen())
            assert board.to_fen() == game["start_fen"], sample["file"]
        else:
            board = Board.from_fen(game["start_fen"])
            fens = [board.to_fen()]

        for record in game["moves"]:
            board.make_move(_move(board, record["move"]))
            fens.append(board.to_fen())

        assert fens == sample["timeline"], sample["file"]
        assert len(sample["timeline"]) == sample["plies"] + 1, sample["file"]


def test_fixture_file_names_every_sample() -> None:
    fixtures = _load(FIXTURES)
    fixture_files = {sample["file"] for sample in fixtures["samples"]}
    sample_files = {path.relative_to(ROOT).as_posix() for path in SAMPLES}
    assert fixture_files == sample_files


def test_web_assets_have_no_network_or_module_references() -> None:
    files = [path for path in WEB.rglob("*") if path.suffix in {".html", ".js", ".css"}]
    assert files, "the web folder should contain the viewer"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text, f"{path.name} references http://"
        assert "https://" not in text, f"{path.name} references https://"
        assert 'type="module"' not in text, f"{path.name} uses an ES module"
        assert "type='module'" not in text, f"{path.name} uses an ES module"


def test_index_loads_the_classic_scripts_in_dependency_order() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    scripts = (
        "replay-core.js",
        "play-core.js",
        "board.js",
        "replay.js",
        "setup.js",
        "play.js",
        "app.js",
    )
    positions = []
    for script in scripts:
        assert f'src="{script}"' in html, script
        positions.append(html.index(f'src="{script}"'))
    assert positions == sorted(positions), "scripts must load in dependency order"
    assert 'type="module"' not in html


def test_node_core_tests_are_committed() -> None:
    assert (WEB / "tests" / "replay-core.test.js").is_file()
    assert (WEB / "tests" / "play-core.test.js").is_file()
    assert (WEB / "tests" / "replay-mount.test.js").is_file()
