<p align="center">
  <img src="app/static/logo_full.svg" alt="Excerpta" width="380"/>
</p>

# Excerpta

**Gestionnaire de liens self-hosted** — collecte, annote, retrouve tes liens depuis n'importe quel appareil.

> collect • annotate • remember

## Fonctionnalités

- **CRUD complet** : titre, description, note Markdown, tags, groupes hiérarchiques, visibilité publique/privée
- **Recherche full-text** (SQLite FTS5) sur titres, descriptions, notes et URLs
- **Groupes hiérarchiques** avec filtrage récursif (sous-groupes inclus)
- **Authentification OIDC/PKCE** via PocketID
- **Import/Export** de favoris au format Netscape HTML
- **Vérificateur de liens cassés** (async, 10 vérifications en parallèle)
- **Archivage Internet Archive** : sauvegarde et stockage de l'URL archivée
- **Pages publiques** par lien (partage sans authentification)
- **Panel admin** : gestion des utilisateurs, statistiques, régénération de clés API
- **API REST v1** complète (Bearer API Key)
- **Application Android** : QR code de configuration intégré (`GET /settings/android-qr.png`)
- **9 thèmes CSS** : light, dark, dracula, nord, nord-dark, catppuccin, gruvbox, solarized, rosepine
- **Bookmarklet** navigateur pour ajout rapide
- **Synchronisation FreshRSS** : import automatique des articles étoilés (toutes les 30 min)

## Stack

| Composant | Technologie |
|-----------|-------------|
| Backend | FastAPI + SQLModel |
| Base de données | SQLite (mode WAL) |
| Templates | Jinja2 + Alpine.js (local, sans CDN) |
| Auth | OIDC/PKCE (PocketID) |
| Sécurité | CSRF, rate limiting, SSRF blacklist, CSP headers |

## Déploiement

```bash
cp .env.example .env
# éditer .env (OIDC_*, SECRET_KEY, BASE_URL...)

docker compose up --build -d
```

> Après chaque modification de templates ou fichiers statiques, relancer avec `--build`.

## API REST

L'API est accessible via une clé API (visible dans Paramètres → Compte) :

```http
GET /api/v1/links
X-API-Key: <api_key>
```

Endpoints disponibles : `GET /me`, `GET/POST /links`, `PATCH/DELETE /links/{id}`, `GET /tags`, `GET /groups`.

## Application Android

L'application compagnon **excerpta-android** se configure en scannant le QR code disponible dans Paramètres → Compte.

## CI/CD (Gitea Actions)

Le workflow `.gitea/workflows/deploy.yml` utilise des variables de dépôt (`vars.*`) et des secrets (`secrets.*`) à configurer dans Gitea.

**Variables (`vars.*`)** :

| Variable | Description | Exemple |
|---|---|---|
| `REGISTRY_URL` | Hôte du registry Docker | `git.example.com` |
| `REGISTRY_USER` | Utilisateur du registry | `monuser` |
| `DEPLOY_PATH` | Chemin de déploiement sur le serveur | `/srv/excerpta` |
| `DEPLOY_PORT` | Port d'écoute du conteneur | `8070` |
| `NTFY_URL` | URL complète du topic ntfy | `https://ntfy.example.com/topic` |

**Secrets (`secrets.*`)** :

| Secret | Description |
|---|---|
| `REGISTRY_TOKEN` | Token d'accès au registry (lecture + écriture) |
| `NTFY_TOKEN` | Token d'authentification ntfy |

> Le runner doit être configuré avec `runs-on: host` et avoir accès à Docker et SSH.

## Licence

[GNU Affero General Public License v3.0](LICENSE) — fork libre, copyleft fort, attribution obligatoire.
