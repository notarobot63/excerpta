from pathlib import Path
from urllib.parse import urlparse
from fastapi.templating import Jinja2Templates
import mistune

from .csrf import csrf_input

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["markdown"] = lambda text: mistune.html(text or "", escape=True)
templates.env.filters["domain"] = lambda url: urlparse(url).netloc or url
templates.env.globals["csrf_input"] = csrf_input
