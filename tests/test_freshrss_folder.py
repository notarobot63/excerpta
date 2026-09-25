"""Non-régression : la synchro FreshRSS ne désétoile que ce qui a quitté son dossier.

Deux défauts, qui retiraient des étoiles dans FreshRSS sans rien signaler :

1. Le dossier d'import était retrouvé par son nom. Après un renommage (barre
   latérale, formulaire de dossier ou champ des réglages), la synchro créait un
   nouveau dossier et désétoilait tous les articles restés dans l'ancien.
2. Un lien enregistré à la main ailleurs, puis étoilé dans FreshRSS, recevait
   un freshrss_item_id par backfill et était désétoilé dans la même passe.
"""
import asyncio

import pytest
from sqlmodel import select

import app.routes.freshrss as fr
from app.models import Folder, FreshRSSConfig, Link, User
from app.routes import folders as folders_routes


def _item(i):
    return {"id": f"tag:{i}", "alternate": [{"href": f"https://a{i}.example"}]}


@pytest.fixture
def env(session, monkeypatch):
    user = User(oidc_sub="fr", public_slug="fr")
    session.add(user)
    session.commit()
    config = FreshRSSConfig(user_id=user.id, freshrss_url="https://rss.example",
                            group_name="FreshRSS", is_enabled=True)
    session.add(config)
    session.commit()

    state = {"starred": [], "unstarred": []}

    async def _ok(url):
        return None

    async def _auth(*args):
        return "AUTH"

    async def _starred(*args):
        return state["starred"]

    async def _unstar(cfg, ids):
        state["unstarred"].extend(ids)
        return 0

    monkeypatch.setattr(fr, "_assert_safe_freshrss_url", _ok)
    monkeypatch.setattr(fr, "_greader_auth", _auth)
    monkeypatch.setattr(fr, "_greader_starred", _starred)
    monkeypatch.setattr(fr, "unstar_items", _unstar)
    monkeypatch.setattr(fr, "spawn", lambda coro, name=None: coro.close())
    return user, config, state


def _sync(session, config):
    return asyncio.run(fr.sync_user(config, session))


def _import_three(session, config, state):
    state["starred"] = [_item(i) for i in range(3)]
    assert _sync(session, config) == 3
    return session.get(Folder, config.folder_id)


def test_renommer_le_dossier_ne_desetoile_rien(session, env):
    user, config, state = env
    folder = _import_three(session, config, state)

    folders_routes._rename(session, folder, "Veille")
    session.commit()
    _sync(session, config)

    assert state["unstarred"] == []
    names = [f.name for f in session.exec(select(Folder).where(Folder.user_id == user.id)).all()]
    assert names == ["Veille"], "la synchro a recréé un dossier au lieu de suivre le renommé"
    assert config.group_name == "Veille", "le champ des réglages doit suivre le renommage"


def test_changer_le_nom_dans_les_reglages_renomme_le_dossier(session, env):
    user, config, state = env
    _import_three(session, config, state)

    asyncio.run(fr.freshrss_settings_save(
        request=None, freshrss_url="https://rss.example", freshrss_user="",
        freshrss_token="", group_name="Lectures RSS", is_enabled="on",
        current_user=user, session=session,
    ))
    _sync(session, config)

    assert state["unstarred"] == []
    names = [f.name for f in session.exec(select(Folder).where(Folder.user_id == user.id)).all()]
    assert names == ["Lectures RSS"]


def test_lien_deja_enregistre_puis_etoile_garde_son_etoile(session, env):
    user, config, state = env
    other = Folder(user_id=user.id, name="Lectures")
    session.add(other)
    session.commit()
    session.add(Link(user_id=user.id, url="https://a9.example", folder_id=other.id))
    session.commit()

    state["starred"] = [_item(9)]
    _sync(session, config)
    _sync(session, config)

    assert state["unstarred"] == []
    link = session.exec(select(Link).where(Link.url == "https://a9.example")).one()
    assert link.freshrss_item_id is None, "un lien hors dossier ne doit pas être marqué comme importé"


def test_lien_importe_sorti_du_dossier_est_toujours_desetoile(session, env):
    """Le self-healing voulu reste en place pour un vrai import déplacé."""
    user, config, state = env
    _import_three(session, config, state)
    other = Folder(user_id=user.id, name="Archives")
    session.add(other)
    session.commit()
    moved = session.exec(select(Link).where(Link.url == "https://a1.example")).one()
    moved.folder_id = other.id
    session.add(moved)
    session.commit()

    _sync(session, config)
    assert state["unstarred"] == ["tag:1"]


