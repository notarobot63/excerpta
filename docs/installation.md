# Installation

## Requirements

- Docker and Docker Compose
- An OIDC provider (PocketID, Authentik, Keycloak, etc.)

## Quick start

### Option A - pre-built image

```bash
curl -O https://raw.githubusercontent.com/notarobot63/excerpta/main/docker-compose.prod.yml
curl -O https://raw.githubusercontent.com/notarobot63/excerpta/main/.env.example
cp .env.example .env
# Edit .env
docker compose -f docker-compose.prod.yml up -d
```

### Option B - build locally

```bash
git clone https://github.com/notarobot63/excerpta.git
cd excerpta
cp .env.example .env
# Edit .env
GIT_COMMIT=$(git describe --tags --always | sed 's/^v//') docker compose up --build -d
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Session secret key - `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `BASE_URL` | Yes | Full public URL (e.g. `https://links.example.com`) |
| `OIDC_CLIENT_ID` | Yes | OIDC application client ID |
| `OIDC_CLIENT_SECRET` | Yes | OIDC application client secret |
| `OIDC_ISSUER` | Yes | OIDC issuer URL (e.g. `https://auth.example.com`) |
| `FRESHRSS_SYNC_INTERVAL` | No | FreshRSS sync interval in minutes (default: 30) |

## Reverse proxy

Excerpta listens on port `8070` (configurable in the compose file). Caddy example:

```
links.example.com {
    reverse_proxy localhost:8070
}
```

Nginx example:

```nginx
server {
    listen 443 ssl;
    server_name links.example.com;

    location / {
        proxy_pass http://localhost:8070;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> The `X-Forwarded-For` header is required for rate limiting to work correctly per client IP.

## Updating

```bash
# Pre-built image
docker compose -f docker-compose.prod.yml pull && \
  docker compose -f docker-compose.prod.yml up -d

# Local build
git pull && GIT_COMMIT=$(git describe --tags --always | sed 's/^v//') docker compose up --build -d
```

### Migrating to the unprivileged image (one-off)

The image no longer runs as `root` but under UID `10001`. The volume of an
instance created before this change still belongs to `root`: without taking
ownership, the application starts and then exits on
`attempt to write a readonly database`. With the container stopped:

```bash
docker compose -f docker-compose.prod.yml down
docker run --rm -v excerpta_data:/data alpine chown -R 10001:0 /data
docker compose -f docker-compose.prod.yml up -d
```

A fresh instance gets the right ownership from the start, nothing to do.

## Persistent data

Data (SQLite database) is stored in a named Docker volume, `excerpta_data`. To back it up:

```bash
docker run --rm \
  -v excerpta_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/excerpta-backup.tar.gz /data
```
