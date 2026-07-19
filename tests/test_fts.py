"""Non-régression FTS : le bug historique était des DELETE sur une colonne
`link_id` inexistante (la table fts_links est en rowid)."""
from sqlalchemy import text

from app.models import Link, LinkTagLink, User
from app.utils import get_or_create_tag, refresh_link_fts


def _make_user(session) -> User:
    u = User(oidc_sub="sub-1", name="Alice", public_slug="alice")
    session.add(u)
    session.flush()
    return u


def _add_link(session, user, url, title, tags):
    link = Link(user_id=user.id, url=url, title=title, description="", note="")
    session.add(link)
    session.flush()
    tag_objs = [get_or_create_tag(session, user.id, t) for t in tags]
    for t in tag_objs:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))
    session.flush()
    refresh_link_fts(session, link, tag_objs)
    session.commit()
    return link


def _search_ids(session, query):
    rows = session.execute(
        text("SELECT rowid FROM fts_links WHERE fts_links MATCH :q"), {"q": query}
    ).fetchall()
    return {r[0] for r in rows}


def test_link_indexed_and_searchable(session):
    user = _make_user(session)
    link = _add_link(session, user, "https://ex.com", "Recette tarte aux pommes", ["cuisine"])
    assert link.id in _search_ids(session, "tarte")
    assert link.id in _search_ids(session, "cuisine")  # tag indexé


def test_delete_link_removes_from_fts(session):
    user = _make_user(session)
    link = _add_link(session, user, "https://ex.com/x", "Document unique", [])
    assert link.id in _search_ids(session, "unique")
    session.delete(link)
    session.commit()
    # trigger links_ad supprime l'entrée FTS
    assert link.id not in _search_ids(session, "unique")


def test_delete_tag_preserves_link_fts(session):
    """Régression : supprimer un tag ne doit PAS détruire le FTS des liens
    qui le portaient - ils existent toujours."""
    user = _make_user(session)
    link = _add_link(session, user, "https://ex.com/y", "Article important", ["obsolete"])
    tag_id = session.execute(
        text("SELECT id FROM tags WHERE name = 'obsolete'")
    ).scalar_one()

    # Logique de delete_tag (version corrigée) :
    affected = [
        r[0] for r in session.execute(
            text("SELECT link_id FROM link_tags WHERE tag_id = :t"), {"t": tag_id}
        ).fetchall()
    ]
    session.execute(text("DELETE FROM link_tags WHERE tag_id = :t"), {"t": tag_id})
    session.execute(text("DELETE FROM tags WHERE id = :t"), {"t": tag_id})
    session.flush()
    for lid in affected:
        lk = session.get(Link, lid)
        if lk:
            refresh_link_fts(session, lk, list(lk.tags))
    session.commit()

    # Le lien reste cherchable par son titre, le tag a disparu de l'index
    assert link.id in _search_ids(session, "important")
    assert link.id not in _search_ids(session, "obsolete")


def test_fts_uses_rowid_not_link_id(session):
    """Garde-fou direct : la table n'a pas de colonne link_id."""
    cols = {
        r[1] for r in session.execute(text("PRAGMA table_info(fts_links)")).fetchall()
    }
    assert "link_id" not in cols
    assert "rowid" not in cols  # rowid est implicite, pas listé
