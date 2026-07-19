# Android app

The **excerpta-android** app lets you save links directly from the Android share menu.

## Features

- Share a URL from any Android app
- Add a title, note and tags before sending
- API key authentication (stored locally)

## Installation

> Available at [notarobot63/excerpta-android](https://github.com/notarobot63/excerpta-android), with pre-built signed APK releases.

## Configuration

1. In Excerpta, go to **Settings → Account**
2. Scan the QR code with the Android app

The QR code encodes the instance URL and the API key. It's also directly available via:

```
GET /settings/android-qr.png
X-API-Key: <key>
```

## Manual configuration

If scanning isn't possible, enter manually in the app:

- **Instance URL**: `https://your-instance.example.com`
- **API key**: available in Settings → Account
