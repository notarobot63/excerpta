"""Non-régression : les URL relatives d'une page sont résolues contre la page.

- Vue lecteur : sans `url` passée à readability, `src="/img/a.png"` restait
  relatif et visait Excerpta : images et liens cassés.
- Favicon : `href="assets/fav.png"` était collé à l'hôte, ce qui donnait
  « https://example.comassets/fav.png », soit un autre hôte.
"""
import asyncio
import re
from contextlib import asynccontextmanager

import pytest

from app.routes.links import enrichment, reader

_PAGE_URL = "https://example.com/blog/2026/post"
_PARAGRAPH = "<p>" + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20 + "</p>"


def _fake_network(monkeypatch, module, html: str):
    class _Resp:
        status_code = 200
        url = _PAGE_URL
        encoding = "utf-8"
        headers = {"content-type": "text/html; charset=utf-8"}

        async def aiter_bytes(self):
            yield html.encode()

    @asynccontextmanager
    async def _stream(*args, **kwargs):
        yield _Resp()

    async def _public(url):
        return True

    monkeypatch.setattr(module, "_safe_stream", _stream)
    monkeypatch.setattr(module, "_assert_public_url", _public)


def test_lecteur_resout_images_et_liens(monkeypatch):
    html = (f"<html><body><article><h1>T</h1>{_PARAGRAPH}"
            f"<img src='/img/fig1.png'><a href='../autre'>suite</a>{_PARAGRAPH}"
            "</article></body></html>")
    _fake_network(monkeypatch, reader, html)

    data = asyncio.run(reader._extract_reader(_PAGE_URL))

    urls = re.findall(r'(?:src|href)="([^"]*)"', data["html"])
    assert "https://example.com/img/fig1.png" in urls
    assert "https://example.com/blog/autre" in urls


@pytest.mark.parametrize("href, attendu", [
    ("assets/fav.png", "https://example.com/blog/2026/assets/fav.png"),
    ("/fav.png", "https://example.com/fav.png"),
    ("//cdn.example.net/fav.png", "https://cdn.example.net/fav.png"),
    ("https://static.example.org/fav.png", "https://static.example.org/fav.png"),
])
def test_favicon_relatif(monkeypatch, href, attendu):
    _fake_network(monkeypatch, enrichment,
                  f'<html><head><title>T</title><link rel="icon" href="{href}"></head></html>')
    meta = asyncio.run(enrichment._fetch_meta(_PAGE_URL))
    assert meta["favicon_url"] == attendu


def test_vignette_og_relative(monkeypatch):
    _fake_network(monkeypatch, enrichment,
                  '<html><head><meta property="og:image" content="cover.jpg"></head></html>')
    meta = asyncio.run(enrichment._fetch_meta(_PAGE_URL))
    assert meta["thumbnail_url"] == "https://example.com/blog/2026/cover.jpg"


def test_favicon_vers_adresse_privee_refuse(monkeypatch):
    _fake_network(monkeypatch, enrichment,
                  '<html><head><link rel="icon" href="http://127.0.0.1/fav.png"></head></html>')
    meta = asyncio.run(enrichment._fetch_meta(_PAGE_URL))
    assert meta["favicon_url"] == "https://example.com/favicon.ico"
