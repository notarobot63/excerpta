"""Non-régression sur les redirections pilotées par un paramètre de formulaire.

`return_to` était validé par un simple `startswith("/")`, qui laisse passer
`//evil.example` (le navigateur y lit un hôte) et `/\\evil.example`. La règle
correcte vit dans `app.utils.safe_next` ; ces tests vérifient qu'elle est
réellement appliquée par les routes, et qu'aucune n'en réintroduit une variante
maison.
"""
import asyncio
from pathlib import Path

import pytest

from app.models import Link, User
from app.routes.links import crud as crud_mod
from app.utils import safe_next

_EXTERNES = ["//evil.example", "/\\evil.example", "https://evil.example",
             "http://evil.example/x", ""]


class _FakeForm(dict):
    def getlist(self, key):
        value = self.get(key)
        return value if isinstance(value, list) else ([value] if value else [])


class _FakeRequest:
    def __init__(self, form=None, headers=None):
        self._form = _FakeForm(form or {})
        self.headers = headers or {}

    async def form(self):
        return self._form


def _make_user(session, sub="u1"):
    user = User(oidc_sub=sub, email=f"{sub}@e.com", public_slug=sub)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_link(session, user):
    link = Link(user_id=user.id, url="https://exemple.test/a")
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


@pytest.mark.parametrize("cible", _EXTERNES)
def test_delete_link_ne_redirige_pas_hors_du_site(session, cible):
    user = _make_user(session)
    link = _make_link(session, user)
    resp = asyncio.run(crud_mod.delete_link(
        link_id=link.id, request=_FakeRequest(), user=user, session=session,
        unstar_freshrss="", return_to=cible,
    ))
    assert resp.headers["location"] == "/"


@pytest.mark.parametrize("cible", _EXTERNES)
def test_bulk_delete_ne_redirige_pas_hors_du_site(session, cible):
    user = _make_user(session)
    link = _make_link(session, user)
    req = _FakeRequest(form={"link_ids": [str(link.id)], "return_to": cible})
    resp = asyncio.run(crud_mod.bulk_delete_links(request=req, user=user, session=session))
    assert resp.headers["location"] == "/"


def test_un_chemin_interne_est_preserve(session):
    user = _make_user(session)
    link = _make_link(session, user)
    resp = asyncio.run(crud_mod.delete_link(
        link_id=link.id, request=_FakeRequest(), user=user, session=session,
        unstar_freshrss="", return_to="/?page=2&tag=lecture",
    ))
    assert resp.headers["location"] == "/?page=2&tag=lecture"


def test_aucune_route_ne_revalide_le_chemin_a_la_main():
    """La forme `x if x.startswith("/") else "/"` est précisément le contrôle
    insuffisant corrigé ici : elle ne doit pas réapparaître ailleurs."""
    fautifs = [
        str(f) for f in Path("app").rglob("*.py")
        if 'startswith("/") else' in f.read_text()
    ]
    assert fautifs == [], f"validation de redirection maison, utiliser safe_next : {fautifs}"


@pytest.mark.parametrize("raw,attendu", [
    ("//evil.example", "/"),
    ("/\\evil.example", "/"),
    ("https://evil.example", "/"),
    (None, "/"),
    ("/settings", "/settings"),
])
def test_safe_next(raw, attendu):
    assert safe_next(raw) == attendu
