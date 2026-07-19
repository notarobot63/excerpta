# Contributing

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLModel |
| Database | SQLite (WAL + FTS5) |
| Templates | Jinja2 |
| JS | Alpine.js (v3, served locally) |
| CSS | Vanilla CSS, theme variables |
| Reader extraction | readability-lxml + nh3 (HTML sanitization) |
| Tests | pytest (`tests/`, see `requirements-dev.txt`) |

## Development environment

```bash
git clone https://github.com/notarobot63/excerpta.git
cd excerpta
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env
uvicorn app.main:app --reload
```

The app is available at `http://localhost:8000`.

## Structure

```
app/
├── main.py          - FastAPI entry point, lifespan, middlewares
├── models.py        - SQLModel models (Link, Tag, Folder, User, FreshRSSConfig)
├── database.py      - SQLite init, idempotent migrations, FTS
├── auth.py          - get_current_user dependency
├── config.py        - environment variables (pydantic Settings)
├── crypto.py        - Fernet + HMAC encryption for tokens/API keys
├── csrf.py          - CSRF protection (header + form token)
├── ratelimit.py     - per-IP rate limiting
├── utils.py         - shared helpers (sidebar_data, FTS, folders)
├── routes/
│   ├── links.py     - link CRUD, drag&drop, live search, reader view, Wayback archiving, metadata
│   ├── tags.py      - tag management (rename with merge, delete)
│   ├── folders.py   - hierarchical folders (inline rename, A→Z sort, reorder)
│   ├── freshrss.py  - FreshRSS sync, GReader unstar
│   ├── api.py       - REST API v1
│   ├── settings.py  - settings, import/export, link checking, bulk archiving
│   ├── public.py    - per-user public page (/u/{slug}) + RSS feed
│   ├── admin.py     - admin panel
│   └── auth.py      - OIDC/PKCE login, logout
├── templates/       - Jinja2 (base.html + a sub-structure per domain;
│                      links/_results.html = fragment reused by AJAX search)
└── static/          - CSS, JS, SVG (served locally, no CDN)

tests/               - pytest (temporary SQLite DB + FTS, see conftest.py)
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The GitLab CI runs `pytest` at the `test` stage, which blocks the build and deployment on failure.

## Database migrations

Migrations are idempotent and run in `database.py:init_db()` at startup. To add a column:

```python
cols = {r[1] for r in con.execute("PRAGMA table_info(my_table)").fetchall()}
if "new_column" not in cols:
    con.execute("ALTER TABLE my_table ADD COLUMN new_column TEXT")
```

## Security

Before submitting a contribution:

- No secrets in plaintext in code or templates
- External URLs go through `_safe_url()` (SSRF blacklist)
- POST forms include the CSRF token (`{{ csrf_input(request) }}`)
- JSON requests use the `X-CSRF-Token` header
- Any new authenticated route uses `Depends(get_current_user)`

## Reporting an issue

Open an issue on the repository with a precise description of the observed behavior and steps to reproduce it.
