# Installation

## Prérequis

- Docker et Docker Compose
- Un fournisseur OIDC (PocketID, Authentik, Keycloak, etc.)

## Démarrage rapide

### Option A — image pré-buildée

```bash
curl -O https://raw.githubusercontent.com/notarobot63/excerpta/main/docker-compose.prod.yml
curl -O https://raw.githubusercontent.com/notarobot63/excerpta/main/.env.example
cp .env.example .env
# Éditer .env
REGISTRY_IMAGE=ghcr.io/notarobot63/excerpta:latest \
  docker compose -f docker-compose.prod.yml up -d
```

### Option B — build local

```bash
git clone https://github.com/notarobot63/excerpta.git
cd excerpta
cp .env.example .env
# Éditer .env
docker compose up --build -d
```

## Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `SECRET_KEY` | Oui | Clé secrète sessions — `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Oui | URL publique complète (ex. `https://liens.example.com`) |
| `OIDC_CLIENT_ID` | Oui | Client ID de l'application OIDC |
| `OIDC_CLIENT_SECRET` | Oui | Client secret de l'application OIDC |
| `OIDC_ISSUER` | Oui | URL de l'issuer OIDC (ex. `https://auth.example.com`) |
| `FRESHRSS_SYNC_INTERVAL` | Non | Intervalle de sync FreshRSS en minutes (défaut : 30) |

## Reverse proxy

Excerpta écoute sur le port `8070` (configurable dans le compose). Exemple de configuration Caddy :

```
liens.example.com {
    reverse_proxy localhost:8070
}
```

Exemple Nginx :

```nginx
server {
    listen 443 ssl;
    server_name liens.example.com;

    location / {
        proxy_pass http://localhost:8070;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> Le header `X-Forwarded-For` est nécessaire pour que le rate limiting fonctionne correctement par IP client.

## Mise à jour

```bash
# Image pré-buildée
REGISTRY_IMAGE=ghcr.io/notarobot63/excerpta:latest \
  docker compose -f docker-compose.prod.yml pull && \
  docker compose -f docker-compose.prod.yml up -d

# Build local
git pull && docker compose up --build -d
```

## Données persistantes

Les données (base SQLite) sont stockées dans un volume Docker nommé `excerpta_data`. Pour sauvegarder :

```bash
docker run --rm \
  -v excerpta_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/excerpta-backup.tar.gz /data
```
