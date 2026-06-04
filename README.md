<p align="center">
  <img src="app/static/logo_full.svg" alt="Excerpta" width="380"/>
</p>

# Excerpta

**Gestionnaire de liens self-hosted** — collecte, annote et retrouve tes liens depuis n'importe quel appareil.

> collect • annotate • remember

---

## Fonctionnalités

### Gestion des liens
- **Métadonnées automatiques** : titre, description, thumbnail og:image et favicon extraits à la sauvegarde
- **Notes Markdown** par lien (rendu complet)
- **Tags** multiples par lien, avec renommage/suppression inline depuis la sidebar (fusion automatique si le nom cible existe déjà)
- **Dossiers hiérarchiques** : arborescence imbriquée, filtrage récursif des sous-dossiers
- **Drag & drop** : déplacer un lien vers un autre dossier depuis la sidebar, réordonner et reparenter les dossiers
- **Visibilité** : chaque lien peut être public ou privé
- **Archivage** : sauvegarde sur Internet Archive avec stockage de l'URL archivée

### Recherche et navigation
- **Recherche full-text** (SQLite FTS5) sur titres, descriptions, notes et URLs
- Filtrage par dossier, tag ou les deux combinés

### Import / Export
- **Import de favoris** Netscape HTML (Firefox, Chrome, Safari) — dossiers inclus
- **Export** au format Netscape HTML avec groupes
- **Vérificateur de liens cassés** (async, 10 vérifications en parallèle)
- **Rafraîchissement de métadonnées** avec progression en temps réel

### Intégrations
- **Synchronisation FreshRSS** : import automatique des articles étoilés toutes les N minutes (configurable)
- **Bookmarklet** navigateur pour ajout rapide depuis n'importe quelle page
- **Application Android** (excerpta-android) : QR code de configuration intégré (`GET /settings/android-qr.png`)

### Interface
- **9 thèmes CSS** : light, dark, dracula, nord, nord-dark, catppuccin, gruvbox, solarized, rosepine — toggle light/dark en un clic
- Thumbnails og:image avec proxy côté serveur (contourne les restrictions CORP/ORB de Firefox)
- Placeholder visuel coloré pour les liens sans thumbnail
- Responsive — adapté mobile et desktop

### Authentification et sécurité
- **OIDC/PKCE** (PocketID, Keycloak, Authentik, tout fournisseur compatible)
- CSRF, rate limiting par IP réelle (derrière reverse proxy), SSRF blacklist avec pré-résolution DNS
- Content Security Policy avec nonce, headers de sécurité
- Chiffrement API key et tokens FreshRSS (Fernet + HMAC)

### Administration
- **Panel admin** : gestion des utilisateurs, statistiques, régénération des clés API
- **API REST v1** complète (Bearer API Key) : `GET /me`, `GET/POST /links`, `PATCH/DELETE /links/{id}`, `GET /tags`, `GET /folders`
- Version de l'application injectée au build (hash git)

---

## Stack

| Composant | Technologie |
|-----------|-------------|
| Backend | FastAPI + SQLModel |
| Base de données | SQLite (mode WAL, FTS5, index optimisés) |
| Templates | Jinja2 + Alpine.js (servi localement, sans CDN) |
| Auth | OIDC/PKCE |
| Conteneur | Docker |

---

## Déploiement

### Prérequis
- Docker et Docker Compose

### Démarrage rapide

**Option A — image pré-buildée (recommandé)**

```bash
cp .env.example .env
# Éditer .env avec tes valeurs

curl -O https://GIT_HOST/Thomas/excerpta/-/raw/main/docker-compose.prod.yml
REGISTRY_IMAGE=REGISTRY_HOST/thomas/excerpta:latest docker compose -f docker-compose.prod.yml up -d
```

**Option B — build local**

```bash
git clone https://GIT_HOST/Thomas/excerpta.git
cd excerpta
cp .env.example .env
# Éditer .env avec tes valeurs
docker compose up --build -d
```

### Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `SECRET_KEY` | Oui | Clé secrète — `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Oui | URL publique de l'instance (ex. `https://liens.example.com`) |
| `OIDC_CLIENT_ID` | Oui | Client ID de l'application OIDC |
| `OIDC_CLIENT_SECRET` | Oui | Client secret de l'application OIDC |
| `OIDC_ISSUER` | Oui | URL de l'issuer OIDC (ex. `https://auth.example.com`) |
| `FRESHRSS_SYNC_INTERVAL` | Non | Intervalle de sync FreshRSS en minutes (défaut : 30) |

### CI/CD (GitLab)

Le pipeline `.gitlab-ci.yml` fourni effectue :
1. **Build** : construit et pousse l'image Docker sur le registry
2. **Deploy** : pull la nouvelle image sur le serveur cible et relance le container

Variables requises dans GitLab CI/CD :

| Variable | Type | Description |
|---|---|---|
| `CI_REGISTRY` | Variable | URL du registry Docker |
| `CI_REGISTRY_USER` | Variable | Login registry |
| `CI_REGISTRY_PASSWORD` | Secret | Mot de passe registry |
| `DEPLOY_HOST` | Variable | Hôte de déploiement |
| `DEPLOY_PATH` | Variable | Chemin du docker-compose sur le serveur |
| `DEPLOY_PORT` | Variable | Port de l'application (pour le healthcheck) |
| `DEPLOY_SSH_KEY` | Secret (fichier) | Clé SSH privée pour le déploiement |
| `NTFY_URL` | Variable | URL de notification NTFY |
| `NTFY_TOKEN` | Secret | Token NTFY |

---

## API REST

Authentification via clé API (visible dans Paramètres → Compte) :

```http
GET /api/v1/links
X-API-Key: <api_key>
```

Endpoints :

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/me` | Profil utilisateur |
| `GET` | `/api/v1/links` | Liste paginée, filtre `?q=`, `?tag=`, `?folder_id=` |
| `POST` | `/api/v1/links` | Créer un lien |
| `PATCH` | `/api/v1/links/{id}` | Modifier un lien |
| `DELETE` | `/api/v1/links/{id}` | Supprimer un lien |
| `GET` | `/api/v1/tags` | Liste des tags avec compteurs |
| `GET` | `/api/v1/folders` | Arborescence des dossiers |

---

## Application Android

L'application compagnon **excerpta-android** permet l'ajout rapide de liens depuis le partage système Android. Elle se configure en scannant le QR code disponible dans **Paramètres → Compte**.

> Le dépôt excerpta-android n'est pas encore publié.

---

## Licence

[GNU Affero General Public License v3.0](LICENSE) — fork libre, copyleft fort, attribution obligatoire.
