# API REST v1

## Authentification

Toutes les requêtes API requièrent une clé API dans le header :

```
X-API-Key: <votre-clé-api>
```

La clé est disponible dans **Paramètres → Compte**.

**Rate limit :** 60 requêtes / minute par clé.

## Endpoints

### GET /api/v1/me

Retourne le profil de l'utilisateur authentifié.

```bash
curl https://votre-instance.example.com/api/v1/me \
  -H "X-API-Key: <clé>"
```

```json
{ "id": 1, "name": "Thomas" }
```

---

### GET /api/v1/links

Liste les liens, paginés, avec recherche et filtres optionnels.

**Paramètres query**

| Paramètre | Type | Description |
|---|---|---|
| `q` | string | Recherche full-text (titre, description, note, URL, tags), insensible aux accents |
| `tag` | string | Filtrer par tag |
| `group_id` | int | Filtrer par dossier (inclut les sous-dossiers) |
| `page` | int | Page (défaut : 1) |
| `per_page` | int | Résultats par page (défaut : 30, max : 100) |

```bash
curl "https://votre-instance.example.com/api/v1/links?q=python&tag=dev&page=1" \
  -H "X-API-Key: <clé>"
```

```json
{
  "links": [
    {
      "id": 42,
      "url": "https://example.com/article",
      "title": "Titre de l'article",
      "description": "Description courte",
      "favicon_url": "https://...",
      "thumbnail_url": "https://...",
      "note": "Ma note en **Markdown**",
      "is_public": false,
      "created_at": "2026-01-15T10:30:00",
      "tags": ["python", "dev"]
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 30,
  "total_pages": 1
}
```

---

### POST /api/v1/links

Crée un nouveau lien.

```bash
curl -X POST https://votre-instance.example.com/api/v1/links \
  -H "X-API-Key: <clé>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "title": "Exemple", "tags": ["dev", "tool"]}'
```

**Corps**

| Champ | Type | Description |
|---|---|---|
| `url` | string | URL du lien (obligatoire) |
| `title` | string | Titre (défaut : URL) |
| `note` | string | Note Markdown |
| `tags` | array | Liste de tags |
| `folder_id` | int | ID du dossier (optionnel, ignoré si invalide) |
| `is_public` | bool | Rendre le lien public dès la création (défaut : false) |

```json
{ "id": 43, "url": "https://example.com", "title": "Exemple" }
```

---

### PATCH /api/v1/links/{id}

Modifie un lien existant.

```bash
curl -X PATCH https://votre-instance.example.com/api/v1/links/42 \
  -H "X-API-Key: <clé>" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'
```

**Corps**

| Champ | Type | Description |
|---|---|---|
| `is_public` | bool | Rendre le lien public ou privé |

---

### DELETE /api/v1/links/{id}

Supprime un lien. Retourne `204 No Content`.

```bash
curl -X DELETE https://votre-instance.example.com/api/v1/links/42 \
  -H "X-API-Key: <clé>"
```

---

### GET /api/v1/tags

Liste tous les tags avec leur nombre de liens associés.

```bash
curl https://votre-instance.example.com/api/v1/tags \
  -H "X-API-Key: <clé>"
```

```json
{
  "tags": [
    { "name": "dev", "count": 12 },
    { "name": "python", "count": 5 }
  ]
}
```

---

### GET /api/v1/folders

Retourne l'arborescence des dossiers avec leur nombre de liens.

```bash
curl https://votre-instance.example.com/api/v1/folders \
  -H "X-API-Key: <clé>"
```

```json
{
  "folders": [
    { "id": 1, "name": "Dev", "parent_id": null, "depth": 0, "count": 8 },
    { "id": 3, "name": "Python", "parent_id": 1, "depth": 1, "count": 5 }
  ]
}
```

---

### POST /api/v1/freshrss/sync

Déclenche une sync FreshRSS manuelle. Utile pour un cron externe.

```bash
curl -X POST https://votre-instance.example.com/api/v1/freshrss/sync \
  -H "X-API-Key: <clé>"
```

```json
{ "added": 3 }
```

---

### GET /public/feed.xml

Flux RSS public, sans authentification. Retourne les 100 derniers liens publics au format RSS 2.0.

```bash
curl https://votre-instance.example.com/public/feed.xml
```

Disponible aussi via la balise `<link rel="alternate">` dans la page publique (détection automatique par les lecteurs RSS).

## Codes d'erreur

| Code | Signification |
|---|---|
| 401 | Clé API manquante ou invalide |
| 404 | Ressource introuvable |
| 422 | Données invalides |
| 429 | Rate limit atteint |
