"""Tests d'intégration des features récentes : recherche live (rendu partiel),
extraction lecteur (+ sanitisation) et archivage Wayback (statut).

On mocke httpx via MockTransport (aucun accès réseau) et la résolution DNS.
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.routes.links as links_mod
from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import Link, User


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── Vue lecteur : extraction + sanitisation anti-XSS ────────────────────────────

def test_extract_reader_sanitizes(monkeypatch):
    html = (
        b"<html><head><title>Titre</title></head><body>"
        b"<article><h1>Mon Article</h1>"
        b"<p>Un paragraphe avec suffisamment de texte pour que l'extracteur "
        b"Readability le considere comme contenu principal, il faut vraiment "
        b"plusieurs phrases completes ici sinon le seuil interne n'est pas atteint.</p>"
        b"<p>Deuxieme paragraphe tout aussi fourni en mots afin de garantir une "
        b"extraction stable et reproductible dans ce test automatise sans reseau.</p>"
        b'<img src="https://ex.com/i.png" onerror="alert(1)">'
        b"<script>alert('xss')</script>"
        b"</article></body></html>"
    )

    def handler(request):
        return httpx.Response(200, content=html, headers={"content-type": "text/html; charset=utf-8"})

    monkeypatch.setattr(links_mod, "_http_client", _mock_client(handler))

    async def _ok(_host):
        return True

    monkeypatch.setattr(links_mod, "_hostname_resolves_public", _ok)

    data = asyncio.run(links_mod._extract_reader("https://example.com/article"))
    assert data is not None
    assert "Mon Article" in data["html"]
    assert "<script" not in data["html"]       # script supprimé
    assert "onerror" not in data["html"]        # handler inline supprimé


def test_extract_reader_rejects_private_url(monkeypatch):
    # URL privée : doit être rejetée par la garde SSRF avant toute requête
    monkeypatch.setattr(links_mod, "_http_client", _mock_client(lambda r: httpx.Response(200)))
    assert asyncio.run(links_mod._extract_reader("http://127.0.0.1/secret")) is None


# ── Archivage Wayback : statut ok / failed (plus d'échec silencieux) ────────────

def _make_link(engine, sub, url):
    with Session(engine) as s:
        u = User(oidc_sub=sub, email=f"{sub}@e.com", public_slug=sub)
        s.add(u)
        s.commit()
        lk = Link(user_id=u.id, url=url)
        s.add(lk)
        s.commit()
        return lk.id


def test_wayback_archive_ok(monkeypatch, engine):
    monkeypatch.setattr(links_mod, "db_engine", engine)
    lid = _make_link(engine, "arch_ok", "https://example.com/x")

    def handler(request):
        return httpx.Response(200, headers={"location": "https://web.archive.org/web/123/https://example.com/x"})

    monkeypatch.setattr(links_mod, "_http_client", _mock_client(handler))
    asyncio.run(links_mod._wayback_archive(lid))

    with Session(engine) as s:
        lk = s.get(Link, lid)
        assert lk.archive_status == "ok"
        assert lk.archived_url and "web.archive.org" in lk.archived_url
        assert lk.archived_at is not None


def test_wayback_archive_failed(monkeypatch, engine):
    monkeypatch.setattr(links_mod, "db_engine", engine)
    lid = _make_link(engine, "arch_ko", "https://example.com/y")

    def handler(request):
        return httpx.Response(503)

    monkeypatch.setattr(links_mod, "_http_client", _mock_client(handler))
    asyncio.run(links_mod._wayback_archive(lid))

    with Session(engine) as s:
        lk = s.get(Link, lid)
        assert lk.archive_status == "failed"
        assert lk.archived_url is None


# ── Recherche temps réel : rendu partiel vs page complète ───────────────────────

@pytest.fixture
def client(engine):
    with Session(engine) as s:
        u = User(oidc_sub="searcher", email="s@e.com", public_slug="searcher")
        s.add(u)
        s.commit()
        uid = u.id
        s.add(Link(user_id=uid, url="https://example.com/python", title="Python rocks"))
        # match "python" dans l'URL uniquement (titre sans le terme) -> doit
        # passer APRÈS le match dans le titre grâce au bm25 pondéré
        s.add(Link(user_id=uid, url="https://python.example.org/guide", title="Guide debutant"))
        s.add(Link(
            user_id=uid, url="https://dead.example.com/gone", title="Article disparu",
            is_broken=True, archive_status="ok",
            archived_url="https://web.archive.org/web/1/https://dead.example.com/gone",
            reader_html="<p>copie locale</p>",
        ))
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


def test_full_page_has_layout(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text
    assert "<aside" in r.text  # sidebar = layout complet


def test_partial_is_fragment(client):
    r = client.get("/?q=python&partial=1")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" not in r.text  # fragment seul
    assert "<aside" not in r.text
    assert "Python rocks" in r.text


def test_partial_search_filters_out_non_matching(client):
    r = client.get("/?q=zzznomatch&partial=1")
    assert r.status_code == 200
    assert "Python rocks" not in r.text


def test_bm25_title_ranks_before_url(client):
    r = client.get("/?q=python&partial=1")
    assert r.status_code == 200
    assert "Python rocks" in r.text and "Guide debutant" in r.text
    # titre (poids 10) avant URL seule (poids 1)
    assert r.text.index("Python rocks") < r.text.index("Guide debutant")


def test_broken_link_shows_recovery(client):
    r = client.get("/")
    assert "Lien cassé" in r.text
    assert "lire la copie sauvegardée" in r.text  # copie lecteur en cache
    assert "voir l'archive" in r.text              # archive Wayback
