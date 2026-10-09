"""Non-régression : purger FreshRSS ne laisse pas de sous-dossier orphelin.

La purge supprimait le dossier FreshRSS sans remonter ses sous-dossiers à la
racine, contrairement à la suppression d'un dossier. Ils pointaient vers un
parent disparu : l'API mobile, qui ne partait que des racines, les omettait
avec leurs liens.
"""
import asyncio

from app.models import Folder, FreshRSSConfig, Link, User
from app.routes import api as api_routes
from app.routes import settings as settings_routes


def test_purge_remonte_les_sous_dossiers(session):
    user = User(oidc_sub="pf", public_slug="pf")
    session.add(user)
    session.commit()
    fr_folder = Folder(user_id=user.id, name="FreshRSS")
    session.add(fr_folder)
    session.flush()
    sub = Folder(user_id=user.id, name="Gardé", parent_id=fr_folder.id)
    session.add(sub)
    session.flush()
    session.add(FreshRSSConfig(user_id=user.id, freshrss_url="https://rss.example",
                               folder_id=fr_folder.id))
    session.add(Link(user_id=user.id, url="https://imported.example", folder_id=fr_folder.id))
    session.add(Link(user_id=user.id, url="https://kept.example", folder_id=sub.id))
    session.commit()
    sub_id = sub.id

    asyncio.run(settings_routes.purge_freshrss(user=user, session=session))

    session.expire_all()
    assert session.get(Folder, sub_id).parent_id is None
    folders = asyncio.run(api_routes.api_list_folders(user=user, session=session))["folders"]
    assert [(f["name"], f["depth"], f["count"]) for f in folders] == [("Gardé", 0, 1)]


def test_api_dossiers_rattache_un_orphelin_a_la_racine(session):
    """Base déjà abîmée par l'ancienne purge : l'API montre quand même le dossier."""
    user = User(oidc_sub="po", public_slug="po")
    session.add(user)
    session.commit()
    session.add(Folder(user_id=user.id, name="Orphelin", parent_id=999))
    session.add(Folder(user_id=user.id, name="Racine"))
    session.commit()

    folders = asyncio.run(api_routes.api_list_folders(user=user, session=session))["folders"]
    assert sorted(f["name"] for f in folders) == ["Orphelin", "Racine"]
