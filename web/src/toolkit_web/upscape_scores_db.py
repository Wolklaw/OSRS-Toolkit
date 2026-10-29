from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# A global leaderboard for Upscape (a separate, unrelated game by the same dev), stored in its own file so it can never
# touch a trading journal. One row per (player_id, mode): a new submission only overwrites it if it is actually better,
# ranked ascending for sprint_100 (lowest time wins) and descending for everything else (highest score wins).
SCHEMA = """
CREATE TABLE IF NOT EXISTS upscape_score (
    player_id   TEXT NOT NULL,
    mode        TEXT NOT NULL,
    name        TEXT NOT NULL,
    score       INTEGER NOT NULL,
    floor       INTEGER NOT NULL,
    combo       INTEGER NOT NULL,
    climber     TEXT NOT NULL,
    version     TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (player_id, mode)
);

CREATE INDEX IF NOT EXISTS upscape_score_by_mode ON upscape_score(mode, score);
"""

MODES = (
    "classic",
    "classic_wide",
    "sprint_100",
    "chaos",
    "chaos_wide",
    "rockfall",
    "rockfall_wide",
)
ASCENDING_MODES = {"sprint_100"}


def _stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class UpscapeScore:
    name: str
    score: int
    floor: int
    combo: int
    climber: str


class UpscapeScoresDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def submit(
        self,
        player_id: str,
        mode: str,
        name: str,
        score: int,
        floor: int,
        combo: int,
        climber: str,
        version: str | None,
    ) -> bool:
        ascending = mode in ASCENDING_MODES
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT score FROM upscape_score WHERE player_id = ? AND mode = ?",
                (player_id, mode),
            ).fetchone()
            if existing is not None:
                better = score < existing["score"] if ascending else score > existing["score"]
                if not better:
                    return False
            connection.execute(
                """
                INSERT INTO upscape_score
                    (player_id, mode, name, score, floor, combo, climber, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, mode) DO UPDATE SET
                    name = excluded.name, score = excluded.score, floor = excluded.floor,
                    combo = excluded.combo, climber = excluded.climber, version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (player_id, mode, name, score, floor, combo, climber, version, _stamp()),
            )
            return True

    def top(self, mode: str, limit: int) -> list[UpscapeScore]:
        order = "ASC" if mode in ASCENDING_MODES else "DESC"
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT name, score, floor, combo, climber FROM upscape_score
                WHERE mode = ? ORDER BY score {order} LIMIT ?
                """,
                (mode, limit),
            ).fetchall()
        return [
            UpscapeScore(row["name"], row["score"], row["floor"], row["combo"], row["climber"])
            for row in rows
        ]
