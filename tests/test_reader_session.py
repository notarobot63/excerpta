"""Non-régression : l'extraction de la vue lecteur ne retient pas de connexion DB.

La session restait ouverte pendant `_extract_reader` (jusqu'à 10 s de réseau
par saut) : une quinzaine d'ouvertures simultanées épuisait le pool
SQLAlchemy (5+10) et faisait attendre toutes les autres routes.
"""
import asyncio

from app.models import Link, User
from app.routes import api as api_mod
from app.routes.links import reader as reader_mod


def _setup(session):
    user = User(oidc_sub="r", public_slug="r")
    session.add(user)
    session.commit()
    link = Link(user_id=user.id, url="https://example.com/a", title="A")
    session.add(link)
    session.commit()
    session.refresh(link)
    return user, link


def _fake_extract(session, seen):
    async def _extract(url):
        seen.append(session.in_transaction())
        return {"title": "Titre", "html": "<p>corps</p>"}
    return _extract


def test_api_reader_libere_la_session_pendant_l_extraction(session, monkeypatch):
    user, link = _setup(session)
    seen = []
    monkeypatch.setattr(reader_mod, "_extract_reader", _fake_extract(session, seen))
    out = asyncio.run(api_mod.api_link_reader(link_id=link.id, user=user, session=session))
    assert seen == [False]
    assert out["reader_html"] == "<p>corps</p>"
    assert session.get(Link, link.id).reader_title == "Titre"


def test_lien_supprime_pendant_l_extraction_renvoie_404(session, monkeypatch):
    import pytest
    from fastapi import HTTPException

    user, link = _setup(session)

    async def _extract(url):
        with type(session)(session.get_bind()) as other:
            other.delete(other.get(Link, link.id))
            other.commit()
        return {"title": "T", "html": "<p>x</p>"}

    monkeypatch.setattr(reader_mod, "_extract_reader", _extract)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(reader_mod.extract_and_store_reader(session, link))
    assert exc.value.status_code == 404
