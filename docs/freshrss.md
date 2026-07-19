# FreshRSS integration

Excerpta can automatically sync starred articles from FreshRSS via the GReader API.

## Configuration

1. In FreshRSS, enable the GReader API: **Settings → Authentication → API access**
2. In Excerpta: **Settings → FreshRSS**

| Field | Description |
|---|---|
| FreshRSS URL | Instance URL (e.g. `https://rss.example.com`) |
| Username | FreshRSS login username |
| API password | GReader API password (can differ from the main password) |
| Target folder | Excerpta folder where imported articles are filed |
| Interval | Sync frequency in minutes (default: 30) |

The GReader token is encrypted (Fernet) before storage.

## How it works

On every sync, Excerpta fetches all starred articles and imports the ones not already present (deduplicated by URL). Imported articles are filed into the configured **target folder** - it's this folder, not a tag, that identifies them as coming from FreshRSS.

> Earlier versions added a `freshrss` tag to every imported article. This tag, redundant with the folder, has been removed: an idempotent migration automatically strips it from existing links at startup.

Sync runs:
- Automatically in the background at the configured interval
- Manually via the **Sync now** button in settings
- Via the REST API: `POST /api/v1/freshrss/sync` (authenticated by API key)

## Automatic unstarring

When you delete a link imported from FreshRSS, Excerpta offers to **unstar the article in FreshRSS** at the same time via a checkbox (checked by default).

If you uncheck the box, the link is deleted only in Excerpta - the star in FreshRSS is kept.

> Links imported before the version that introduced this feature automatically get their GReader ID back on the next sync.

## Moving a link out of the FreshRSS folder

Moving a link out of its **FreshRSS folder** (via drag & drop in the sidebar or by editing the link) **automatically unstars** it in FreshRSS: the article leaves your starred articles list. This is the logical counterpart of deletion.

Moving a link between two folders that are not the FreshRSS folder has no effect on FreshRSS.

> Self-healing: on every sync, any article still starred in FreshRSS but no longer in the FreshRSS folder on the Excerpta side gets unstarred (catches up on earlier moves and network failures).
