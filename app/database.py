import sqlite3
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import create_engine, Session, SQLModel

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record):
    if isinstance(dbapi_conn, sqlite3.Connection):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


_FTS_SETUP = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS fts_links USING fts5(
        link_id UNINDEXED,
        title,
        description,
        note,
        url,
        tags,
        content='',
        tokenize='unicode61 remove_diacritics 1'
    )""",
    """CREATE TRIGGER IF NOT EXISTS links_ai AFTER INSERT ON links BEGIN
        INSERT INTO fts_links(link_id, title, description, note, url, tags)
        VALUES (new.id, new.title, new.description, new.note, new.url, '');
    END""",
    """CREATE TRIGGER IF NOT EXISTS links_au AFTER UPDATE ON links BEGIN
        DELETE FROM fts_links WHERE link_id = old.id;
        INSERT INTO fts_links(link_id, title, description, note, url, tags)
        VALUES (new.id, new.title, new.description, new.note, new.url, '');
    END""",
    """CREATE TRIGGER IF NOT EXISTS links_ad AFTER DELETE ON links BEGIN
        DELETE FROM fts_links WHERE link_id = old.id;
    END""",
]


def init_db():
    SQLModel.metadata.create_all(engine)
    db_path = settings.database_url.removeprefix("sqlite:///")
    con = sqlite3.connect(db_path)
    for stmt in _FTS_SETUP:
        con.execute(stmt)
    # Migrations idempotentes
    gcols = [r[1] for r in con.execute("PRAGMA table_info(groups)").fetchall()]
    if "parent_id" not in gcols:
        con.execute("ALTER TABLE groups ADD COLUMN parent_id INTEGER DEFAULT NULL")
    ucols = [r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()]
    if "is_admin" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "is_active" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    if "session_version" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
    lcols = [r[1] for r in con.execute("PRAGMA table_info(links)").fetchall()]
    if "thumbnail_url" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN thumbnail_url TEXT NOT NULL DEFAULT ''")
    con.commit()
    con.close()


def refresh_link_fts(conn, link_id: int, title: str, description: str, note: str, url: str, tags: str):
    """Call after tag changes to keep fts_links.tags in sync."""
    conn.execute(text("DELETE FROM fts_links WHERE link_id = :id"), {"id": link_id})
    conn.execute(
        text("INSERT INTO fts_links(link_id, title, description, note, url, tags) VALUES (:lid, :t, :d, :n, :u, :tg)"),
        {"lid": link_id, "t": title, "d": description, "n": note, "u": url, "tg": tags},
    )


def get_session():
    with Session(engine) as session:
        yield session
