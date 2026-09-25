"""Non-régression : une étiquette répétée (à la casse près) ne fait plus d'erreur 500.

« Python, python » donnait deux fois la même étiquette, donc deux lignes
identiques dans `link_tags` : violation de clé primaire. À l'import, un seul
favori portant `TAGS="Foo,foo"` annulait tout le fichier.
"""
import asyncio
import io

from fastapi import BackgroundTasks, UploadFile
from sqlmodel import select

from app.models import Link, Tag, User
from app.routes import settings as settings_routes
from app.routes.links import crud


class _FakeRequest:
    def __init__(self, json_body=None):
        self._json = json_body

    async def json(self):
        return self._json


def _user(session):
    user = User(oidc_sub="dup", public_slug="dup")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _tag_names(link):
    return sorted(t.name for t in link.tags)


def test_creation_avec_doublons(session):
    user = _user(session)
    link, created = crud.create_link(
        session, BackgroundTasks(), user_id=user.id, url="https://e.example",
        tag_names=["Python", "python", " PYTHON ", "web"],
    )
    assert created
    session.refresh(link)
    assert _tag_names(link) == ["python", "web"]


def test_ajout_groupe_avec_doublons(session):
    user = _user(session)
    link = Link(user_id=user.id, url="https://e.example")
    session.add(link)
    session.commit()

    req = _FakeRequest({"link_ids": [link.id], "tags": "Rust, rust"})
    asyncio.run(crud.bulk_tag_links(req, user=user, session=session))
    session.refresh(link)
    assert _tag_names(link) == ["rust"]


def test_import_avec_doublons(session):
    user = _user(session)
    html = (b'<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>'
            b'<DT><A HREF="https://a.example" TAGS="Foo,foo">A</A>'
            b'<DT><A HREF="https://b.example" TAGS="bar">B</A></DL>')
    upload = UploadFile(file=io.BytesIO(html), filename="bookmarks.html")
    asyncio.run(settings_routes.import_links(
        request=None, background_tasks=BackgroundTasks(), file=upload, user=user, session=session,
    ))
    links = session.exec(select(Link).where(Link.user_id == user.id)).all()
    assert sorted(lk.url for lk in links) == ["https://a.example", "https://b.example"]
    assert sorted(t.name for t in session.exec(select(Tag)).all()) == ["bar", "foo"]


def test_renommage_normalise_la_casse(session):
    """Sans quoi « Python » renommé et « python » ajouté ensuite coexistaient."""
    from app.routes import tags as tags_routes

    user = _user(session)
    tag = Tag(user_id=user.id, name="py")
    session.add(tag)
    session.commit()

    resp = asyncio.run(tags_routes.rename_tag(
        tag.id, tags_routes.RenameTagBody(name=" Python "), user=user, session=session,
    ))
    assert b'"new_name":"python"' in resp.body
    crud.create_link(session, BackgroundTasks(), user_id=user.id,
                     url="https://e.example", tag_names=["Python"])
    assert [t.name for t in session.exec(select(Tag)).all()] == ["python"]
