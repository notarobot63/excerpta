"""Internationalisation : résolution de la locale et traduction de l'interface.

Décision d'architecture complète : docs/i18n.md.

Règle centrale, dont tout le reste découle : **la locale est un état de requête,
pas un état d'application**. Elle est portée par un ContextVar, isolé par tâche
asyncio. On n'utilise donc ni `gettext.install()`, ni un objet `Translations`
attaché une fois pour toutes à l'environnement Jinja : sur une application async
servant des requêtes concurrentes, une requête en anglais serait rendue avec la
locale d'une requête française traitée en parallèle.

Les `msgid` sont en anglais. Une locale sans catalogue compilé retombe donc sur
le `msgid` lui-même, ce qui affiche de l'anglais correct plutôt qu'une clé.
C'est aussi pourquoi `en` n'a pas de catalogue : il serait une identité.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextvars import ContextVar, Token
from pathlib import Path

from babel.support import NullTranslations, Translations
from starlette.requests import Request

DEFAULT_LOCALE = "en"
DOMAIN = "messages"
TRANSLATIONS_DIR = Path(__file__).parent / "translations"

# Nom du cookie de préférence, pour les visiteurs non authentifiés (page de
# connexion et pages publiques /u/{slug}, qui n'ont pas d'utilisateur en session).
LOCALE_COOKIE = "lang"

_current_locale: ContextVar[str] = ContextVar("excerpta_locale", default=DEFAULT_LOCALE)

# Les catalogues sont immuables une fois chargés : les mettre en cache est sûr.
# C'est le CHOIX de la locale qui est propre à la requête, pas le catalogue.
# Clé = (répertoire, locale) pour que les tests puissent pointer un autre
# répertoire sans collision avec le cache de production.
_catalogs: dict[tuple[str, str], NullTranslations] = {}


# --------------------------------------------------------------------------
# Locales disponibles
# --------------------------------------------------------------------------

def available_locales() -> tuple[str, ...]:
    """Locales proposables, triées, DEFAULT_LOCALE toujours en tête.

    Une locale est disponible dès qu'un `messages.po` existe, même sans `.mo`
    compilé : en développement, les chaînes non compilées retombent sur les
    msgid anglais plutôt que de faire disparaître la langue du sélecteur.
    """
    found = set()
    try:
        for entry in TRANSLATIONS_DIR.iterdir():
            if (entry / "LC_MESSAGES" / f"{DOMAIN}.po").is_file():
                found.add(entry.name)
    except OSError:
        # Répertoire absent : seul l'anglais (les msgid) est servi.
        pass
    found.discard(DEFAULT_LOCALE)
    return (DEFAULT_LOCALE, *sorted(found))


def _catalog(locale: str) -> NullTranslations:
    key = (str(TRANSLATIONS_DIR), locale)
    cached = _catalogs.get(key)
    if cached is None:
        # Renvoie un NullTranslations si le .mo est absent : repli sur msgid.
        cached = Translations.load(TRANSLATIONS_DIR, [locale], domain=DOMAIN)
        _catalogs[key] = cached
    return cached


def clear_cache() -> None:
    """Vide le cache de catalogues. Réservé aux tests."""
    _catalogs.clear()


# --------------------------------------------------------------------------
# Locale courante (état de requête)
# --------------------------------------------------------------------------

def get_locale() -> str:
    return _current_locale.get()


def set_locale(locale: str) -> Token[str]:
    return _current_locale.set(locale)


def reset_locale(token: Token[str]) -> None:
    _current_locale.reset(token)


# --------------------------------------------------------------------------
# Traduction
# --------------------------------------------------------------------------

def gettext(message: str) -> str:
    return _catalog(get_locale()).gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Forme plurielle. Obligatoire dès qu'un compteur apparaît : certaines
    langues ont plus de deux formes, et « 1 lien » / « 5 liens » ne sont pas la
    même chaîne."""
    return _catalog(get_locale()).ngettext(singular, plural, n)


# --------------------------------------------------------------------------
# Négociation
# --------------------------------------------------------------------------

def parse_accept_language(header: str | None) -> list[str]:
    """Codes de langue d'un en-tête Accept-Language, du plus au moins souhaité.

    « fr-FR,fr;q=0.9,en;q=0.8 » -> ['fr_FR', 'fr', 'en']
    Les valeurs illisibles sont ignorées plutôt que de faire échouer la requête.
    """
    if not header:
        return []
    parsed: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        piece = part.strip()
        if not piece:
            continue
        code, _, params = piece.partition(";")
        code = code.strip().replace("-", "_")
        if not code or code == "*":
            continue
        quality = 1.0
        key, _, value = params.partition("=")
        if key.strip() == "q":
            try:
                quality = float(value)
            except ValueError:
                quality = 1.0
        if quality <= 0:
            continue
        # `index` départage à qualité égale, en respectant l'ordre du client.
        parsed.append((-quality, index, code))
    return [code for _, _, code in sorted(parsed)]


def negotiate(preferred: Iterable[str], available: Sequence[str]) -> str | None:
    """Première locale souhaitée qui a une correspondance parmi `available`.

    Correspondance exacte d'abord, puis sur la langue seule : « fr_FR » demandé
    accepte « fr » disponible, et « fr » demandé accepte « fr_CA » disponible.
    """
    by_code = {code.lower(): code for code in available}
    by_language: dict[str, str] = {}
    for code in available:
        by_language.setdefault(code.lower().split("_")[0], code)

    for want in preferred:
        want = want.strip().replace("-", "_").lower()
        if not want:
            continue
        if want in by_code:
            return by_code[want]
        language = want.split("_")[0]
        if language in by_language:
            return by_language[language]
    return None


def resolve_locale(request: Request) -> str:
    """Cascade de résolution, première valeur trouvée (voir docs/i18n.md) :

    1. préférence de l'utilisateur connecté, propagée en session ;
    2. cookie de préférence, pour les visiteurs non authentifiés ;
    3. en-tête Accept-Language du navigateur ;
    4. DEFAULT_LOCALE.
    """
    available = available_locales()

    # La session est lue depuis le scope : SessionMiddleware l'y a déposée, et
    # y accéder ne coûte pas la construction d'un objet Session.
    session = request.scope.get("session") or {}
    chosen = negotiate([session.get("lang") or ""], available)
    if chosen:
        return chosen

    chosen = negotiate([request.cookies.get(LOCALE_COOKIE) or ""], available)
    if chosen:
        return chosen

    chosen = negotiate(
        parse_accept_language(request.headers.get("accept-language")), available
    )
    return chosen or DEFAULT_LOCALE


# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------

class LocaleMiddleware:
    """Pose la locale de la requête pour toute sa durée.

    Middleware ASGI pur, et non un BaseHTTPMiddleware : celui-ci exécute la
    suite de la pile dans une tâche distincte, ce qui rend le raisonnement sur
    la propagation du ContextVar inutilement subtil. Ici, routes et templates
    partagent le contexte du middleware.

    Doit s'exécuter APRÈS SessionMiddleware pour lire la préférence en session.
    Starlette empilant les middlewares à l'envers (le dernier ajouté s'exécute
    en premier), cela veut dire l'ajouter AVANT lui dans app/main.py.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = set_locale(resolve_locale(Request(scope)))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_locale(token)
