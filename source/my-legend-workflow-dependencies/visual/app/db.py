from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    settings = Settings.load()
    path = Path(settings.database)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              chapter_title TEXT NOT NULL DEFAULT '',
              source_text TEXT NOT NULL,
              options_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS versions (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
              version_no INTEGER NOT NULL,
              script_text TEXT NOT NULL,
              script_json TEXT NOT NULL,
              tpm_json TEXT NOT NULL,
              quality_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )


def list_projects() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT p.*, COUNT(v.id) AS version_count
               FROM projects p LEFT JOIN versions v ON v.project_id=p.id
               GROUP BY p.id ORDER BY p.updated_at DESC"""
        ).fetchall()
    return [dict(row) | {"options": json.loads(row["options_json"])} for row in rows]


def get_project(project_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        versions = conn.execute(
            "SELECT * FROM versions WHERE project_id=? ORDER BY version_no DESC", (project_id,)
        ).fetchall()
    project = dict(row)
    project["options"] = json.loads(project.pop("options_json"))
    project["versions"] = [deserialize_version(v) for v in versions]
    return project


def deserialize_version(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ("script_json", "tpm_json", "quality_json"):
        result[key.removesuffix("_json")] = json.loads(result.pop(key))
    return result


def create_project(title: str, chapter_title: str, source_text: str, options: dict) -> str:
    project_id = "prj_" + uuid.uuid4().hex
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, title, chapter_title, source_text, json.dumps(options, ensure_ascii=False), now, now),
        )
    return project_id


def add_version(project_id: str, script_text: str, script: dict, tpm: dict, quality: dict) -> str:
    version_id = "ver_" + uuid.uuid4().hex
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version_no), 0)+1 n FROM versions WHERE project_id=?", (project_id,)).fetchone()
        conn.execute(
            "INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (version_id, project_id, row["n"], script_text, json.dumps(script, ensure_ascii=False),
             json.dumps(tpm, ensure_ascii=False), json.dumps(quality, ensure_ascii=False), _now()),
        )
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
    return version_id


def get_version(version_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
    return deserialize_version(row) if row else None


def delete_project(project_id: str) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM versions WHERE project_id=?", (project_id,))
        cur = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    return cur.rowcount > 0

