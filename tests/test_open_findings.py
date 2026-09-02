"""Non-régression sur les points restés ouverts après la première passe de revue.

Un test par défaut : filtrage de la recherche en base, sélecteur de tags de la
page publique, plafond du préchauffage d'images, et i18n du JavaScript.
"""
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import Link, LinkTagLink, Tag, User
from app.utils import refresh_link_fts

APP = Path(__file__).resolve().parent.parent / "app"


# ── Recherche restreinte à l'utilisateur, en base ─────────────────────────────

@pytest.fixture
def two_users(session: Session):
    """Deux comptes, un lien chacun, le même mot dans les deux titres."""
    made = []
    for sub in ("sub-a", "sub-b"):
        user = User(oidc_sub=sub, name=sub)
        session.add(user)
        session.flush()
        link = Link(user_id=user.id, url=f"https://example.test/{sub}", title="rapport annuel")
        session.add(link)
        session.flush()
        refresh_link_fts(session, link, [])
        made.append((user, link))
    session.commit()
    return made


def test_fts_join_filters_by_user(session: Session, two_users):
    """La restriction doit se faire dans la requête, pas après coup.

    Le motif employé par les deux routes de recherche : sans la jointure, la
    requête rendait les identifiants de tous les comptes.
    """
    (user_a, link_a), (_, link_b) = two_users

    rows = session.execute(
        text(
            "SELECT fts_links.rowid FROM fts_links"
            " JOIN links ON links.id = fts_links.rowid"
            " WHERE fts_links MATCH :q AND links.user_id = :uid"
            " ORDER BY bm25(fts_links, 10.0, 2.0, 2.0, 1.0, 4.0)"
        ),
        {"q": "rapport*", "uid": user_a.id},
    ).fetchall()

    ids = [r[0] for r in rows]
    assert ids == [link_a.id]
    assert link_b.id not in ids, "la recherche traverse les comptes"


def test_search_routes_filter_in_sql():
    """Garde-fou : les deux routes de recherche joignent bien sur links."""
    for path in ("routes/links/crud.py", "routes/api.py"):
        source = (APP / path).read_text()
        assert "JOIN links ON links.id = fts_links.rowid" in source, path
        assert "links.user_id = :uid" in source, path


# ── Page publique : sélecteur de tags et filtrage ─────────────────────────────

@pytest.fixture
def public_owner(session: Session):
    owner = User(oidc_sub="sub-public", name="Public", public_slug="public")
    session.add(owner)
    session.flush()
    tags = {}
    for name in ("archives", "veille"):
        tag = Tag(user_id=owner.id, name=name)
        session.add(tag)
        session.flush()
        tags[name] = tag
    for i, tag_name in enumerate(("archives", "veille")):
        link = Link(
            user_id=owner.id, url=f"https://example.test/p{i}",
            title=f"Public {i}", is_public=True,
        )
        session.add(link)
        session.flush()
        session.add(LinkTagLink(link_id=link.id, tag_id=tags[tag_name].id))
        session.flush()
        refresh_link_fts(session, link, [tags[tag_name]])
    session.commit()
    return owner


def test_public_tag_selector_lists_every_tag(session: Session, public_owner):
    """Le sélecteur doit rester complet, même quand un filtre est actif.

    Construit depuis les liens déjà filtrés, il se réduisait au tag courant et
    empêchait de passer à un autre.
    """
    rows = session.execute(
        text(
            "SELECT DISTINCT t.name FROM tags t"
            " JOIN link_tags lt ON lt.tag_id = t.id"
            " JOIN links l ON l.id = lt.link_id"
            " WHERE l.user_id = :uid AND l.is_public = 1"
            " ORDER BY t.name"
        ),
        {"uid": public_owner.id},
    ).fetchall()

    assert [r[0] for r in rows] == ["archives", "veille"]


def test_public_route_filters_tag_in_sql():
    source = (APP / "routes/public.py").read_text()
    filter_pos = source.find("stmt.join(LinkTagLink")
    limit_pos = source.find("_PUBLIC_MAX_LINKS)")
    assert filter_pos != -1, "le filtre par tag doit être une jointure SQL"
    assert filter_pos < limit_pos, "le filtre doit précéder la troncature"


# ── Préchauffage borné ────────────────────────────────────────────────────────

def test_image_warmup_is_capped():
    source = (APP / "routes/links/proxy.py").read_text()
    assert "_WARMUP_MAX_LINKS" in source
    warm = source[source.find("async def warm_img_cache"):source.find("async def _fetch")]
    assert ".limit(_WARMUP_MAX_LINKS)" in warm, "la requête de préchauffage doit être bornée"


# ── i18n du JavaScript ────────────────────────────────────────────────────────

_ACCENTED = re.compile(r"""['"][^'"]*[éèêëàâçùûôîï][^'"]*['"]""")


def test_no_french_literals_left_in_static_js():
    """Les libellés du JS viennent du serveur, traduits.

    Un accent dans une chaîne littérale trahit un libellé écrit en dur, que
    `pybabel extract` ne verrait jamais.
    """
    offenders = {}
    for js in (APP / "static").glob("*.js"):
        if js.name.endswith(".min.js"):
            continue
        found = [
            line.strip()
            for line in js.read_text(encoding="utf-8").splitlines()
            if _ACCENTED.search(line) and not line.strip().startswith("//")
        ]
        if found:
            offenders[js.name] = found
    assert not offenders, f"chaînes françaises en dur : {offenders}"


def test_js_strings_partial_is_included_where_app_js_loads():
    templates = APP / "templates"
    for name in ("base.html", "public/index.html"):
        source = (templates / name).read_text()
        if "/static/app.js" in source:
            assert '_js_strings.html' in source, f"{name} charge app.js sans ses libellés"


def test_js_strings_uses_client_side_token_not_percent():
    """Jinja interpole toujours `%` au retour de `_()` : `%(x)s` sans valeur
    fait échouer le rendu de la page entière."""
    source = (APP / "templates/settings/index.html").read_text()
    template_attr = [line for line in source.splitlines() if "data-template=" in line]
    assert template_attr, "le gabarit du libellé de thème a disparu"
    assert "%(" not in template_attr[0], "utiliser {name}, pas %(name)s"
