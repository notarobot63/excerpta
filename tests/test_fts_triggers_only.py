"""L'index FTS suit les suppressions et les imports par ses seuls déclencheurs.

Plusieurs routes le vidaient ou le remplissaient à la main en plus des
déclencheurs `links_ai` / `links_ad`. Ces écritures en double sont retirées :
ces tests vérifient que l'index reste exact sans elles.
"""
import asyncio

from sqlalchemy import text

from app.models import Folder, FreshRSSConfig, Link, User
from app.routes import admin
from app.routes import settings as settings_routes
from tests.test_freshrss_folder import _item, _sync, env  # noqa: F401 (fixture)


def _fts_rows(session, user_id):
    return session.execute(text(
        "SELECT COUNT(*) FROM fts_links WHERE rowid IN (SELECT id FROM links WHERE user_id = :u)"
        " OR rowid NOT IN (SELECT id FROM links)"), {"u": user_id}).scalar()


def _user_with_links(session, sub):
    user = User(oidc_sub=sub, public_slug=sub)
    session.add(user)
    session.commit()
    folder = Folder(user_id=user.id, name="FreshRSS")
    session.add(folder)
    session.flush()
    session.add_all([Link(user_id=user.id, url=f"https://{sub}{n}.example", folder_id=folder.id)
                     for n in range(3)])
    session.add(FreshRSSConfig(user_id=user.id, folder_id=folder.id))
    session.commit()
    return user


def test_purge_tout_vide_l_index(session):
    user = _user_with_links(session, "pa")
    asyncio.run(settings_routes.purge_all(user=user, session=session))
    assert _fts_rows(session, user.id) == 0


def test_purge_freshrss_vide_l_index(session):
    user = _user_with_links(session, "pf")
    asyncio.run(settings_routes.purge_freshrss(user=user, session=session))
    assert _fts_rows(session, user.id) == 0


def test_suppression_de_compte_vide_l_index(session):
    target = _user_with_links(session, "del")
    boss = User(oidc_sub="boss", public_slug="boss", is_admin=True)
    session.add(boss)
    session.commit()
    target_id = target.id
    asyncio.run(admin.delete_user(uid=target_id, admin=boss, session=session))
    assert _fts_rows(session, target_id) == 0


def test_lien_importe_par_freshrss_est_cherchable(session, env):  # noqa: F811
    user, config, state = env
    state["starred"] = [{**_item(7), "title": "Zanzibar"}]
    _sync(session, config)
    hits = session.execute(text("SELECT rowid FROM fts_links WHERE fts_links MATCH 'zanzibar'")).fetchall()
    assert len(hits) == 1
