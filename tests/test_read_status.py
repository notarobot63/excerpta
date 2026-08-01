"""Non-régression sur le statut lu/non-lu (models.Link.read_at) :
- filtre ?unread=1 sur la liste (via TestClient, comme les autres tests de
  rendu de test_features.py) ;
- toggle_read et bulk_mark_read (appel direct des fonctions, comme le reste
  de la suite pour les mutations : le CSRF qui les protège en pratique est
  déjà couvert séparément par test_csrf.py).
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import Link, User
from app.routes.links import crud as crud_mod


@pytest.fixture
def client(engine):
    with Session(engine) as s:
        u = User(oidc_sub="reader", email="r@e.com", public_slug="reader")
        s.add(u)
        s.commit()
        uid = u.id
        s.add(Link(user_id=uid, url="https://example.com/lu", title="Déjà lu",
                    read_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        s.add(Link(user_id=uid, url="https://example.com/non-lu", title="Pas encore lu"))
        s.commit()

    def _get_session():
        with Session(engine) as s:
            yield s

    def _get_user():
        with Session(engine) as s:
            u = s.get(User, uid)
            s.expunge(u)
            return u

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[get_current_user] = _get_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_unread_filter_exclut_les_liens_lus(client):
    r = client.get("/?unread=1&partial=1")
    assert r.status_code == 200
    assert "Pas encore lu" in r.text
    assert "Déjà lu" not in r.text


def test_sans_filtre_les_deux_apparaissent(client):
    r = client.get("/?partial=1")
    assert r.status_code == 200
    assert "Pas encore lu" in r.text
    assert "Déjà lu" in r.text


# ── Mutations : appel direct (CSRF couvert séparément par test_csrf.py) ──────

def _make_user_and_link(session, read=False):
    user = User(oidc_sub="s1", email="s1@e.com", public_slug="s1")
    session.add(user)
    session.commit()
    link = Link(
        user_id=user.id, url="https://example.com/x",
        read_at=datetime.now(timezone.utc).replace(tzinfo=None) if read else None,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return user, link


def test_toggle_read_marque_puis_demarque(session):
    user, link = _make_user_and_link(session, read=False)
    result = asyncio.run(crud_mod.toggle_read(link.id, user=user, session=session))
    assert result == {"ok": True, "read": True}
    session.refresh(link)
    assert link.read_at is not None

    result2 = asyncio.run(crud_mod.toggle_read(link.id, user=user, session=session))
    assert result2 == {"ok": True, "read": False}
    session.refresh(link)
    assert link.read_at is None


def test_toggle_read_autre_utilisateur_404(session):
    user, link = _make_user_and_link(session, read=False)
    other = User(oidc_sub="s2", email="s2@e.com", public_slug="s2")
    session.add(other)
    session.commit()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(crud_mod.toggle_read(link.id, user=other, session=session))
    assert exc.value.status_code == 404


class _FakeForm(dict):
    def getlist(self, key):
        v = self.get(key, [])
        return v if isinstance(v, list) else [v]


class _FakeRequest:
    def __init__(self, link_ids, headers=None):
        self._form = _FakeForm({"link_ids": [str(i) for i in link_ids], "return_to": "/"})
        self.headers = headers or {"x-csrf-token": "1"}  # simule un appel AJAX -> 204

    async def form(self):
        return self._form


def test_bulk_mark_read_marque_tous_les_liens_selectionnes(session):
    user, link1 = _make_user_and_link(session, read=False)
    link2 = Link(user_id=user.id, url="https://example.com/y")
    session.add(link2)
    session.commit()
    session.refresh(link2)

    req = _FakeRequest([link1.id, link2.id])
    resp = asyncio.run(crud_mod.bulk_mark_read(req, user=user, session=session))
    assert resp.status_code == 204

    session.refresh(link1)
    session.refresh(link2)
    assert link1.read_at is not None
    assert link2.read_at is not None


def test_bulk_mark_read_ignore_les_liens_dautres_utilisateurs(session):
    user, link = _make_user_and_link(session, read=False)
    other = User(oidc_sub="s3", email="s3@e.com", public_slug="s3")
    session.add(other)
    session.commit()
    other_link = Link(user_id=other.id, url="https://example.com/z")
    session.add(other_link)
    session.commit()
    session.refresh(other_link)

    req = _FakeRequest([other_link.id])
    asyncio.run(crud_mod.bulk_mark_read(req, user=user, session=session))

    session.refresh(other_link)
    assert other_link.read_at is None  # appartient à un autre utilisateur, non affecté
