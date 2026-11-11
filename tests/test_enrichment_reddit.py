"""Liens Reddit : titre par oEmbed, le scraping recevant une page de vérification."""
import asyncio

import httpx

from app.routes.links import enrichment as en

POST = "https://www.reddit.com/r/selfhosted/comments/1t5pc19/pia_tun/"


def _fake(calls, oembed_status=200):
    async def _safe_request(method, url, **kw):
        calls.append((method, url))
        if method == "HEAD":  # lien de partage : redirection vers le post
            return httpx.Response(200, request=httpx.Request("HEAD", POST + "?share_id=x"))
        return httpx.Response(oembed_status, json={"title": "pia-tun", "author_name": "x0lie"},
                              request=httpx.Request("GET", url))
    return _safe_request


def test_share_link_resolved_then_oembed(monkeypatch):
    calls = []
    monkeypatch.setattr(en, "safe_request", _fake(calls))
    meta = asyncio.run(en._fetch_meta("https://www.reddit.com/r/selfhosted/s/EMre6Q0eo6"))
    assert meta["title"] == "pia-tun"
    assert meta["description"] == "r/selfhosted · u/x0lie"
    assert calls[0][0] == "HEAD"
    assert "share_id" not in calls[1][1] and "comments%2F1t5pc19" in calls[1][1]


def test_post_link_goes_straight_to_oembed(monkeypatch):
    calls = []
    monkeypatch.setattr(en, "safe_request", _fake(calls))
    assert asyncio.run(en._reddit_meta(POST))["title"] == "pia-tun"
    assert [m for m, _ in calls] == ["GET"]


def test_oembed_failure_falls_back_to_generic(monkeypatch):
    calls = []
    monkeypatch.setattr(en, "safe_request", _fake(calls, oembed_status=404))
    assert asyncio.run(en._reddit_meta(POST)) is None
    # Ni post ni commentaire (page de subreddit) : pas d'oEmbed.
    assert asyncio.run(en._reddit_meta("https://www.reddit.com/r/selfhosted/")) is None
