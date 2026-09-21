"""Camada de acesso ao SQLite: conexao, schema e migracoes simples."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .paths import data_dir

DB_PATH = Path(os.environ.get("STUDYIA_DB", data_dir() / "studyia.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS deck (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS card (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES deck(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('quiz', 'flash')),
    question    TEXT NOT NULL,
    options     TEXT NOT NULL DEFAULT '[]',   -- JSON array (so quiz)
    answer      TEXT NOT NULL,                -- indice da correta (quiz) ou verso (flash)
    explanation TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',
    suspended   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_card_deck ON card(deck_id);

CREATE TABLE IF NOT EXISTS schedule (
    card_id      INTEGER PRIMARY KEY REFERENCES card(id) ON DELETE CASCADE,
    state        TEXT NOT NULL DEFAULT 'new',   -- new | learning | review | relearning
    step         INTEGER NOT NULL DEFAULT 0,
    ease         REAL NOT NULL DEFAULT 2.5,
    interval_min REAL NOT NULL DEFAULT 0,
    due_at       TEXT NOT NULL,
    reps         INTEGER NOT NULL DEFAULT 0,
    lapses       INTEGER NOT NULL DEFAULT 0,
    last_review  TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_due ON schedule(due_at);

CREATE TABLE IF NOT EXISTS session (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id    INTEGER REFERENCES deck(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    reviewed   INTEGER NOT NULL DEFAULT 0,
    correct    INTEGER NOT NULL DEFAULT 0,
    ms_total   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id        INTEGER NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    session_id     INTEGER REFERENCES session(id) ON DELETE SET NULL,
    reviewed_at    TEXT NOT NULL,
    grade          INTEGER NOT NULL,
    correct        INTEGER NOT NULL,
    answer_given   TEXT NOT NULL DEFAULT '',
    ms             INTEGER NOT NULL DEFAULT 0,
    interval_after REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_review_card ON review(card_id);
CREATE INDEX IF NOT EXISTS idx_review_when ON review(reviewed_at);

CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    """Timestamp UTC em ISO-8601, formato unico usado em todo o banco."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Ao apagar/alterar, o SQLite passa a sobrescrever o dado antigo com zeros em vez de
    # deixá-lo legível nas páginas livres do arquivo.
    conn.execute("PRAGMA secure_delete = ON")
    return conn


def depurar_arquivo() -> None:
    """Elimina do disco qualquer resto de dados apagados ou substituídos.

    Necessário depois de mexer em segredos: trocar um valor não apaga a versão antiga do
    arquivo — ela sobra em páginas livres e no log (WAL) até uma limpeza. Sem isto, uma
    chave de API migrada para a forma cifrada continuaria legível, em texto puro, dentro
    do próprio banco. Só reescreve o arquivo quando chamado (troca/remoção de chave).
    """
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)  # VACUUM não roda em transação
    try:
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO setting (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
