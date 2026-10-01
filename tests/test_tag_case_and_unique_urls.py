"""Non-régression : étiquettes sensibles à la casse et URL en double.

1. Les étiquettes antérieures à la normalisation (2026-09-25) gardaient leur
   casse : « Python » existant, le lien suivant tagué Python créait une seconde
   étiquette « python ». `init_db` les fusionne et pose l'unicité.
2. Éditer un lien vers une URL déjà enregistrée créait un doublon ; deux ajouts
   simultanés aussi. L'URL est désormais unique par compte.
"""
import asyncio
import sqlite3

import pytest
from fastapi import BackgroundTasks
from sqlmodel import SQLModel, create_engine, select

from app import database
from app.models import Link, LinkTagLink, Tag, User
from app.routes.links import crud as crud_mod


def _legacy_db(tmp_path, monkeypatch):
    """Base au schéma courant mais sans les index uniques, comme en production."""
    db_file = tmp_path / "legacy.db"
    url = f"sqlite:///{db_file}"
    eng = create_engine(url)
    SQLModel.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db_file)
    con.execute("DROP INDEX ux_tags_user_name")
    con.execute("DROP INDEX ux_links_user_url")
    con.commit()
    monkeypatch.setattr(database.settings, "database_url", url)
    monkeypatch.setattr(database, "engine", create_engine(url))
    return db_file, con


def _indexes(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def test_migration_fusionne_les_etiquettes_de_casse_differente(tmp_path, monkeypatch):
    db_file, con = _legacy_db(tmp_path, monkeypatch)
    con.execute("INSERT INTO users (id, oidc_sub, email, name, api_key, api_key_hmac, theme,"
                " is_admin, is_active, public_page_title, tags_enabled, folders_enabled,"
                " session_version, created_at) VALUES"
                " (1,'a','','','k','','light',0,1,'',1,1,0,'2026-01-01')")
    con.executemany("INSERT INTO tags (id, user_id, name) VALUES (?, 1, ?)",
                    [(1, "Python"), (2, "python"), (3, " PYTHON "), (4, "Été"), (5, "web")])
    con.executemany(
        "INSERT INTO links (id, user_id, url, title, description, favicon_url, thumbnail_url,"
        " note, is_public, reader_failed, created_at, updated_at)"
        " VALUES (?, 1, ?, 't', '', '', '', '', 0, 0, '2026-01-01', '2026-01-01')",
        [(1, "https://a.example"), (2, "https://b.example")],
    )
    # Le lien 1 porte deux variantes : il ne doit garder qu'une association.
    con.executemany("INSERT INTO link_tags (link_id, tag_id) VALUES (?, ?)",
                    [(1, 1), (1, 2), (2, 3), (2, 4), (2, 5)])
    con.commit()
    con.close()

    database.init_db()

    con = sqlite3.connect(db_file)
    names = sorted(r[0] for r in con.execute("SELECT name FROM tags"))
    assert names == ["python", "web", "été"]
    py_id = con.execute("SELECT id FROM tags WHERE name='python'").fetchone()[0]
    assert py_id == 2  # l'étiquette déjà normalisée est conservée
    assert sorted(con.execute("SELECT link_id FROM link_tags WHERE tag_id=?", (py_id,))) == [(1,), (2,)]
    fts = dict(con.execute("SELECT rowid, tags FROM fts_links"))
    assert "Python" not in fts[1] and "python" in fts[1]
    assert "ux_tags_user_name" in _indexes(con)
    assert "ux_links_user_url" in _indexes(con)
    con.close()

    database.init_db()  # idempotent


def test_migration_avertit_sans_planter_si_des_urls_sont_en_double(tmp_path, monkeypatch, caplog):
    db_file, con = _legacy_db(tmp_path, monkeypatch)
    con.execute("INSERT INTO users (id, oidc_sub, email, name, api_key, api_key_hmac, theme,"
                " is_admin, is_active, public_page_title, tags_enabled, folders_enabled,"
                " session_version, created_at) VALUES"
                " (1,'a','','','k','','light',0,1,'',1,1,0,'2026-01-01')")
    con.executemany(
        "INSERT INTO links (id, user_id, url, title, description, favicon_url, thumbnail_url,"
        " note, is_public, reader_failed, created_at, updated_at)"
        " VALUES (?, 1, 'https://dup.example', 't', '', '', '', '', 0, 0, '2026-01-01', '2026-01-01')",
        [(1,), (2,)],
    )
    con.commit()
    con.close()

    database.init_db()

    con = sqlite3.connect(db_file)
    assert "ux_links_user_url" not in _indexes(con)
    con.close()
    assert "ux_links_user_url" in caplog.text


def _user(session):
    user = User(oidc_sub="u", public_slug="u")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_editer_vers_une_url_existante_redirige_vers_le_lien_existant(session):
    user = _user(session)
    a = Link(user_id=user.id, url="https://a.example", title="A")
    b = Link(user_id=user.id, url="https://b.example", title="B")
    session.add(a)
    session.add(b)
    session.commit()

    resp = asyncio.run(crud_mod.edit_link(
        request=None, link_id=a.id, background_tasks=BackgroundTasks(),
        url="https://b.example", title="A modifié", description="", note="",
        is_public=None, tags="", folder_id=None, user=user, session=session,
    ))
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/links/{b.id}/edit?duplicate=1"
    session.expire_all()
    assert session.get(Link, a.id).url == "https://a.example"
    assert len(session.exec(select(Link).where(Link.url == "https://b.example")).all()) == 1


def test_ajout_concurrent_de_la_meme_url_rejoint_le_premier(session, engine):
    """Le second ajout perd la course sur l'index unique et renvoie l'existant."""
    from sqlmodel import Session

    user = _user(session)
    # Le contrôle préalable de create_link ne voit rien : on simule l'autre
    # requête qui insère entre ce contrôle et le flush.
    real_exec = session.exec
    state = {"done": False}

    def exec_then_race(stmt, *a, **kw):
        result = real_exec(stmt, *a, **kw)
        if not state["done"]:
            state["done"] = True
            with Session(engine) as other:
                other.add(Link(user_id=user.id, url="https://race.example", title="1er"))
                other.commit()
        return result

    session.exec = exec_then_race
    link, created = crud_mod.create_link(
        session, BackgroundTasks(), user_id=user.id, url="https://race.example",
    )
    session.exec = real_exec
    assert created is False
    assert link.title == "1er"
