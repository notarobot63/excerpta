import os
from pathlib import Path
from urllib.parse import urlparse, urlencode
from babel.core import Locale, UnknownLocaleError
from babel.dates import format_date, format_datetime
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
import mistune

from . import i18n
from .config import settings
from .csrf import csrf_input

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# i18n : les callables sont résolues à CHAQUE appel et lisent la locale dans le
# ContextVar de la requête en cours. Ne jamais remplacer par
# install_gettext_translations() avec un catalogue figé : deux requêtes
# concurrentes de langues différentes se contamineraient. Voir docs/i18n.md.
templates.env.add_extension("jinja2.ext.i18n")
templates.env.install_gettext_callables(
    gettext=i18n.gettext, ngettext=i18n.ngettext, newstyle=True
)
templates.env.globals["current_locale"] = i18n.get_locale
templates.env.globals["available_locales"] = i18n.available_locales


def _locale_name(code: str) -> str:
    """Nom de la langue dans cette langue : « français », « Deutsch ».

    Un anglophone ne cherche pas « French » dans un sélecteur, il cherche
    « Français ». Le repli sur le code brut évite qu'une locale exotique fasse
    disparaître l'entrée du sélecteur.
    """
    try:
        return Locale.parse(code).get_display_name(code) or code
    except (UnknownLocaleError, ValueError):
        return code


templates.env.globals["locale_name"] = _locale_name


def _localedate(value, fmt: str = "medium") -> str:
    """Date écrite selon la locale courante : 26/07/2026 en français,
    Jul 26, 2026 en anglais. Un `strftime` codé en dur imposerait l'ordre
    jour/mois français à toutes les langues."""
    if not value:
        return ""
    try:
        return format_date(value, format=fmt, locale=i18n.get_locale())
    except (UnknownLocaleError, ValueError):
        return format_date(value, format=fmt, locale=i18n.DEFAULT_LOCALE)


def _localedatetime(value, fmt: str = "short") -> str:
    """Date et heure selon la locale courante."""
    if not value:
        return ""
    try:
        return format_datetime(value, format=fmt, locale=i18n.get_locale())
    except (UnknownLocaleError, ValueError):
        return format_datetime(value, format=fmt, locale=i18n.DEFAULT_LOCALE)


templates.env.filters["localedate"] = _localedate
templates.env.filters["localedatetime"] = _localedatetime
_md = mistune.create_markdown(escape=True)
templates.env.filters["markdown"] = lambda text: Markup(_md(text or ""))
templates.env.filters["domain"] = lambda url: urlparse(url).netloc or url

_PLACEHOLDER_COLORS = [
    "#2e86ab", "#a23b72", "#f18f01", "#c73e1d", "#3b1f2b",
    "#44bba4", "#e94f37", "#393e41", "#6b4226", "#7b2d8b",
    "#2d6a4f", "#e76f51", "#457b9d", "#6a0572", "#0077b6",
]

def _domain_color(url: str) -> str:
    host = urlparse(url).netloc or url
    host = host.removeprefix("www.")
    idx = sum(ord(c) for c in host) % len(_PLACEHOLDER_COLORS)
    return _PLACEHOLDER_COLORS[idx]

def _domain_initial(url: str) -> str:
    host = urlparse(url).netloc or url
    host = host.removeprefix("www.")
    return host[0].upper() if host else "?"

def _proxy_img(url: str) -> str:
    if not url:
        return ""
    return "/proxy/img?" + urlencode({"url": url})

templates.env.filters["domain_color"] = _domain_color
templates.env.filters["domain_initial"] = _domain_initial
templates.env.filters["proxy_img"] = _proxy_img
templates.env.globals["csrf_input"] = csrf_input
# Le mode démo change ce que l'interface propose : le formulaire d'ajout libre
# est remplacé par le catalogue fermé. Exposé en fonction plutôt qu'en valeur
# pour rester juste si le réglage est modifié à chaud (tests).
templates.env.globals["demo_mode"] = lambda: settings.demo_mode
# Version affichée dans l'UI : le tag SemVer quand l'image est construite depuis
# un tag (v1.2.0 -> "1.2.0"), sinon le SHA court du commit. Renseignée par le
# build-arg APP_VERSION du Dockerfile.
templates.env.globals["app_version"] = os.getenv("APP_VERSION", "dev")

# Cache-busting des assets : version = mtime max des fichiers statiques.
# Change uniquement quand un asset change → cache navigateur 1 an immuable
# tout en garantissant la fraîcheur après un déploiement.
def _compute_static_version() -> str:
    static_dir = Path(__file__).parent / "static"
    try:
        latest = max(
            (f.stat().st_mtime for f in static_dir.glob("*") if f.is_file()),
            default=0.0,
        )
        return str(int(latest))
    except OSError:
        return "1"

templates.env.globals["static_version"] = _compute_static_version()
