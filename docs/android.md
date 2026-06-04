# Application Android

L'application **excerpta-android** permet d'enregistrer des liens directement depuis le menu de partage d'Android.

## Fonctionnalités

- Partage d'URL depuis n'importe quelle application Android
- Ajout de titre, note et tags avant l'envoi
- Authentification par clé API (stockée localement)

## Installation

> Le dépôt excerpta-android n'est pas encore publié publiquement. L'APK peut être compilé depuis les sources ou distribué directement.

## Configuration

1. Dans Excerpta, aller dans **Paramètres → Compte**
2. Scanner le QR code avec l'application Android

Le QR code encode l'URL de l'instance et la clé API. Il est également accessible directement via :

```
GET /settings/android-qr.png
X-API-Key: <clé>
```

## Configuration manuelle

Si le scan n'est pas possible, renseigner manuellement dans l'application :

- **URL de l'instance** : `https://votre-instance.example.com`
- **Clé API** : disponible dans Paramètres → Compte
