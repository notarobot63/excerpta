import sqlite3
from sqlalchemy import event
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
        # Perf : sûr sous WAL. synchronous=NORMAL réduit les fsync, busy_timeout
        # évite les "database is locked", cache/mmap/temp accélèrent les lectures.
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA cache_size=-16000")   # ~16 Mo de page cache
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA mmap_size=134217728")  # 128 Mo I/O mappé
        cur.close()


_FTS_SETUP = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS fts_links USING fts5(
        title,
        description,
        note,
        url,
        tags,
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


def init_db():
    SQLModel.metadata.create_all(engine)
    db_path = settings.database_url.removeprefix("sqlite:///")
    con = sqlite3.connect(db_path)
    # Migration FTS : si l'ancienne table contentless (link_id UNINDEXED, content='')
    # est présente, la supprimer pour la recréer correctement avec rowid.
    fts_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='fts_links'"
    ).fetchone()
    if fts_sql and "content=''" in fts_sql[0]:
        con.execute("DROP TABLE IF EXISTS fts_links")
        for trigger in ("links_ai", "links_au", "links_ad"):
            con.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for stmt in _FTS_SETUP:
        con.execute(stmt)
    if fts_sql and "content=''" in fts_sql[0]:
        rows = con.execute("""
            SELECT l.id, l.title, l.description, l.note, l.url,
                   COALESCE(GROUP_CONCAT(t.name, ' '), '')
            FROM links l
            LEFT JOIN link_tags lt ON lt.link_id = l.id
            LEFT JOIN tags t ON t.id = lt.tag_id
            GROUP BY l.id
        """).fetchall()
        con.executemany(
            "INSERT INTO fts_links(rowid, title, description, note, url, tags) VALUES (?,?,?,?,?,?)",
            rows,
        )
    # Migrations idempotentes
    cols = {r[1] for r in con.execute("PRAGMA table_info(links)").fetchall()}
    if "freshrss_item_id" not in cols:
        con.execute("ALTER TABLE links ADD COLUMN freshrss_item_id TEXT")
    user_cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    if "public_page_title" not in user_cols:
        con.execute("ALTER TABLE users ADD COLUMN public_page_title TEXT NOT NULL DEFAULT 'Liens publics'")
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
    if "public_slug" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN public_slug TEXT")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_public_slug ON users(public_slug)")
    # Backfill des slugs manquants (multi-tenant page publique)
    from .utils import slugify
    missing = con.execute(
        "SELECT id, name FROM users WHERE public_slug IS NULL OR public_slug = ''"
    ).fetchall()
    if missing:
        taken = {
            r[0] for r in con.execute(
                "SELECT public_slug FROM users WHERE public_slug IS NOT NULL AND public_slug != ''"
            ).fetchall()
        }
        for uid, name in missing:
            base = slugify(name) or f"u{uid}"
            slug = base
            n = 0
            while slug in taken:
                n += 1
                slug = f"{base}-{uid}" if n == 1 else f"{base}-{uid}-{n}"
            con.execute("UPDATE users SET public_slug = ? WHERE id = ?", (slug, uid))
            taken.add(slug)
    lcols = [r[1] for r in con.execute("PRAGMA table_info(links)").fetchall()]
    if "thumbnail_url" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN thumbnail_url TEXT NOT NULL DEFAULT ''")
    if "is_broken" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN is_broken INTEGER")
    if "check_status" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN check_status INTEGER")
    if "last_checked_at" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN last_checked_at TEXT")
    if "reader_html" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN reader_html TEXT")
    if "reader_title" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN reader_title TEXT")
    if "reader_extracted_at" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN reader_extracted_at TEXT")
    if "reader_failed" not in lcols:
        con.execute("ALTER TABLE links ADD COLUMN reader_failed INTEGER NOT NULL DEFAULT 0")

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
    con.execute("CREATE INDEX IF NOT EXISTS idx_links_user_url ON links(user_id, url)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_links_user_created ON links(user_id, created_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_link_tags_tag_id ON link_tags(tag_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_links_folder_id ON links(folder_id)")

    con.commit()
    con.close()


def get_session():
    with Session(engine) as session:
        yield session


def cleanup_freshrss_tag() -> None:
    """Migration unique idempotente : retire le tag 'freshrss' (redondant avec
    le dossier FreshRSS) de tous les liens, supprime le tag, et rafraîchit le
    FTS des liens affectés avec leurs tags restants. Ne fait rien si absent."""
    from sqlmodel import select
    from sqlalchemy import text as _text
    from .models import Tag, Link, LinkTagLink
    from .utils import refresh_link_fts

    with Session(engine) as session:
        tags = list(session.exec(select(Tag).where(Tag.name == "freshrss")).all())
        if not tags:
            return
        tag_ids = [t.id for t in tags]
        ph = ",".join(str(i) for i in tag_ids)
        affected_ids = [
            r[0] for r in session.execute(
                _text(f"SELECT DISTINCT link_id FROM link_tags WHERE tag_id IN ({ph})")
            ).fetchall()
        ]
        session.execute(_text(f"DELETE FROM link_tags WHERE tag_id IN ({ph})"))
        for t in tags:
            session.delete(t)
        session.flush()
        for lid in affected_ids:
            link = session.get(Link, lid)
            if link:
                remaining = list(session.exec(
                    select(Tag).join(LinkTagLink, LinkTagLink.tag_id == Tag.id)
                    .where(LinkTagLink.link_id == lid)
                ).all())
                refresh_link_fts(session, link, remaining)
        session.commit()
