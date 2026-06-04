# Configuration

## OIDC / Authentification

Excerpta utilise OIDC avec le flux PKCE. Tout fournisseur compatible est supporté.

### PocketID

1. Créer une application OIDC dans PocketID
2. Callback URL : `https://votre-instance.example.com/auth/oidc/callback`
3. Renseigner `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` et `OIDC_ISSUER` dans `.env`

### Authentik

1. Créer un Provider OAuth2/OIDC (type : Authorization Code + PKCE)
2. Callback URL : `https://votre-instance.example.com/auth/oidc/callback`
3. `OIDC_ISSUER` = `https://auth.example.com/application/o/<slug>/`

### Keycloak

1. Créer un client Keycloak avec Access Type `public` et PKCE activé
2. `OIDC_ISSUER` = `https://keycloak.example.com/realms/<realm>`

## Thèmes

9 palettes disponibles, chacune en variante claire et sombre. Le toggle clair/sombre est accessible depuis la barre latérale. Le choix est mémorisé dans le navigateur.

| Palette | Clair | Sombre |
|---|---|---|
| Default | Light | Dark |
| Nord | Nord | Nord Dark |
| Dracula | Dracula Light | Dracula |
| Catppuccin | Latte | Mocha |
| Gruvbox | Gruvbox Light | Gruvbox |
| Solarized | Solarized | Solarized Dark |
| Rosé Pine | Dawn | Moon |

Le sélecteur complet est dans **Paramètres → Apparence**.

## Clé API

La clé API REST est générée automatiquement à la création du compte. Elle est visible et régénérable dans **Paramètres → Compte**. Elle est stockée sous forme de HMAC — la valeur en clair n'est jamais conservée côté serveur.

## Bookmarklet

Le bookmarklet est disponible dans **Paramètres → Bookmarklet**. Le glisser dans la barre de favoris du navigateur permet d'enregistrer la page courante en un clic.

## Import / Export

- **Import** : favoris au format Netscape HTML (Firefox, Chrome, Safari) — les dossiers sont recréés automatiquement
- **Export** : `Paramètres → Export` — génère un fichier Netscape HTML avec dossiers et tags

## Vérification des liens

`Paramètres → Vérifier les liens` lance une vérification asynchrone de tous les liens (10 en parallèle). Les liens cassés (4xx, 5xx, timeout) sont signalés avec leur statut HTTP.

## Administration

Le panel admin (`/admin/`) est accessible uniquement aux comptes marqués `is_admin = true` en base. Il permet de :
- Lister et désactiver des utilisateurs
- Consulter les statistiques globales
- Régénérer les clés API
