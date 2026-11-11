"""Import de flux RSS/Atom : analyse, dédoublonnage, dossier d'import, XML hostile."""
import asyncio

import pytest
from sqlmodel import select

import app.routes.feed_import as fi
from app.crypto import encrypt
from app.models import FeedImportConfig, Folder, Link, User

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Un post</title>
    <link href="https://forum.example/r/selfhosted/comments/abc/un_post/" />
    <content type="html">&lt;p&gt;Corps du post&lt;/p&gt;</content>
    <published>2026-11-01T10:00:00+00:00</published>
  </entry>
  <entry>
    <title>Un commentaire</title>
    <link rel="alternate" href="https://forum.example/r/x/comments/def/t/c1/" />
    <updated>2026-11-02T10:00:00Z</updated>
  </entry>
</feed>"""

RSS = b"""<rss version="2.0"><channel>
  <item><title>Article</title><link>https://exemple.org/a</link>
  <pubDate>Mon, 03 Nov 2026 10:00:00 GMT</pubDate></item>
  <item><title>Sans lien</title></item>
</channel></rss>"""


def test_parse_atom_keeps_discussion_link_and_date():
    entries = fi.parse_feed(ATOM)
    assert [e["url"] for e in entries] == [
        "https://forum.example/r/selfhosted/comments/abc/un_post/",
        "https://forum.example/r/x/comments/def/t/c1/",
    ]
    assert entries[0]["description"] == "Corps du post"
    assert entries[0]["published"].isoformat() == "2026-11-01T10:00:00"
    assert entries[1]["published"].isoformat() == "2026-11-02T10:00:00"


def test_parse_rss_skips_entries_without_link():
    entries = fi.parse_feed(RSS)
    assert [e["url"] for e in entries] == ["https://exemple.org/a"]
    assert entries[0]["published"].isoformat() == "2026-11-03T10:00:00"


def test_parse_does_not_resolve_external_entities(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("FUITE")
    xxe = f"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]>
<rss><channel><item><title>&x;</title><link>https://e.org/</link></item></channel></rss>""".encode()
    entries = fi.parse_feed(xxe)
    assert "FUITE" not in entries[0]["title"]


def test_parse_invalid_xml_raises_runtime_error():
    with pytest.raises(RuntimeError):
        fi.parse_feed(b"<html>pas un flux")


@pytest.fixture
def env(session, monkeypatch):
    user = User(oidc_sub="feed", public_slug="feed")
    session.add(user)
    session.commit()
    config = FeedImportConfig(user_id=user.id, feed_url=encrypt("https://feeds.example/private.rss?token=t"),
                              is_enabled=True)
    session.add(config)
    session.commit()

    async def _fetch(url):
        assert url == "https://feeds.example/private.rss?token=t"  # URL bien déchiffrée
        return fi.parse_feed(ATOM)

    monkeypatch.setattr(fi, "_fetch_feed", _fetch)
    monkeypatch.setattr(fi, "spawn", lambda coro, name=None: coro.close())
    return user, config


def test_sync_imports_into_folder_once(session, env):
    user, config = env
    # Déjà enregistré à la main : ni doublon, ni déplacement.
    session.add(Link(user_id=user.id, url="https://forum.example/r/x/comments/def/t/c1/"))
    session.commit()

    assert asyncio.run(fi.sync_feed(config, session)) == 1
    assert asyncio.run(fi.sync_feed(config, session)) == 0

    folder = session.exec(select(Folder).where(Folder.user_id == user.id, Folder.name == "Feeds")).one()
    links = session.exec(select(Link).where(Link.user_id == user.id)).all()
    assert len(links) == 2
    imported = next(l for l in links if "un_post" in l.url)
    assert imported.folder_id == folder.id and imported.title == "Un post"
    assert config.synced_count == 1 and config.last_sync is not None


def test_deleted_link_is_not_reimported(session, env):
    user, config = env
    assert asyncio.run(fi.sync_feed(config, session)) == 2
    for link in session.exec(select(Link).where(Link.user_id == user.id)).all():
        session.delete(link)
    session.commit()
    assert asyncio.run(fi.sync_feed(config, session)) == 0


def test_parse_atom_xhtml_content_and_rss1():
    xhtml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
      <link href="https://e.org/x"/><content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
      <p>Texte riche</p></div></content></entry></feed>"""
    assert fi.parse_feed(xhtml)[0]["description"] == "Texte riche"
    rdf = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"><item rdf:about="https://e.org/r"><title>R</title>
      <link>https://e.org/r</link><dc:date>2026-11-04T10:00:00Z</dc:date></item></rdf:RDF>"""
    entries = fi.parse_feed(rdf)
    assert [e["url"] for e in entries] == ["https://e.org/r"]
    assert entries[0]["published"].isoformat() == "2026-11-04T10:00:00"


