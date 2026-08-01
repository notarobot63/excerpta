"""Non-régression sur les actions groupées bulk-move / bulk-tag
(app/routes/links/crud.py). Style d'appel direct des fonctions, comme
test_read_status.py : le CSRF qui protège ces routes en pratique est déjà
couvert séparément par test_csrf.py."""
import asyncio

import pytest
from sqlmodel import select

from app.models import Folder, Link, LinkTagLink, Tag, User
from app.routes.links import crud as crud_mod


class _FakeRequest:
    def __init__(self, json_body):
        self._json = json_body

    async def json(self):
        return self._json


def _make_user(session, sub="u1"):
    user = User(oidc_sub=sub, email=f"{sub}@e.com", public_slug=sub)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_link(session, user, url, folder_id=None):
    link = Link(user_id=user.id, url=url, folder_id=folder_id)
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


# ── bulk-move ─────────────────────────────────────────────────────────────────

def test_bulk_move_deplace_les_liens_selectionnes(session):
    user = _make_user(session)
    folder = Folder(user_id=user.id, name="Lectures")
    session.add(folder)
    session.commit()
    session.refresh(folder)
    l1 = _make_link(session, user, "https://example.com/a")
    l2 = _make_link(session, user, "https://example.com/b")

    req = _FakeRequest({"link_ids": [l1.id, l2.id], "folder_id": folder.id})
    result = asyncio.run(crud_mod.bulk_move_links(req, user=user, session=session))
    assert result == {"ok": True, "folder_id": folder.id}

    session.refresh(l1)
    session.refresh(l2)
    assert l1.folder_id == folder.id
    assert l2.folder_id == folder.id


def test_bulk_move_vers_aucun_dossier(session):
    user = _make_user(session)
    folder = Folder(user_id=user.id, name="Lectures")
    session.add(folder)
    session.commit()
    session.refresh(folder)
    link = _make_link(session, user, "https://example.com/a", folder_id=folder.id)

    req = _FakeRequest({"link_ids": [link.id], "folder_id": None})
    asyncio.run(crud_mod.bulk_move_links(req, user=user, session=session))

    session.refresh(link)
    assert link.folder_id is None


def test_bulk_move_ignore_les_liens_dautres_utilisateurs(session):
    user = _make_user(session, "u1")
    other = _make_user(session, "u2")
    folder = Folder(user_id=user.id, name="Lectures")
    session.add(folder)
    session.commit()
    session.refresh(folder)
    other_link = _make_link(session, other, "https://example.com/x")

    req = _FakeRequest({"link_ids": [other_link.id], "folder_id": folder.id})
    asyncio.run(crud_mod.bulk_move_links(req, user=user, session=session))

    session.refresh(other_link)
    assert other_link.folder_id is None  # appartient à un autre utilisateur, non affecté


# ── bulk-tag ──────────────────────────────────────────────────────────────────

def _tag_names(session, link_id):
    rows = session.exec(
        select(Tag).join(LinkTagLink, LinkTagLink.tag_id == Tag.id)
        .where(LinkTagLink.link_id == link_id)
    ).all()
    return {t.name for t in rows}


def test_bulk_tag_ajoute_sans_dupliquer(session):
    user = _make_user(session)
    l1 = _make_link(session, user, "https://example.com/a")
    l2 = _make_link(session, user, "https://example.com/b")
    existing_tag = Tag(user_id=user.id, name="lecture")
    session.add(existing_tag)
    session.commit()
    session.refresh(existing_tag)
    session.add(LinkTagLink(link_id=l1.id, tag_id=existing_tag.id))
    session.commit()

    req = _FakeRequest({"link_ids": [l1.id, l2.id], "tags": "lecture, python"})
    result = asyncio.run(crud_mod.bulk_tag_links(req, user=user, session=session))
    assert result["ok"] is True

    assert _tag_names(session, l1.id) == {"lecture", "python"}  # pas de doublon sur "lecture"
    assert _tag_names(session, l2.id) == {"lecture", "python"}


def test_bulk_tag_ignore_les_liens_dautres_utilisateurs(session):
    user = _make_user(session, "u1")
    other = _make_user(session, "u2")
    other_link = _make_link(session, other, "https://example.com/x")

    req = _FakeRequest({"link_ids": [other_link.id], "tags": "python"})
    asyncio.run(crud_mod.bulk_tag_links(req, user=user, session=session))

    assert _tag_names(session, other_link.id) == set()


def test_bulk_tag_sans_tags_ne_fait_rien(session):
    user = _make_user(session)
    link = _make_link(session, user, "https://example.com/a")

    req = _FakeRequest({"link_ids": [link.id], "tags": ""})
    result = asyncio.run(crud_mod.bulk_tag_links(req, user=user, session=session))
    assert result == {"ok": True, "tags": []}
    assert _tag_names(session, link.id) == set()
