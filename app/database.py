import sqlite3
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import create_engine, Session, SQLModel

from .config import settings
from .crypto import encrypt, hmac_key, is_encrypted

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
    # Migration groups → folders (ancienne DB)
    # Note : create_all() crée `folders` avant ce code, donc on détecte via la présence de `groups`
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "groups" in tables:
        # Migrer chaque groupe vers un dossier correspondant (même nom+user) ou en créer un
        for gid, gname, guid, gparent in con.execute(
            "SELECT id, name, user_id, parent_id FROM groups"
        ).fetchall():
            existing = con.execute(
                "SELECT id FROM folders WHERE name=? AND user_id=?", (gname, guid)
            ).fetchone()
            if existing:
                fid = existing[0]
            else:
                con.execute(
                    "INSERT INTO folders(name, user_id, parent_id, is_public, sort_order) VALUES(?,?,?,0,0)",
                    (gname, guid, gparent),
                )
                fid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            if "link_groups" in tables:
                con.execute(
                    "UPDATE links SET folder_id=? WHERE id IN "
                    "(SELECT link_id FROM link_groups WHERE group_id=?)",
                    (fid, gid),
                )
        if "link_groups" in tables:
            con.execute("DROP TABLE link_groups")
        con.execute("DROP TABLE groups")
    # Migrations additionnelles sur folders
    if "folders" in tables or "groups" in tables:
        fcols = [r[1] for r in con.execute("PRAGMA table_info(folders)").fetchall()]
        if "sort_order" not in fcols:
            con.execute("ALTER TABLE folders ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        lcols_f = [r[1] for r in con.execute("PRAGMA table_info(links)").fetchall()]
        if "folder_id" not in lcols_f:
            con.execute("ALTER TABLE links ADD COLUMN folder_id INTEGER REFERENCES folders(id)")

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

    # Chiffrement api_key + ajout api_key_hmac
    ucols = [r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()]
    if "api_key_hmac" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN api_key_hmac TEXT")
    rows = con.execute("SELECT id, api_key FROM users WHERE api_key_hmac IS NULL").fetchall()
    for row_id, api_key in rows:
        if api_key is None:
            continue
        if not is_encrypted(api_key):
            enc = encrypt(api_key)
            hm = hmac_key(api_key)
            con.execute("UPDATE users SET api_key = ?, api_key_hmac = ? WHERE id = ?",
                        (enc, hm, row_id))
        else:
            from .crypto import decrypt
            hm = hmac_key(decrypt(api_key))
            con.execute("UPDATE users SET api_key_hmac = ? WHERE id = ?", (hm, row_id))

    # Chiffrement freshrss_token
    if con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='freshrss_configs'"
    ).fetchone():
        for row_id, token in con.execute(
            "SELECT id, freshrss_token FROM freshrss_configs"
        ).fetchall():
            if token and not is_encrypted(token):
                con.execute(
                    "UPDATE freshrss_configs SET freshrss_token = ? WHERE id = ?",
                    (encrypt(token), row_id),
                )

    # Index de performance
    con.execute("CREATE INDEX IF NOT EXISTS idx_links_user_created ON links(user_id, created_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_link_tags_tag_id ON link_tags(tag_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_links_folder_id ON links(folder_id)")

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
