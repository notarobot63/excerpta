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

À chaque sync, Excerpta récupère tous les articles étoilés et importe ceux qui ne sont pas encore présents (déduplication par URL). Les articles importés reçoivent automatiquement le tag `freshrss`.

La sync s'exécute :
- Automatiquement en arrière-plan selon l'intervalle configuré
- Manuellement via le bouton **Synchroniser maintenant** dans les paramètres
- Via l'API REST : `POST /api/v1/freshrss/sync` (authentification par clé API)

## Déséttoilage automatique

Lorsque tu supprimes un lien importé depuis FreshRSS, Excerpta propose de **déséttoiler l'article dans FreshRSS** en même temps via une checkbox (cochée par défaut).

Si tu décoches la case, le lien est supprimé uniquement dans Excerpta — l'étoile dans FreshRSS est conservée.

> Les liens importés avant la version qui introduit cette fonctionnalité récupèrent leur ID GReader automatiquement lors de la prochaine sync.

## Changer de dossier

Déplacer un lien FreshRSS vers un autre dossier dans Excerpta (drag & drop ou édition) n'affecte pas FreshRSS. Seule la suppression peut déclencher un déséttoilage.
