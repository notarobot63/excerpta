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
- **Authentification OIDC/PKCE** (PocketID, Keycloak, Authentik, etc.)
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
| Auth | OIDC/PKCE |
| Sécurité | CSRF, rate limiting, SSRF blacklist, CSP headers |

## Déploiement

```bash
cp .env.example .env
# éditer .env
docker compose up --build -d
```

> Après chaque modification de templates ou fichiers statiques, relancer avec `--build`.

### Variables d'environnement

| Variable | Description |
|---|---|
| `SECRET_KEY` | Clé secrète Flask — générer avec `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | URL publique de l'instance (ex. `https://liens.example.com`) |
| `OIDC_CLIENT_ID` | Client ID de l'application OIDC |
| `OIDC_CLIENT_SECRET` | Client secret de l'application OIDC |
| `OIDC_ISSUER` | URL de l'issuer OIDC (ex. `https://auth.example.com`) |
| `FRESHRSS_SYNC_INTERVAL` | Intervalle de sync FreshRSS en minutes (défaut : 30) |

## API REST

L'API est accessible via une clé API (visible dans Paramètres → Compte) :

```http
GET /api/v1/links
X-API-Key: <api_key>
```

Endpoints disponibles : `GET /me`, `GET/POST /links`, `PATCH/DELETE /links/{id}`, `GET /tags`, `GET /groups`.

## Application Android

L'application compagnon **excerpta-android** se configure en scannant le QR code disponible dans Paramètres → Compte.

## CI/CD

Un workflow Gitea Actions est fourni dans `.gitea/workflows/deploy.yml`. Il s'appuie sur les variables d'environnement suivantes, à injecter dans le runner (`runner.envs` dans `config.yaml`) ou via les variables du dépôt :

| Variable | Description |
|---|---|
| `REGISTRY_URL` | Hôte du registry Docker |
| `REGISTRY_USER` | Utilisateur du registry |
| `DEPLOY_PATH` | Chemin de déploiement sur le serveur |
| `DEPLOY_PORT` | Port d'écoute du conteneur |
| `NTFY_URL` | URL complète du topic ntfy |

Secrets requis : `REGISTRY_TOKEN`, `NTFY_TOKEN`.

## Licence

[GNU Affero General Public License v3.0](LICENSE) — fork libre, copyleft fort, attribution obligatoire.
