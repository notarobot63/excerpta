"""Recherche plein texte : tri par pertinence, pagination et filtres combinés.

La liste chargeait toutes les correspondances complètes avant d'en garder une
page. Le correctif ne lit plus que leurs identifiants : ces tests fixent ce
que l'utilisateur doit continuer à voir.
"""
import asyncio

from app.models import Link, LinkTagLink, Tag, User
from app.routes.links import crud
from app.routes.links.constants import PER_PAGE


def _list(session, user, monkeypatch, **params):
    captured = {}

    def _capture(request, template, context, *args, **kwargs):
        captured.update(context)
        return None

    monkeypatch.setattr(crud.templates, "TemplateResponse", _capture)
    monkeypatch.setattr(crud, "sidebar_data", lambda *a: {})

    class _Req:
        query_params = {}
        headers = {}

    asyncio.run(crud.list_links(request=_Req(), q=params.get("q"), tag=params.get("tag"),
                                group_id=None, page=params.get("page", 1),
                                user=user, session=session))
    return captured


def _seed(session):
    user = User(oidc_sub="sp", public_slug="sp")
    session.add(user)
    session.commit()
    # Le titre pèse plus que l'URL dans le classement bm25.
    links = [Link(user_id=user.id, url=f"https://site{n}.example/rust", title=f"Page {n}")
             for n in range(PER_PAGE + 5)]
    links.append(Link(user_id=user.id, url="https://top.example", title="Rust Rust Rust"))
    session.add_all(links)
    session.commit()
    return user, links


def test_pertinence_puis_pagination(session, monkeypatch):
    user, links = _seed(session)
    first = _list(session, user, monkeypatch, q="rust")
    assert first["total"] == PER_PAGE + 6
    assert len(first["links"]) == PER_PAGE
    assert first["links"][0].url == "https://top.example", "le titre doit primer sur l'URL"

    second = _list(session, user, monkeypatch, q="rust", page=2)
    assert len(second["links"]) == 6
    seen = {lk.id for lk in first["links"]} | {lk.id for lk in second["links"]}
    assert len(seen) == PER_PAGE + 6, "aucun lien en double ni oublié entre les pages"


def test_recherche_combinee_a_une_etiquette(session, monkeypatch):
    user, links = _seed(session)
    tag = Tag(user_id=user.id, name="garde")
    session.add(tag)
    session.flush()
    session.add(LinkTagLink(link_id=links[3].id, tag_id=tag.id))
    session.commit()

    out = _list(session, user, monkeypatch, q="rust", tag="garde")
    assert [lk.id for lk in out["links"]] == [links[3].id]
    assert out["total"] == 1


def test_seule_la_page_affichee_est_chargee(session, monkeypatch):
    """Une recherche large ne doit pas charger tous les liens correspondants."""
    from sqlalchemy import event

    user, _ = _seed(session)
    user_id = user.id
    session.expunge_all()
    user = session.get(User, user_id)
    loaded = []

    def _on_load(target, _ctx):
        loaded.append(target.id)

    event.listen(Link, "load", _on_load)
    try:
        _list(session, user, monkeypatch, q="rust")
    finally:
        event.remove(Link, "load", _on_load)
    assert len(loaded) == PER_PAGE
