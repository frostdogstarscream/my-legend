"""Phase 9: Clue Ledger — structured evidence tracking for Mystery Mode.

Each clue has:
  - clue_id: unique identifier
  - description: what was introduced
  - introduced_at: where (chapter or scene)
  - clue_type: evidence | red_herring | observation | testimony
  - supports: what hypothesis it supports
  - status: introduced | revisited | resolved | refuted
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ClueEntry(BaseModel):
    clue_id: str
    description: str
    introduced_at: str = ""          # chapter label or scene id
    clue_type: str = "evidence"      # evidence | red_herring | observation | testimony
    supports: str = ""               # hypothesis label
    status: str = "introduced"       # introduced | revisited | resolved | refuted


class ClueLedger(BaseModel):
    """Tracks all clues in a Mystery story."""

    central_question: str = ""
    clues: list[ClueEntry] = Field(default_factory=list)
    reveal_basis: list[str] = Field(default_factory=list)  # clue_ids used in reveal
    resolution: str = ""
    ending_type: str = "closed"  # closed | open

    def add_clue(self, clue: ClueEntry) -> None:
        self.clues.append(clue)

    def get_clue(self, clue_id: str) -> ClueEntry | None:
        for c in self.clues:
            if c.clue_id == clue_id:
                return c
        return None

    def has_reveal_basis(self) -> bool:
        """Check that reveal references known clues."""
        if not self.reveal_basis:
            return False
        known_ids = {c.clue_id for c in self.clues}
        return all(rid in known_ids for rid in self.reveal_basis)

    def clue_count(self) -> int:
        return len(self.clues)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict) -> ClueLedger:
        return cls(**d)