def test_config_anterieure_adopte_le_dossier_par_son_nom(session, env):
    user, config, state = env
    legacy = Folder(user_id=user.id, name="FreshRSS")
    session.add(legacy)
    session.commit()
    session.add(Link(user_id=user.id, url="https://a0.example", folder_id=legacy.id))
    session.commit()
    assert config.folder_id is None

    state["starred"] = [_item(0)]
    _sync(session, config)

    assert config.folder_id == legacy.id
    link = session.exec(select(Link).where(Link.url == "https://a0.example")).one()
    assert link.freshrss_item_id == "tag:0", "le backfill des anciens imports doit subsister"
    assert state["unstarred"] == []


def test_supprimer_le_dossier_oublie_son_identifiant(session, env):
    """Un identifiant réattribué par SQLite désignerait un dossier sans rapport."""
    user, config, state = env
    folder = _import_three(session, config, state)
    asyncio.run(folders_routes.delete_folder(folder.id, delete_links="1", user=user, session=session))
    session.refresh(config)
    assert config.folder_id is None


def test_migration_ajoute_la_colonne_folder_id(tmp_path, monkeypatch):
    """Une base existante n'a pas la colonne : create_all n'ajoute pas de colonne."""
    import sqlite3

    from sqlmodel import SQLModel, create_engine

    from app import database

    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.execute(
        "CREATE TABLE freshrss_configs (id INTEGER PRIMARY KEY, user_id INTEGER,"
        " freshrss_url TEXT, freshrss_user TEXT, freshrss_token TEXT,"
        " group_name TEXT, is_enabled INTEGER, last_sync TEXT, synced_count INTEGER)"
    )
    con.commit()
    con.close()

    url = f"sqlite:///{db_file}"
    monkeypatch.setattr(database.settings, "database_url", url)
    monkeypatch.setattr(database, "engine", create_engine(url))
    database.init_db()

    con = sqlite3.connect(db_file)
    cols = {r[1] for r in con.execute("PRAGMA table_info(freshrss_configs)")}
    con.close()
    assert "folder_id" in cols


def test_deux_synchros_simultanees_n_inserent_pas_de_doublon(session, engine, env, monkeypatch):
    """La seconde passe attend la première au lieu d'insérer les mêmes liens.

    La fenêtre de course est le désétoilage des liens sortis du dossier : seul
    `await` entre la lecture des URL existantes et l'insertion des nouvelles.
    """
    from sqlmodel import Session

    user, config, state = env
    _import_three(session, config, state)
    other = Folder(user_id=user.id, name="Archives")
    session.add(other)
    session.commit()
    moved = session.exec(select(Link).where(Link.url == "https://a0.example")).one()
    moved.folder_id = other.id
    session.add(moved)
    session.commit()
    state["starred"] = [_item(i) for i in range(6)]

    async def _slow_unstar(cfg, ids):
        await asyncio.sleep(0.05)
        state["unstarred"].extend(ids)
        return 0

    monkeypatch.setattr(fr, "unstar_items", _slow_unstar)

    async def _both():
        # Deux requêtes, deux sessions : comme la boucle et « sync now ».
        with Session(engine) as s1, Session(engine) as s2:
            c1, c2 = s1.get(FreshRSSConfig, config.id), s2.get(FreshRSSConfig, config.id)
            return await asyncio.gather(fr.sync_user(c1, s1), fr.sync_user(c2, s2))

    assert sorted(asyncio.run(_both())) == [0, 3]
    session.expire_all()
    urls = [lk.url for lk in session.exec(select(Link).where(Link.user_id == user.id)).all()]
    assert len(urls) == len(set(urls)) == 6
    assert session.get(FreshRSSConfig, config.id).synced_count == 6


def test_panne_reseau_pendant_sync_now_donne_502(session, env, monkeypatch):
    import httpx
    from fastapi import HTTPException

    user, config, state = env

    async def _down(*args):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(fr, "_greader_auth", _down)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fr.freshrss_sync_now(request=None, current_user=user, session=session))
    assert exc.value.status_code == 502
    assert "ConnectError" in exc.value.detail
