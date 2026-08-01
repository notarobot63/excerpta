# REST API v1

## Authentication

All API requests require an API key in the header:

```
X-API-Key: <your-api-key>
```

The key is available in **Settings → Account**.

**Rate limit:** 60 requests / minute per key.

## Endpoints

### GET /api/v1/me

Returns the authenticated user's profile.

```bash
curl https://your-instance.example.com/api/v1/me \
  -H "X-API-Key: <key>"
```

```json
{ "id": 1, "name": "Thomas" }
```

---

### GET /api/v1/links

Lists links, paginated, with optional search and filters.

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `q` | string | Full-text search (title, description, note, URL, tags), accent-insensitive |
| `tag` | string | Filter by tag |
| `group_id` | int | Filter by folder (includes subfolders) |
| `unread` | bool | Only show unread links |
| `page` | int | Page (default: 1) |
| `per_page` | int | Results per page (default: 30, max: 100) |

```bash
curl "https://your-instance.example.com/api/v1/links?q=python&tag=dev&page=1" \
  -H "X-API-Key: <key>"
```

```json
{
  "links": [
    {
      "id": 42,
      "url": "https://example.com/article",
      "title": "Article title",
      "description": "Short description",
      "favicon_url": "https://...",
      "thumbnail_url": "https://...",
      "note": "My note in **Markdown**",
      "is_public": false,
      "is_read": false,
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

Creates a new link.

```bash
curl -X POST https://your-instance.example.com/api/v1/links \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "title": "Example", "tags": ["dev", "tool"]}'
```

**Body**

| Field | Type | Description |
|---|---|---|
| `url` | string | Link URL (required) |
| `title` | string | Title (default: URL) |
| `note` | string | Markdown note |
| `tags` | array | List of tags |
| `folder_id` | int | Folder ID (optional, ignored if invalid) |
| `is_public` | bool | Make the link public right away (default: false) |

```json
{ "id": 43, "url": "https://example.com", "title": "Example" }
```

---

### PATCH /api/v1/links/{id}

Updates an existing link.

```bash
curl -X PATCH https://your-instance.example.com/api/v1/links/42 \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'
```

**Body**

| Field | Type | Description |
|---|---|---|
| `is_public` | bool | Make the link public or private |
| `is_read` | bool | Mark the link as read or unread |

---

### DELETE /api/v1/links/{id}

Deletes a link. Returns `204 No Content`.

```bash
curl -X DELETE https://your-instance.example.com/api/v1/links/42 \
  -H "X-API-Key: <key>"
```

---

### GET /api/v1/tags

Lists all tags with their associated link count.

```bash
curl https://your-instance.example.com/api/v1/tags \
  -H "X-API-Key: <key>"
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

Returns the folder tree with their link counts.

```bash
curl https://your-instance.example.com/api/v1/folders \
  -H "X-API-Key: <key>"
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

Triggers a manual FreshRSS sync. Useful for an external cron.

```bash
curl -X POST https://your-instance.example.com/api/v1/freshrss/sync \
  -H "X-API-Key: <key>"
```

```json
{ "added": 3 }
```

---

### GET /public/feed.xml

Public RSS feed, no authentication required. Returns the 100 most recent public links in RSS 2.0 format.

```bash
curl https://your-instance.example.com/public/feed.xml
```

Also available via the `<link rel="alternate">` tag on the public page (auto-detected by RSS readers).

## Error codes

| Code | Meaning |
|---|---|
| 401 | Missing or invalid API key |
| 404 | Resource not found |
| 422 | Invalid data |
| 429 | Rate limit reached |
