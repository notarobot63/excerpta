"""Non-régression : renommer ou supprimer une étiquette doit se voir en recherche.

Deux défauts couverts ici, tous deux sur la colonne `tags` de l'index :

- le renommage simple ne réécrivait pas l'index (seul le chemin de fusion le
  faisait), si bien que la recherche trouvait encore le lien par l'ancienne
  étiquette et l'ignorait sous la nouvelle ;
- la suppression réécrivait l'index depuis `link.tags` déjà chargée par l'ORM,
  donc avec l'étiquette qui venait d'être retirée en SQL : elle restait
  trouvable.

Les tests passent par les routes elles-mêmes, et non par leur helper : retirer
l'appel de réindexation d'une route doit faire échouer la suite.
"""
import asyncio

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import Link, LinkTagLink, Tag, User
from app.routes.tags import RenameTagBody, delete_tag, rename_tag
from app.utils import refresh_link_fts


@pytest.fixture
def tagged(session: Session):
    user = User(oidc_sub="sub-rename", name="Rename")
    session.add(user)
    session.flush()
    link = Link(user_id=user.id, url="https://example.test/a", title="Article")
    session.add(link)
    session.flush()
    tag = Tag(user_id=user.id, name="python")
    session.add(tag)
    session.flush()
    session.add(LinkTagLink(link_id=link.id, tag_id=tag.id))
    session.flush()
    refresh_link_fts(session, link, [tag])
    session.commit()
    return user, link, tag


def _matches(session: Session, query: str) -> list[int]:
    return [
        r[0]
        for r in session.execute(
            text("SELECT rowid FROM fts_links WHERE fts_links MATCH :q"), {"q": query}
        ).fetchall()
    ]


def _add_tag(session: Session, user: User, link: Link, name: str) -> Tag:
    tag = Tag(user_id=user.id, name=name)
    session.add(tag)
    session.flush()
    session.add(LinkTagLink(link_id=link.id, tag_id=tag.id))
    session.flush()
    refresh_link_fts(session, link, list(link.tags) + [tag])
    session.commit()
    return tag


def test_rename_moves_the_link_to_the_new_name(session: Session, tagged):
    user, link, tag = tagged
    assert _matches(session, "python*") == [link.id]

    asyncio.run(rename_tag(
        tag_id=tag.id, body=RenameTagBody(name="serpent"), user=user, session=session
    ))

    assert _matches(session, "serpent*") == [link.id]
    assert _matches(session, "python*") == [], "l'ancienne étiquette reste indexée"


def test_rename_keeps_other_tags_of_the_link(session: Session, tagged):
    user, link, tag = tagged
    _add_tag(session, user, link, "veille")

    asyncio.run(rename_tag(
        tag_id=tag.id, body=RenameTagBody(name="serpent"), user=user, session=session
    ))

    assert _matches(session, "serpent*") == [link.id]
    assert _matches(session, "veille*") == [link.id]


def test_rename_into_an_existing_name_merges_and_reindexes(session: Session, tagged):
    """Chemin de fusion : le lien doit être trouvable sous le nom cible."""
    user, link, tag = tagged
    other = Tag(user_id=user.id, name="veille")
    session.add(other)
    session.commit()

    asyncio.run(rename_tag(
        tag_id=tag.id, body=RenameTagBody(name="veille"), user=user, session=session
    ))

    assert _matches(session, "veille*") == [link.id]
    assert _matches(session, "python*") == []


def test_delete_leaves_the_link_indexed_without_its_tag(session: Session, tagged):
    user, link, tag = tagged

    asyncio.run(delete_tag(tag_id=tag.id, user=user, session=session))

    assert _matches(session, "python*") == [], "l'étiquette supprimée reste trouvable"
    assert _matches(session, "Article*") == [link.id], "le lien doit survivre à son étiquette"


def test_delete_keeps_the_other_tags_searchable(session: Session, tagged):
    user, link, tag = tagged
    _add_tag(session, user, link, "veille")

    asyncio.run(delete_tag(tag_id=tag.id, user=user, session=session))

    assert _matches(session, "python*") == []
    assert _matches(session, "veille*") == [link.id]


def test_rename_refuses_another_users_tag(session: Session, tagged):
    from fastapi import HTTPException

    _, _, tag = tagged
    intruder = User(oidc_sub="sub-intrus", name="Intrus")
    session.add(intruder)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rename_tag(
            tag_id=tag.id, body=RenameTagBody(name="vole"), user=intruder, session=session
        ))
    assert exc.value.status_code == 404
