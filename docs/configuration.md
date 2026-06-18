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

## Raccourcis clavier

| Touche | Action |
|---|---|
| `n` | Nouveau lien |
| `/` | Focus sur la recherche |

Les raccourcis sont désactivés quand le focus est dans un champ de saisie.

## Détection de doublons

Si tu essaies d'ajouter une URL déjà présente dans ta collection (via le formulaire ou le bookmarklet), Excerpta redirige automatiquement vers la page d'édition du lien existant avec un avertissement.

## Vérification des liens

`Paramètres → Vérifier les liens` lance une vérification asynchrone de tous les liens (10 en parallèle). Les liens cassés (4xx, 5xx, timeout) sont signalés avec leur statut HTTP.

## Recherche

La recherche est **en temps réel** : les résultats se filtrent à la frappe (sans rechargement), avec un court délai anti-rebond et un déclenchement dès 2 caractères. Vider le champ réaffiche tous les liens. La pagination s'effectue aussi en AJAX et l'URL reste partageable (le bouton précédent du navigateur fonctionne).

L'index full-text (SQLite FTS5) couvre **titres, descriptions, notes, URLs et tags**, et est **insensible aux accents**. Sans JavaScript, le formulaire de recherche classique reste fonctionnel.

## Vue lecteur

Chaque lien dispose d'une icône **lecteur** qui ouvre une version lisible et épurée de l'article :

- Extraction du contenu principal via Readability (le même algorithme que le mode lecture de Firefox), puis sanitisation du HTML (anti-XSS).
- Présentation focalisée : colonne de lecture étroite, typographie soignée, images conservées, temps de lecture estimé.
- Taille de police réglable (mémorisée) et thème clair/sombre hérité de l'application.
- L'extraction se fait à la première ouverture puis est **mise en cache** ; les ouvertures suivantes sont instantanées. Ajouter `?refresh=1` à l'URL force une nouvelle extraction.
- Les pages non extractibles (paywall, contenu purement JavaScript) affichent un état d'échec avec un lien vers l'original.

## Archivage (Wayback Machine)

Excerpta archive les liens sur la Wayback Machine de l'Internet Archive.

- **Automatique à l'ajout** : chaque nouveau lien est archivé en tâche de fond (capturé tant que la page est vivante). La sync FreshRSS n'archive pas automatiquement, pour ne pas saturer les quotas de Wayback.
- **Statut visible** par lien sur sa carte : en cours, archivé (l'icône pointe vers la capture Wayback) ou échec (bouton pour réessayer).
- **Archivage en masse** : `Paramètres → Archiver les non-archivés` lance un traitement de fond throttlé sur tous les liens pas encore archivés.

> Wayback limite fortement l'archivage anonyme : sur un gros lot, certains liens peuvent échouer (HTTP 429). Relancer ne re-cible que les liens non archivés.

## Page publique

Chaque utilisateur dispose d'une page publique listant ses liens publics :

- URL : `/u/{slug}` — le `slug` est personnalisable dans **Paramètres → Page publique**.
- Flux RSS associé : `/u/{slug}/feed.xml`.
- Seuls les liens marqués publics y apparaissent ; le titre de la page est personnalisable.

## Administration

Le panel admin (`/admin/`) est accessible uniquement aux comptes marqués `is_admin = true` en base. Il permet de :
- Lister et désactiver des utilisateurs
- Consulter les statistiques globales
- Régénérer les clés API
