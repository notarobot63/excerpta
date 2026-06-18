# Intégration FreshRSS

Excerpta peut synchroniser automatiquement les articles étoilés de FreshRSS via l'API GReader.

## Configuration

1. Dans FreshRSS, activer l'API GReader : **Paramètres → Authentification → Accès API**
2. Dans Excerpta : **Paramètres → FreshRSS**

| Champ | Description |
|---|---|
| URL FreshRSS | URL de l'instance (ex. `https://rss.example.com`) |
| Identifiant | Identifiant de connexion FreshRSS |
| Mot de passe API | Mot de passe API GReader (peut différer du mot de passe principal) |
| Dossier cible | Dossier Excerpta dans lequel ranger les articles importés |
| Intervalle | Fréquence de sync en minutes (défaut : 30) |

Le token GReader est chiffré (Fernet) avant stockage.

## Fonctionnement

À chaque sync, Excerpta récupère tous les articles étoilés et importe ceux qui ne sont pas encore présents (déduplication par URL). Les articles importés sont rangés dans le **dossier cible** configuré — c'est ce dossier, et non un tag, qui les identifie comme provenant de FreshRSS.

> Les versions antérieures posaient un tag `freshrss` sur chaque article importé. Ce tag, redondant avec le dossier, a été supprimé : une migration idempotente le retire automatiquement des liens existants au démarrage.

La sync s'exécute :
- Automatiquement en arrière-plan selon l'intervalle configuré
- Manuellement via le bouton **Synchroniser maintenant** dans les paramètres
- Via l'API REST : `POST /api/v1/freshrss/sync` (authentification par clé API)

## Déséttoilage automatique

Lorsque tu supprimes un lien importé depuis FreshRSS, Excerpta propose de **déséttoiler l'article dans FreshRSS** en même temps via une checkbox (cochée par défaut).

Si tu décoches la case, le lien est supprimé uniquement dans Excerpta — l'étoile dans FreshRSS est conservée.

> Les liens importés avant la version qui introduit cette fonctionnalité récupèrent leur ID GReader automatiquement lors de la prochaine sync.

## Déplacer un lien hors du dossier FreshRSS

Sortir un lien de son **dossier FreshRSS** (par drag & drop dans la sidebar ou via l'édition du lien) le **déséttoile automatiquement** dans FreshRSS : l'article quitte ta liste d'articles étoilés. C'est le pendant logique de la suppression.

Déplacer un lien entre deux dossiers qui ne sont pas le dossier FreshRSS n'a aucun effet sur FreshRSS.

> Auto-réparation : à chaque sync, tout article encore étoilé dans FreshRSS mais qui ne se trouve plus dans le dossier FreshRSS côté Excerpta est déséttoilé (rattrape les déplacements antérieurs et les échecs réseau).
