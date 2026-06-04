# Contribuer

## Stack

| Couche | Technologie |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLModel |
| Base de données | SQLite (WAL + FTS5) |
| Templates | Jinja2 |
| JS | Alpine.js (v3, servi localement) |
| CSS | Vanilla CSS, variables de thème |

## Environnement de développement

```bash
git clone https://GIT_HOST/Thomas/excerpta.git
cd excerpta
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Éditer .env
uvicorn app.main:app --reload
```

L'application est disponible sur `http://localhost:8000`.

## Structure

```
app/
├── main.py          — point d'entrée FastAPI, lifespan, middlewares
├── models.py        — modèles SQLModel (Link, Tag, Folder, User, FreshRSSConfig)
├── database.py      — initialisation SQLite, migrations idempotentes, FTS
├── auth.py          — dépendance get_current_user
├── config.py        — variables d'environnement (pydantic Settings)
├── crypto.py        — chiffrement Fernet + HMAC pour tokens/clés API
├── csrf.py          — protection CSRF (header + form token)
├── ratelimit.py     — rate limiting par IP
├── utils.py         — fonctions partagées (sidebar_data, FTS, dossiers)
├── routes/
│   ├── links.py     — CRUD liens, drag&drop, archivage, métadonnées
│   ├── tags.py      — gestion tags (rename avec fusion, delete)
│   ├── folders.py   — gestion dossiers hiérarchiques
│   ├── freshrss.py  — sync FreshRSS, unstar GReader
│   ├── api.py       — API REST v1
│   ├── settings.py  — paramètres, import/export, vérification liens
│   ├── admin.py     — panel administrateur
│   └── auth.py      — login OIDC/PKCE, logout
├── templates/       — Jinja2 (base.html + une sous-structure par domaine)
└── static/          — CSS, JS, SVG (servis localement, sans CDN)
```

## Migrations base de données

Les migrations sont idempotentes et s'exécutent dans `database.py:init_db()` au démarrage. Pour ajouter une colonne :

```python
cols = {r[1] for r in con.execute("PRAGMA table_info(ma_table)").fetchall()}
if "nouvelle_colonne" not in cols:
    con.execute("ALTER TABLE ma_table ADD COLUMN nouvelle_colonne TEXT")
```

## Sécurité

Avant de soumettre une contribution :

- Pas de secret en clair dans le code ou les templates
- Les URL externes passent par `_safe_url()` (blacklist SSRF)
- Les formulaires POST incluent le token CSRF (`{{ csrf_input(request) }}`)
- Les requêtes JSON utilisent le header `X-CSRF-Token`
- Toute nouvelle route authentifiée utilise `Depends(get_current_user)`

## Signaler un problème

Ouvrir une issue sur le dépôt avec une description précise du comportement observé et des étapes pour le reproduire.
