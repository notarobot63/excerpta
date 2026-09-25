"""Import / export Netscape : notes, dossiers, et aller-retour.

Deux défauts corrigés :
- la note (`<DD>`) n'était jamais retrouvée : html.parser la range dans le
  `<DT>`, là où l'ancien code ne la cherchait pas ;
- les dossiers (`<H3>` + `<DL>`) étaient ignorés, et l'export plat les
  réduisait à un attribut FOLDER que l'import ne relisait pas.
"""
import asyncio
import io
from datetime import datetime

from fastapi import BackgroundTasks, UploadFile
from sqlmodel import select

from app.models import Folder, Link, LinkTagLink, Tag, User
from app.netscape import build_bookmarks, parse_bookmarks
from app.routes import settings as settings_routes

# Forme réelle d'un export Firefox/Chrome : <DT>/<DD>/<p> jamais fermés.
FIREFOX = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks Menu</H1>
<DL><p>
    <DT><H3 ADD_DATE="1">Dev</H3>
    <DD>Description du dossier, pas d'un lien
    <DL><p>
        <DT><A HREF="https://a.example" TAGS="py,web">A &amp; co</A>
        <DD>Note de A
        <DT><H3>Python</H3>
        <DL><p>
            <DT><A HREF="https://b.example">B</A>
        </DL><p>
        <DT><A HREF="https://c.example">C</A>
    </DL><p>
    <DT><A HREF="https://d.example">D</A>
    <DD>Note
de D
    <DT><A HREF="javascript:alert(1)">JS</A>
    <DD>Note orpheline
</DL><p>
"""


def _by_url(items):
    return {i["url"]: i for i in items}


def test_dossiers_imbriques_et_notes():
    items = _by_url(parse_bookmarks(FIREFOX))
    assert set(items) == {"https://a.example", "https://b.example", "https://c.example", "https://d.example"}
    assert items["https://a.example"]["folder_path"] == ("Dev",)
    assert items["https://b.example"]["folder_path"] == ("Dev", "Python")
    assert items["https://c.example"]["folder_path"] == ("Dev",), "le </DL> doit refermer le sous-dossier"
    assert items["https://d.example"]["folder_path"] == ()
    assert items["https://a.example"]["note"] == "Note de A"
    assert items["https://d.example"]["note"] == "Note de D"
    assert items["https://b.example"]["note"] == ""
    assert items["https://a.example"]["title"] == "A & co"
    assert items["https://a.example"]["tags"] == ["py", "web"]


def test_ancien_export_excerpta_avec_attribut_folder():
    html = '<DL><p><DT><A HREF="https://a.example" FOLDER="Lectures">A</A></DL>'
    assert parse_bookmarks(html)[0]["folder_path"] == ("Lectures",)


def _user(session):
    user = User(oidc_sub="nts", public_slug="nts")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _import(session, user, html: str):
    upload = UploadFile(file=io.BytesIO(html.encode()), filename="bookmarks.html")
    asyncio.run(settings_routes.import_links(
        request=None, background_tasks=BackgroundTasks(), file=upload, user=user, session=session,
    ))


def _folder_path(session, folder_id):
    path = []
    while folder_id is not None:
        folder = session.get(Folder, folder_id)
        path.insert(0, folder.name)
        folder_id = folder.parent_id
    return tuple(path)


def test_import_cree_l_arborescence_sans_la_dupliquer(session):
    user = _user(session)
    _import(session, user, FIREFOX)
    links = {lk.url: lk for lk in session.exec(select(Link).where(Link.user_id == user.id)).all()}
    assert _folder_path(session, links["https://b.example"].folder_id) == ("Dev", "Python")
    assert links["https://a.example"].note == "Note de A"
    assert links["https://d.example"].folder_id is None

    # Un second import (autres liens, mêmes dossiers) réutilise l'arbre existant.
    _import(session, user, FIREFOX.replace("b.example", "e.example"))
    names = sorted(f.name for f in session.exec(select(Folder).where(Folder.user_id == user.id)).all())
    assert names == ["Dev", "Python"]


def test_aller_retour_export_import(session):
    user = _user(session)
    root = Folder(user_id=user.id, name="Dev")
    session.add(root)
    session.flush()
    child = Folder(user_id=user.id, name="Py <thon>", parent_id=root.id)
    empty = Folder(user_id=user.id, name="Vide")
    session.add_all([child, empty])
    session.flush()
    tag = Tag(user_id=user.id, name="web")
    session.add(tag)
    session.flush()
    now = datetime(2026, 1, 2, 3, 4, 5)
    l1 = Link(user_id=user.id, url="https://a.example/?x=1&y=2", title='Titre "A"',
              note="Ma note", folder_id=child.id, created_at=now)
    l2 = Link(user_id=user.id, url="https://b.example", title="B", created_at=now)
    session.add_all([l1, l2])
    session.flush()
    session.add(LinkTagLink(link_id=l1.id, tag_id=tag.id))
    session.commit()
    session.refresh(l1)
    session.refresh(l2)

    html = build_bookmarks([l1, l2], [root, child, empty])
    items = _by_url(parse_bookmarks(html))

    a = items["https://a.example/?x=1&y=2"]
    assert a["folder_path"] == ("Dev", "Py <thon>")
    assert a["title"] == 'Titre "A"'
    assert a["note"] == "Ma note"
    assert a["tags"] == ["web"]
    assert items["https://b.example"]["folder_path"] == ()
