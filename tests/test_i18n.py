"""Socle i18n : isolation de la locale entre requêtes concurrentes, cascade
de résolution, repli sur les msgid anglais.

Le test central est `test_concurrent_tasks_do_not_share_locale` : c'est le bug
que l'architecture existe pour empêcher (voir docs/i18n.md). Une implémentation
utilisant gettext.install() ou un catalogue figé sur l'environnement Jinja le
ferait échouer.
"""
import asyncio

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from starlette.requests import Request

from app import i18n

# Traductions distinctes par langue, pour qu'une contamination soit visible.
_FIXTURES = {
    "fr": {"Add link": "Ajouter un lien"},
    "es": {"Add link": "Anadir enlace"},
    "de": {"Add link": "Link hinzufugen"},
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    """Répertoire de catalogues compilés, isolé du répertoire de production."""
    for locale, messages in _FIXTURES.items():
        catalog = Catalog(locale=locale)
        for msgid, msgstr in messages.items():
            catalog.add(msgid, msgstr)
        lc_dir = tmp_path / locale / "LC_MESSAGES"
        lc_dir.mkdir(parents=True)
        with open(lc_dir / f"{i18n.DOMAIN}.mo", "wb") as fh:
            write_mo(fh, catalog)
        # available_locales() se fonde sur le .po, pas sur le .mo.
        (lc_dir / f"{i18n.DOMAIN}.po").write_text("")

    monkeypatch.setattr(i18n, "TRANSLATIONS_DIR", tmp_path)
    i18n.clear_cache()
    yield tmp_path
    i18n.clear_cache()


def _request(headers=None, cookies=None, session=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw.append((b"cookie", cookie.encode()))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": raw}
    if session is not None:
        scope["session"] = session
    return Request(scope)


# --------------------------------------------------------------------------
# Le test qui justifie l'architecture
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_concurrent_tasks_do_not_share_locale(catalogs):
    """Des requêtes simultanées dans des langues différentes ne se contaminent pas.

    Chaque tâche pose sa locale, rend la main à la boucle d'événements pendant
    que les autres posent la leur, puis traduit. Avec un état global, la
    dernière locale posée gagnerait pour tout le monde.
    """
    async def handle(locale, expected):
        token = i18n.set_locale(locale)
        try:
            # Laisse toutes les autres tâches s'intercaler avant de traduire.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert i18n.get_locale() == locale
            return i18n.gettext("Add link"), expected
        finally:
            i18n.reset_locale(token)

    # Plusieurs tâches par langue, pour que l'ordre d'entrelacement varie.
    plan = [
        ("fr", "Ajouter un lien"),
        ("es", "Anadir enlace"),
        ("de", "Link hinzufugen"),
        ("en", "Add link"),  # pas de catalogue : repli sur le msgid
    ] * 8

    results = await asyncio.gather(*(handle(loc, exp) for loc, exp in plan))

    for got, expected in results:
        assert got == expected


@pytest.mark.anyio
async def test_locale_is_restored_after_each_task(catalogs):
    """Le contexte revient à son état antérieur, y compris si la tâche échoue."""
    assert i18n.get_locale() == i18n.DEFAULT_LOCALE

    token = i18n.set_locale("fr")
    try:
        assert i18n.get_locale() == "fr"
    finally:
        i18n.reset_locale(token)

    assert i18n.get_locale() == i18n.DEFAULT_LOCALE


@pytest.mark.anyio
async def test_middleware_resets_locale_when_app_raises(catalogs):
    """Une exception dans la suite de la pile ne laisse pas la locale posée."""
    async def failing_app(scope, receive, send):
        assert i18n.get_locale() == "fr"
        raise RuntimeError("boom")

    middleware = i18n.LocaleMiddleware(failing_app)
    scope = {
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"cookie", b"lang=fr")],
    }

    with pytest.raises(RuntimeError):
        await middleware(scope, None, None)

    assert i18n.get_locale() == i18n.DEFAULT_LOCALE


# --------------------------------------------------------------------------
# Traduction et repli
# --------------------------------------------------------------------------

def test_unknown_locale_falls_back_to_msgid(catalogs):
    """Une locale sans catalogue affiche l'anglais du msgid, pas une clé."""
    token = i18n.set_locale("pl")
    try:
        assert i18n.gettext("Add link") == "Add link"
    finally:
        i18n.reset_locale(token)


def test_untranslated_string_falls_back_to_msgid(catalogs):
    token = i18n.set_locale("fr")
    try:
        assert i18n.gettext("Not in the catalog") == "Not in the catalog"
    finally:
        i18n.reset_locale(token)


def test_available_locales_always_offers_default_first(catalogs):
    """`en` n'a pas de catalogue (msgid == anglais) mais reste proposable."""
    locales = i18n.available_locales()
    assert locales[0] == i18n.DEFAULT_LOCALE
    assert set(locales) == {"en", "fr", "es", "de"}


# --------------------------------------------------------------------------
# Négociation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "header,expected",
    [
        ("fr-FR,fr;q=0.9,en;q=0.8", ["fr_FR", "fr", "en"]),
        # Le tri respecte les q, pas l'ordre d'écriture.
        ("en;q=0.5,fr;q=0.9", ["fr", "en"]),
        # q identiques : l'ordre du client départage.
        ("de,fr", ["de", "fr"]),
        ("fr;q=0", []),          # explicitement refusé
        ("*", []),               # joker : aucune préférence exploitable
        ("fr;q=oops", ["fr"]),   # q illisible : ne fait pas échouer la requête
        ("", []),
        (None, []),
    ],
)
def test_parse_accept_language(header, expected):
    assert i18n.parse_accept_language(header) == expected


@pytest.mark.parametrize(
    "preferred,available,expected",
    [
        (["fr"], ["en", "fr"], "fr"),
        (["fr_FR"], ["en", "fr"], "fr"),      # région demandée, langue dispo
        (["fr"], ["en", "fr_CA"], "fr_CA"),   # langue demandée, région dispo
        (["pl", "fr"], ["en", "fr"], "fr"),   # premier choix indisponible
        (["pl"], ["en", "fr"], None),
        ([""], ["en", "fr"], None),
    ],
)
def test_negotiate(preferred, available, expected):
    assert i18n.negotiate(preferred, available) == expected


# --------------------------------------------------------------------------
# Cascade de résolution
# --------------------------------------------------------------------------

def test_session_preference_wins_over_everything(catalogs):
    request = _request(
        headers={"accept-language": "de"},
        cookies={"lang": "es"},
        session={"lang": "fr"},
    )
    assert i18n.resolve_locale(request) == "fr"


def test_cookie_wins_over_accept_language(catalogs):
    request = _request(headers={"accept-language": "de"}, cookies={"lang": "es"})
    assert i18n.resolve_locale(request) == "es"


def test_accept_language_used_when_no_preference_stored(catalogs):
    request = _request(headers={"accept-language": "de-DE,de;q=0.9"})
    assert i18n.resolve_locale(request) == "de"


def test_falls_back_to_default_when_nothing_matches(catalogs):
    request = _request(headers={"accept-language": "pl,ru;q=0.8"})
    assert i18n.resolve_locale(request) == i18n.DEFAULT_LOCALE


def test_unavailable_stored_preference_does_not_block_negotiation(catalogs):
    """Une préférence devenue invalide (catalogue retiré) ne fige pas la langue."""
    request = _request(headers={"accept-language": "de"}, session={"lang": "pl"})
    assert i18n.resolve_locale(request) == "de"


def test_no_signal_at_all_gives_default(catalogs):
    assert i18n.resolve_locale(_request()) == i18n.DEFAULT_LOCALE
