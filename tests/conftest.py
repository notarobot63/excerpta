"""Fixtures de test : DB SQLite temporaire avec schéma + FTS5.

On ne monte pas FastAPI (l'auth OIDC rendrait les tests fragiles) : on teste
la logique métier directement sur une session SQLModel, ce qui suffit à couvrir
les non-régressions FTS / multi-tenant.
"""
import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401 - enregistre les tables sur SQLModel.metadata

# Schéma FTS5 + triggers, identique à app.database._FTS_SETUP. Dupliqué ici
# pour découpler les tests de la cascade d'imports lourds de app.database
# (crypto → cryptography). test_fts_uses_rowid_not_link_id garde le schéma honnête.
_FTS_SETUP = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS fts_links USING fts5(
        title, description, note, url, tags,
        tokenize='unicode61 remove_diacritics 1'
    )""",
    """CREATE TRIGGER IF NOT EXISTS links_ai AFTER INSERT ON links BEGIN
        INSERT INTO fts_links(rowid, title, description, note, url, tags)
        VALUES (new.id, new.title, new.description, new.note, new.url, '');
    END""",
    """CREATE TRIGGER IF NOT EXISTS links_au AFTER UPDATE ON links BEGIN
        DELETE FROM fts_links WHERE rowid = old.id;
        INSERT INTO fts_links(rowid, title, description, note, url, tags)
        VALUES (new.id, new.title, new.description, new.note, new.url, '');
    END""",
    """CREATE TRIGGER IF NOT EXISTS links_ad AFTER DELETE ON links BEGIN
        DELETE FROM fts_links WHERE rowid = old.id;
    END""",
]


@pytest.fixture
def engine(tmp_path):
    db_file = tmp_path / "test.db"
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _record):
        if isinstance(dbapi_conn, sqlite3.Connection):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    SQLModel.metadata.create_all(eng)
    # Triggers + table FTS via sqlite3 brut (comme init_db)
    con = sqlite3.connect(str(db_file))
    for stmt in _FTS_SETUP:
        con.execute(stmt)
    con.commit()
    con.close()
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
