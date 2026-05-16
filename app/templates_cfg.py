from pathlib import Path
from urllib.parse import urlparse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
import mistune

from .csrf import csrf_input

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Retourne un Markup → Jinja2 ne l'échappe pas, | safe inutile et supprimé dans les templates
templates.env.filters["markdown"] = lambda text: Markup(mistune.html(text or ""))
templates.env.filters["domain"] = lambda url: urlparse(url).netloc or url
templates.env.globals["csrf_input"] = csrf_input
