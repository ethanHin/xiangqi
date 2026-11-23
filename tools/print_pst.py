"""Print the piece-square tables as a Markdown block, for the README.

The README's table values must match ``eval/pst.py``; this prints them in the
document's orientation (rank 9 at the top) so they can be pasted without
transcription errors:

    python -m tools.print_pst
"""

from __future__ import annotations

from engine.types import PieceKind
from eval.pst import RED_TABLES

TITLES = {
    PieceKind.SOLDIER: "Soldiers",
    PieceKind.HORSE: "Horses",
    PieceKind.CHARIOT: "Chariots",
    PieceKind.CANNON: "Cannons",
    PieceKind.ADVISOR: "Advisors",
    PieceKind.ELEPHANT: "Elephants",
    PieceKind.GENERAL: "General",
}


def main() -> None:
    print("Columns a-i, ranks printed from 9 (top) down to 0 (Red's back rank).")
    for kind, rows in RED_TABLES.items():
        print(f"\n**{TITLES[kind]}**\n")
        print("```")
        for rank in range(9, -1, -1):
            print(f"rank {rank}" + "".join(f"{value:>5}" for value in rows[rank]))
        print("```")


if __name__ == "__main__":
    main()
