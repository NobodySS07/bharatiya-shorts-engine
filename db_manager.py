"""SQLite storage, hot backups and NumPy cosine-similarity dedup."""
import glob
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import log

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_name TEXT NOT NULL,
    story_title TEXT NOT NULL UNIQUE,
    story_summary TEXT,
    script_tone TEXT,
    source_tradition TEXT,
    source_type TEXT CHECK (source_type IN ('seed','ai')),
    embedding TEXT,
    embedding_model TEXT,
    script_json TEXT,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | scripted | media_done | rendered | failed | rejected
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    output_dir TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
# columns added after v1 (for DBs created by an earlier version)
MIGRATIONS = {
    "source_tradition": "TEXT", "embedding_model": "TEXT", "attempts": "INTEGER NOT NULL DEFAULT 0",
    "last_error": "TEXT", "updated_at": "TIMESTAMP",
}


@contextmanager
def connect():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(stories)")}
        for col, typ in MIGRATIONS.items():
            if col not in cols:
                c.execute(f"ALTER TABLE stories ADD COLUMN {col} {typ}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON stories(status)")
        # guard: never mix embedding models (vectors would be incomparable)
        models = {r[0] for r in c.execute(
            "SELECT DISTINCT embedding_model FROM stories WHERE embedding_model IS NOT NULL")}
        if models and models != {config.EMBEDDING_MODEL}:
            raise SystemExit(f"DB embeddings were made with {models}, config uses "
                             f"{config.EMBEDDING_MODEL}. Re-embed or revert config.")


def backup_database(reason: str = "") -> Optional[str]:
    if not os.path.exists(config.DB_PATH):
        return None
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(config.BACKUP_DIR, f"stories_{stamp}.db")
    try:
        src = sqlite3.connect(config.DB_PATH)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
    except Exception as e:                       # a failed backup must not kill the run
        log.warning(f"DB backup failed ({reason}): {e}")
        return None
    backups = sorted(glob.glob(os.path.join(config.BACKUP_DIR, "stories_*.db")))
    for old in backups[:-config.MAX_DB_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest


# ------------------------------------------------------------------ similarity
def _all_embeddings() -> Tuple[List[str], np.ndarray]:
    with connect() as c:
        rows = c.execute("SELECT story_title, embedding FROM stories WHERE embedding IS NOT NULL").fetchall()
    if not rows:
        return [], np.empty((0, 0))
    titles = [r["story_title"] for r in rows]
    mat = np.array([json.loads(r["embedding"]) for r in rows], dtype=np.float32)
    return titles, mat


def check_similarity(new_embedding: List[float]) -> Tuple[bool, float, Optional[str]]:
    """Returns (is_unique, max_similarity, closest_title)."""
    titles, mat = _all_embeddings()
    if not titles:
        return True, 0.0, None
    v = np.asarray(new_embedding, dtype=np.float32)
    if mat.shape[1] != v.shape[0]:
        raise ValueError(f"embedding dim mismatch: db={mat.shape[1]} new={v.shape[0]}")
    sims = (mat @ v) / (np.linalg.norm(mat, axis=1) * np.linalg.norm(v) + 1e-9)
    i = int(np.argmax(sims))
    max_sim = float(sims[i])
    return max_sim < config.SIMILARITY_THRESHOLD, max_sim, titles[i]


# ------------------------------------------------------------------ CRUD
def title_exists(title: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM stories WHERE lower(story_title)=lower(?)",
                         (title.strip(),)).fetchone() is not None


def insert_story(topic: Dict[str, Any], embedding: List[float], source_type: str) -> Dict[str, Any]:
    backup_database("insert")
    with connect() as c:
        cur = c.execute(
            """INSERT INTO stories (character_name, story_title, story_summary, script_tone,
               source_tradition, source_type, embedding, embedding_model, status)
               VALUES (?,?,?,?,?,?,?,?,'queued')""",
            (topic["character_name"], topic["story_title"].strip(), topic.get("story_summary", ""),
             topic.get("script_tone", ""), topic.get("source_tradition", ""), source_type,
             json.dumps(embedding), config.EMBEDDING_MODEL))
        return get_story(cur.lastrowid, conn=c)


def get_story(story_id: int, conn=None) -> Dict[str, Any]:
    if conn is not None:
        return dict(conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone())
    with connect() as c:
        return dict(c.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone())


def update_story(story_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE stories SET {sets} WHERE id=?", (*fields.values(), story_id))


def record_failure(story_id: int, error: str) -> str:
    """Increments attempts; parks the story as 'failed' after MAX_VIDEO_ATTEMPTS."""
    with connect() as c:
        row = c.execute("SELECT attempts, status FROM stories WHERE id=?", (story_id,)).fetchone()
        attempts = (row["attempts"] or 0) + 1
        status = "failed" if attempts >= config.MAX_VIDEO_ATTEMPTS else row["status"]
        c.execute("UPDATE stories SET attempts=?, last_error=?, status=?, updated_at=? WHERE id=?",
                  (attempts, error[:2000], status, datetime.now().isoformat(timespec="seconds"), story_id))
    return status


def get_unfinished() -> Optional[Dict[str, Any]]:
    with connect() as c:
        row = c.execute(
            """SELECT * FROM stories WHERE status IN ('queued','scripted','media_done')
               AND attempts < ? ORDER BY id LIMIT 1""", (config.MAX_VIDEO_ATTEMPTS,)).fetchone()
    return dict(row) if row else None


def recent_titles(limit: int = 150) -> List[str]:
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT character_name || ' — ' || story_title FROM stories ORDER BY id DESC LIMIT ?", (limit,))]
