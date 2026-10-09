"""Non-régression : un seul chemin d'enrichissement, et il plafonne la description.

Le bloc « compléter les champs vides » existait en quatre copies (création,
import, synchro FreshRSS, rafraîchissement en masse). Trois d'entre elles
stockaient `og:description` tel quel : une page dont la balise pèse 1 Mo
l'écrivait entière en base.
"""
import asyncio

from app.models import Link, User
from app.routes.links import enrichment
from app.routes.links.constants import MAX_DESC_LEN


def test_enrichissement_en_serie_plafonne_la_description(session, engine, monkeypatch):
    user = User(oidc_sub="en", public_slug="en")
    session.add(user)
    session.commit()
    link = Link(user_id=user.id, url="https://big.example", title="https://big.example")
    session.add(link)
    session.commit()

    async def _meta(url):
        return {"title": "Grosse page", "description": "x" * 100_000,
                "favicon_url": "https://big.example/favicon.ico", "thumbnail_url": ""}

    monkeypatch.setattr(enrichment, "db_engine", engine)
    monkeypatch.setattr(enrichment, "_fetch_meta", _meta)
    asyncio.run(enrichment.refresh_links_meta([(link.id, link.url)]))

    session.expire_all()
    stored = session.get(Link, link.id)
    assert len(stored.description) == MAX_DESC_LEN
    assert stored.title == "Grosse page"
    assert stored.favicon_url == "https://big.example/favicon.ico"


def test_un_echec_n_interrompt_pas_la_serie(session, engine, monkeypatch):
    user = User(oidc_sub="en2", public_slug="en2")
    session.add(user)
    session.commit()
    links = [Link(user_id=user.id, url=f"https://{n}.example") for n in ("ko", "ok")]
    session.add_all(links)
    session.commit()

    async def _meta(url):
        if "ko" in url:
            raise RuntimeError("panne")
        return {"description": "Bonne description"}

    monkeypatch.setattr(enrichment, "db_engine", engine)
    monkeypatch.setattr(enrichment, "_fetch_meta", _meta)
    asyncio.run(enrichment.refresh_links_meta([(lk.id, lk.url) for lk in links]))

    session.expire_all()
    assert session.get(Link, links[1].id).description == "Bonne description"
