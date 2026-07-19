# Configuration

## OIDC / Authentication

Excerpta uses OIDC with the PKCE flow. Any compatible provider is supported.

### PocketID

1. Create an OIDC application in PocketID
2. Callback URL: `https://your-instance.example.com/auth/oidc/callback`
3. Set `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `OIDC_ISSUER` in `.env`

### Authentik

1. Create an OAuth2/OIDC Provider (type: Authorization Code + PKCE)
2. Callback URL: `https://your-instance.example.com/auth/oidc/callback`
3. `OIDC_ISSUER` = `https://auth.example.com/application/o/<slug>/`

### Keycloak

1. Create a Keycloak client with Access Type `public` and PKCE enabled
2. `OIDC_ISSUER` = `https://keycloak.example.com/realms/<realm>`

## Themes

9 palettes available, each with a light and dark variant. The light/dark toggle is accessible from the sidebar. The choice is remembered in the browser.

| Palette | Light | Dark |
|---|---|---|
| Default | Light | Dark |
| Nord | Nord | Nord Dark |
| Dracula | Dracula Light | Dracula |
| Catppuccin | Latte | Mocha |
| Gruvbox | Gruvbox Light | Gruvbox |
| Solarized | Solarized | Solarized Dark |
| Rosé Pine | Dawn | Moon |

The full selector is in **Settings → Appearance**.

## API key

The REST API key is generated automatically when the account is created. It is visible and can be regenerated in **Settings → Account**. It is stored as an HMAC - the plaintext value is never kept server-side.

## Bookmarklet

The bookmarklet is available in **Settings → Bookmarklet**. Dragging it to the browser's bookmarks bar lets you save the current page in one click.

## Import / Export

- **Import**: bookmarks in Netscape HTML format (Firefox, Chrome, Safari) - folders are recreated automatically
- **Export**: `Settings → Export` - generates a Netscape HTML file with folders and tags

## Keyboard shortcuts

| Key | Action |
|---|---|
| `n` | New link |
| `/` | Focus search |

Shortcuts are disabled while focus is in an input field.

## Duplicate detection

If you try to add a URL that's already in your collection (via the form or the bookmarklet), Excerpta automatically redirects to the existing link's edit page with a warning.

## Link checking

`Settings → Check links` runs an asynchronous check of all links (10 in parallel). Broken links (4xx, 5xx, timeout) are flagged with their HTTP status.

When a link is broken, its card directly offers a **recovery** option: *read the cached copy* (if the reader content has been cached) and/or *view the archive* (Wayback capture), instead of a dead link.

## Search

Search is **real-time**: results are filtered as you type (no reload), with a short debounce delay and a 2-character trigger threshold. Clearing the field shows all links again. Pagination also happens via AJAX and the URL stays shareable (the browser's back button works).

The full-text index (SQLite FTS5) covers **titles, descriptions, notes, URLs and tags**, and is **accent-insensitive**. Without JavaScript, the classic search form still works.

Results are ranked by relevance (**weighted bm25**): a term found in the title carries more weight than in tags, description, or URL. Searched terms are **highlighted** in displayed titles.

## Reader view

Every link has a **reader** icon that opens a clean, readable version of the article:

- Main content extraction via Readability (the same algorithm as Firefox's reading mode), followed by HTML sanitization (anti-XSS).
- Focused presentation: narrow reading column, refined typography, images preserved, estimated reading time.
- Adjustable font size (remembered) and light/dark theme inherited from the app.
- Extraction happens on first open and is then **cached**; subsequent opens are instant. Adding `?refresh=1` to the URL forces a new extraction.
- Pages that can't be extracted (paywall, purely JavaScript content) show a failure state with a link to the original.

## Archiving (Wayback Machine)

Excerpta archives links on the Internet Archive's Wayback Machine.

- **Automatic on add**: every new link is archived in the background (captured while the page is still alive). FreshRSS sync does not archive automatically, to avoid saturating Wayback's quotas.
- **Visible status** per link on its card: in progress, archived (the icon points to the Wayback capture), or failed (button to retry).
- **Bulk archiving**: `Settings → Archive unarchived` runs a throttled background job over all links not yet archived.

> Wayback strongly limits anonymous archiving: on a large batch, some links may fail (HTTP 429). Retrying only targets the links that are still not archived.

## Public page

Every user has a public page listing their public links:

- URL: `/u/{slug}` - the `slug` is customizable in **Settings → Public page**.
- Associated RSS feed: `/u/{slug}/feed.xml`.
- Only links marked public appear there; the page title is customizable.

## Administration

The admin panel (`/admin/`) is only accessible to accounts flagged `is_admin = true` in the database. It lets you:
- List and disable users
- View global statistics
- Regenerate API keys
