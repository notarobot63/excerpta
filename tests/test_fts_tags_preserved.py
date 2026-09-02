"""Non-régression : un UPDATE sur `links` ne doit pas vider la colonne `tags`.

L'ancien déclencheur `links_au` réinsérait la ligne FTS avec `tags = ''`. Un
déplacement de lien, une extraction lecteur, un archivage ou une mise à jour de
vignette effaçaient donc les étiquettes de l'index : le lien cessait d'être
trouvé par la recherche sur son étiquette, alors qu'elle restait en base.
"""
import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import Folder, Link, LinkTagLink, Tag, User
from app.utils import refresh_link_fts


@pytest.fixture
def link_with_tag(session: Session):
    user = User(oidc_sub="sub-fts-tags", name="FTS")
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


def test_tag_indexed_after_creation(session: Session, link_with_tag):
    _, link, _ = link_with_tag
    assert _matches(session, "python*") == [link.id]


def test_tag_survives_folder_move(session: Session, link_with_tag):
    """Le chemin de `move_link` / `bulk_move_links` : un UPDATE nu sur links."""
    user, link, _ = link_with_tag
    folder = Folder(user_id=user.id, name="Cible")
    session.add(folder)
    session.flush()

    link.folder_id = folder.id
    session.add(link)
    session.commit()

    assert _matches(session, "python*") == [link.id]


def test_tag_survives_reader_extraction(session: Session, link_with_tag):
    """Le chemin de `read_link` : écriture de reader_html sans toucher aux tags."""
    _, link, _ = link_with_tag
    link.reader_html = "<p>corps</p>"
    link.reader_failed = False
    session.add(link)
    session.commit()

    assert _matches(session, "python*") == [link.id]


def test_tag_survives_metadata_enrichment(session: Session, link_with_tag):
    """Le chemin de `_fetch_and_update_meta` et de l'archivage."""
    _, link, _ = link_with_tag
    link.thumbnail_url = "https://example.test/img.png"
    link.archive_status = "ok"
    session.add(link)
    session.commit()

    assert _matches(session, "python*") == [link.id]


def test_updated_columns_are_reindexed(session: Session, link_with_tag):
    """L'indexation du reste ne doit pas régresser en corrigeant les étiquettes."""
    _, link, _ = link_with_tag
    link.title = "Titre remanie"
    session.add(link)
    session.commit()

    assert _matches(session, "remanie*") == [link.id]
    assert _matches(session, "python*") == [link.id]


def test_tags_removed_from_link_leave_the_index(session: Session, link_with_tag):
    """Le déclencheur relit link_tags : retirer l'association doit se voir."""
    _, link, tag = link_with_tag
    session.execute(
        text("DELETE FROM link_tags WHERE link_id = :lid"), {"lid": link.id}
    )
    session.flush()
    # Une écriture quelconque sur le lien rafraîchit sa ligne d'index.
    link.title = "Sans etiquette"
    session.add(link)
    session.commit()

    assert _matches(session, "python*") == []
    assert _matches(session, "etiquette*") == [link.id]


def test_delete_still_removes_from_index(session: Session, link_with_tag):
    """Suppression par l'ORM, comme `delete_link` : les associations partent
    avec le lien (les retirer en SQL d'abord lèverait un StaleDataError)."""
    _, link, _ = link_with_tag
    session.delete(link)
    session.commit()

    assert _matches(session, "python*") == []
    assert _matches(session, "Article*") == []
