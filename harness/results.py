"""Aggregate game records: win/draw/loss, game length, and search statistics.

The tournament and the M6 experiments both report through this module, so the
numbers in the README, the CSVs, and the console all mean the same thing:

* ``SideScore`` is the record of one bot type across a set of games, counting only
  games where that bot actually played (self-play is skipped, since a result
  cannot be attributed to one side).
* ``Summary`` describes the whole run: results split by colour, average game
  length, and the search statistics per move.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from harness.match import GameRecord


@dataclass
class SideScore:
    """Wins, draws, and losses for one bot type."""

    bot_type: str
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        """Number of games attributed to this bot."""
        return self.wins + self.draws + self.losses

    @property
    def win_rate(self) -> float:
        """Wins divided by games (0.0 for an empty set)."""
        return self.wins / self.games if self.games else 0.0

    @property
    def score_rate(self) -> float:
        """Win rate with draws counted as half, the usual engine match score."""
        if not self.games:
            return 0.0
        return (self.wins + 0.5 * self.draws) / self.games


@dataclass
class Summary:
    """Whole-tournament statistics."""

    games: int = 0
    red_wins: int = 0
    black_wins: int = 0
    draws: int = 0
    avg_plies: float = 0.0
    avg_move_time_ms: float = 0.0
    avg_nodes: float = 0.0
    avg_depth: float = 0.0
    reasons: dict[str, int] = field(default_factory=dict)
    cap_hits: int = 0


def summarize(records: Sequence[GameRecord]) -> Summary:
    """Aggregate ``records`` into a ``Summary``."""
    summary = Summary(games=len(records))
    total_moves = 0
    total_time = 0.0
    total_nodes = 0
    total_depth = 0
    total_plies = 0

    for record in records:
        if record.result == "red_win":
            summary.red_wins += 1
        elif record.result == "black_win":
            summary.black_wins += 1
        else:
            summary.draws += 1
        reason = record.reason or "unfinished"
        summary.reasons[reason] = summary.reasons.get(reason, 0) + 1
        if reason == "move_cap":
            summary.cap_hits += 1

        total_plies += record.plies
        for move in record.moves:
            total_moves += 1
            total_time += move.time_ms
            total_nodes += move.nodes
            total_depth += move.depth

    if records:
        summary.avg_plies = total_plies / len(records)
    if total_moves:
        summary.avg_move_time_ms = total_time / total_moves
        summary.avg_nodes = total_nodes / total_moves
        summary.avg_depth = total_depth / total_moves
    return summary


def score_for(records: Sequence[GameRecord], bot_type: str) -> SideScore:
    """Wins/draws/losses for ``bot_type`` across games where it played one side.

    A game where the same type plays both sides (self-play) is skipped: the result
    cannot be credited to one side of the same bot.
    """
    score = SideScore(bot_type=bot_type)
    for record in records:
        red_type = record.red.get("type")
        black_type = record.black.get("type")
        if red_type == bot_type and black_type == bot_type:
            continue
        if red_type == bot_type:
            _apply(score, record, as_red=True)
        elif black_type == bot_type:
            _apply(score, record, as_red=False)
    return score


def format_summary(summary: Summary, scores: Sequence[SideScore], title: str) -> str:
    """Render a summary and the per-bot scores as text for the console."""
    lines = [title, "-" * len(title)]
    lines.append(
        f"games {summary.games}   red wins {summary.red_wins}   "
        f"black wins {summary.black_wins}   draws {summary.draws}"
    )
    lines.append(
        f"avg game length {summary.avg_plies:.1f} plies   "
        f"avg move {summary.avg_move_time_ms:.2f} ms   "
        f"avg nodes {summary.avg_nodes:.0f}   avg depth {summary.avg_depth:.2f}"
    )
    reasons = ", ".join(f"{reason} {count}" for reason, count in sorted(summary.reasons.items()))
    lines.append(f"endings: {reasons or 'none'}")
    for score in scores:
        if not score.games:
            continue
        lines.append(
            f"{score.bot_type:>10}: {score.wins}W {score.draws}D {score.losses}L "
            f"of {score.games}   win rate {score.win_rate:.1%}   "
            f"score {score.score_rate:.1%}"
        )
    return "\n".join(lines)


def rows_for_csv(records: Sequence[GameRecord]) -> list[dict[str, Any]]:
    """One flat row per game, for ``experiments/results`` CSVs (used from M6)."""
    rows = []
    for record in records:
        rows.append(
            {
                "id": record.id,
                "seed": record.seed,
                "red": record.red.get("type"),
                "black": record.black.get("type"),
                "result": record.result,
                "reason": record.reason,
                "plies": record.plies,
                "opening_plies": len(record.opening_moves),
                "avg_move_time_ms": _average([m.time_ms for m in record.moves]),
                "avg_nodes": _average([m.nodes for m in record.moves]),
                "avg_depth": _average([m.depth for m in record.moves]),
            }
        )
    return rows


def _apply(score: SideScore, record: GameRecord, as_red: bool) -> None:
    """Add one game to ``score`` from the point of view of the side it played."""
    if record.result == "draw" or record.result is None:
        score.draws += 1
        return
    red_won = record.result == "red_win"
    if red_won == as_red:
        score.wins += 1
    else:
        score.losses += 1


def _average(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