def test_refused_url_error_does_not_leak_token(monkeypatch):
    async def _refuse(method, url, **kw):
        raise fi._UnsafeRedirect(f"URL refusée : {url}")

    monkeypatch.setattr(fi, "safe_request", _refuse)
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(fi._fetch_feed("https://feeds.example/private.rss?token=SECRET"))
    assert "SECRET" not in str(exc.value)


def test_is_due_follows_each_interval():
    from datetime import datetime, timedelta
    now = datetime(2026, 11, 11, 12, 0)
    assert fi._is_due(FeedImportConfig(user_id=1), now)  # jamais synchronisé
    recent = FeedImportConfig(user_id=1, interval_minutes=60, last_sync=now - timedelta(minutes=30))
    assert not fi._is_due(recent, now)
    assert fi._is_due(recent, now + timedelta(minutes=30))
    assert fi._interval(1) == fi.MIN_INTERVAL and fi._interval(10**6) == fi.MAX_INTERVAL


def test_sync_error_is_recorded_on_the_feed(session, env, monkeypatch):
    _user, config = env

    async def _fail(url):
        raise RuntimeError("Feed returned HTTP 403")

    monkeypatch.setattr(fi, "_fetch_feed", _fail)
    with pytest.raises(RuntimeError):
        asyncio.run(fi._sync_recording_error(config, session))
    session.refresh(config)
    assert config.last_error == "Feed returned HTTP 403" and config.last_sync is not None


@pytest.fixture
def web(engine, monkeypatch):
    import re
    from fastapi.testclient import TestClient
    from sqlmodel import Session
    from app.auth import get_current_user
    from app.database import get_session
    from app.main import app

    with Session(engine) as s:
        me, other = User(oidc_sub="me", public_slug="me"), User(oidc_sub="other", public_slug="other")
        s.add(me), s.add(other)
        s.commit()
        s.add(FeedImportConfig(user_id=me.id, feed_url=encrypt("https://a.example/rss"), group_name="Veille"))
        s.add(FeedImportConfig(user_id=other.id, feed_url=encrypt("https://b.example/rss"), group_name="Autre"))
        s.commit()
        uid = me.id

    def _get_session():
        with Session(engine) as s:
            yield s

    def _get_user():
        with Session(engine) as s:
            u = s.get(User, uid)
            s.expunge(u)
            return u

    async def _public(url):
        return True

    monkeypatch.setattr(fi, "_assert_public_url", _public)
    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[get_current_user] = _get_user
    client = TestClient(app, base_url="https://testserver")  # cookie de session Secure
    page = client.get("/settings/feed-import")
    client.csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    yield client, engine, uid
    app.dependency_overrides.clear()


def test_several_feeds_per_account_and_ownership(web):
    from sqlmodel import Session
    client, engine, uid = web
    r = client.post("/settings/feed-import", data={
        "csrf_token": client.csrf, "feed_url": "https://c.example/atom", "group_name": "Blog",
        "interval_minutes": "2"}, follow_redirects=False)
    assert r.status_code == 303
    with Session(engine) as s:
        feeds = s.exec(select(FeedImportConfig).order_by(FeedImportConfig.id)).all()
        mine = [f for f in feeds if f.user_id == uid]
        other_id = next(f.id for f in feeds if f.user_id != uid)
    assert [f.group_name for f in mine] == ["Veille", "Blog"]
    assert mine[1].interval_minutes == fi.MIN_INTERVAL and mine[1].is_enabled

    page = client.get("/settings/feed-import").text
    assert "Veille" in page and "Blog" in page and "Autre" not in page
    assert "c.example" not in page  # l'URL (et son jeton) n'est jamais réaffichée

    for path in (f"/settings/feed-import/{other_id}", f"/settings/feed-import/{other_id}/delete"):
        assert client.post(path, data={"csrf_token": client.csrf}, follow_redirects=False).status_code == 404

    assert client.post(f"/settings/feed-import/{mine[1].id}/delete", data={"csrf_token": client.csrf},
                       follow_redirects=False).status_code == 303
    with Session(engine) as s:
        assert s.exec(select(FeedImportConfig).where(FeedImportConfig.user_id == uid)).all()[0].group_name == "Veille"
