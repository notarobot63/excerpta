"""Non-régression : une page qui ne déclare son encodage que dans le HTML.

Sans charset dans Content-Type, httpx répond `utf-8` : une page iso-8859-1
donnait « Caf� » en titre et dans la vue lecteur.
"""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from app.routes.links import enrichment, reader
from app.routes.links.net_guard import decode_html

_URL = "https://example.com/vieux-site"
_PAGE = ("<html><head><meta charset='iso-8859-1'><title>Café des sports</title></head>"
         "<body><article><h1>Été</h1>"
         + "<p>" + "Résumé très détaillé de l'été à la buvette. " * 20 + "</p>"
         + "</article></body></html>").encode("latin-1")


def _fake_network(monkeypatch, module, content_type):
    @asynccontextmanager
    async def _stream(*args, **kwargs):
        yield httpx.Response(200, headers={"content-type": content_type}, content=_PAGE,
                             request=httpx.Request("GET", _URL))

    async def _public(url):
        return True

    monkeypatch.setattr(module, "_safe_stream", _stream)
    monkeypatch.setattr(module, "_assert_public_url", _public)


def test_titre_d_une_page_latin1_sans_charset_http(monkeypatch):
    _fake_network(monkeypatch, enrichment, "text/html")
    meta = asyncio.run(enrichment._fetch_meta(_URL))
    assert meta["title"] == "Café des sports"


def test_lecteur_d_une_page_latin1_sans_charset_http(monkeypatch):
    _fake_network(monkeypatch, reader, "text/html")
    data = asyncio.run(reader._extract_reader(_URL))
    assert "Résumé très détaillé" in data["html"]
    assert "�" not in data["html"]


@pytest.mark.parametrize("content_type, body, attendu", [
    # L'en-tête prime sur le document, comme dans un navigateur.
    ("text/html; charset=utf-8", "<meta charset='iso-8859-1'>Été".encode(), "Été"),
    # Charset inconnu de Python : détection plutôt qu'une exception.
    ("text/html; charset=x-inconnu", "<meta charset='utf-8'>Été".encode(), "Été"),
])
def test_decode_html(content_type, body, attendu):
    resp = httpx.Response(200, headers={"content-type": content_type}, content=body)
    assert attendu in decode_html(resp, body)
