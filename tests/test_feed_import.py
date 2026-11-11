"""Import de flux RSS/Atom : analyse, dédoublonnage, dossier d'import, XML hostile."""
import asyncio

import pytest
from sqlmodel import select

import app.routes.feed_import as fi
from app.crypto import encrypt
from app.models import FeedImportConfig, Folder, Link, User

# Forme du flux privé « saved » de Reddit : le lien d'une entrée est la discussion.
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Un post</title>
    <link href="https://www.reddit.com/r/selfhosted/comments/abc/un_post/" />
    <content type="html">&lt;p&gt;Corps du post&lt;/p&gt;</content>
    <published>2026-11-01T10:00:00+00:00</published>
  </entry>
  <entry>
    <title>Un commentaire</title>
    <link rel="alternate" href="https://www.reddit.com/r/x/comments/def/t/c1/" />
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
        "https://www.reddit.com/r/selfhosted/comments/abc/un_post/",
        "https://www.reddit.com/r/x/comments/def/t/c1/",
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
    config = FeedImportConfig(user_id=user.id, feed_url=encrypt("https://www.reddit.com/saved.rss?feed=t"),
                              is_enabled=True)
    session.add(config)
    session.commit()

    async def _fetch(url):
        assert url == "https://www.reddit.com/saved.rss?feed=t"  # URL bien déchiffrée
        return fi.parse_feed(ATOM)

    monkeypatch.setattr(fi, "_fetch_feed", _fetch)
    monkeypatch.setattr(fi, "spawn", lambda coro, name=None: coro.close())
    return user, config


def test_sync_imports_into_folder_once(session, env):
    user, config = env
    # Déjà enregistré à la main : ni doublon, ni déplacement.
    session.add(Link(user_id=user.id, url="https://www.reddit.com/r/x/comments/def/t/c1/"))
    session.commit()

    assert asyncio.run(fi.sync_feed(config, session)) == 1
    assert asyncio.run(fi.sync_feed(config, session)) == 0

    folder = session.exec(select(Folder).where(Folder.user_id == user.id, Folder.name == "Reddit")).one()
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
        asyncio.run(fi._fetch_feed("https://www.reddit.com/saved.rss?feed=SECRET"))
    assert "SECRET" not in str(exc.value)
