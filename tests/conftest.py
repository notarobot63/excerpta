"""Fixtures de test : DB SQLite temporaire avec schéma + FTS5.

On ne monte pas FastAPI (l'auth OIDC rendrait les tests fragiles) : on teste
la logique métier directement sur une session SQLModel, ce qui suffit à couvrir
les non-régressions FTS / multi-tenant.
"""
import os

# Doit précéder tout import de `app.*` : Settings est instancié à l'import de
# app.config, et c'est ce flag qui autorise le host "testserver" du TestClient
# (retiré des hôtes acceptés en production).
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test" * 16)

import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401 - enregistre les tables sur SQLModel.metadata

# Schéma FTS5 + déclencheurs, importés depuis le module que `app.database`
# applique en production. `app.fts_schema` est sans dépendance, ce qui préserve
# la raison d'être de l'ancienne copie (ne pas tirer crypto → cryptography) tout
# en supprimant la dérive possible entre les deux définitions.
from app.fts_schema import FTS_SETUP as _FTS_SETUP


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
