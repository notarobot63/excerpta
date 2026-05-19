from pathlib import Path
from urllib.parse import urlparse, urlencode
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
import mistune

from .csrf import csrf_input

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
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
    host = host.lstrip("www.")
    idx = sum(ord(c) for c in host) % len(_PLACEHOLDER_COLORS)
    return _PLACEHOLDER_COLORS[idx]

def _domain_initial(url: str) -> str:
    host = urlparse(url).netloc or url
    host = host.lstrip("www.")
    return host[0].upper() if host else "?"

def _proxy_img(url: str) -> str:
    if not url:
        return ""
    return "/proxy/img?" + urlencode({"url": url})

templates.env.filters["domain_color"] = _domain_color
templates.env.filters["domain_initial"] = _domain_initial
templates.env.filters["proxy_img"] = _proxy_img
templates.env.globals["csrf_input"] = csrf_input
