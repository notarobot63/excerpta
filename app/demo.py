"""Mode démo : espaces jetables et isolés, avec un catalogue pour amorcer.

Contrat de sécurité de ce module, à ne pas assouplir sans y repenser :

1. Le visiteur ajoute les URL de son choix, et le serveur les récupère comme il
   le fait en production : uniquement derrière la garde SSRF de
   `app/routes/links/net_guard.py` (adresses privées refusées, chaque saut de
   redirection revalidé, corps borné). Sont récupérés les métadonnées, le contenu
   de la vue lecteur et les vignettes. `CATALOG` reste le point de départ de
   l'espace et fournit un contenu lecteur préparé, mais n'est plus une limite.
2. Une sortie réseau reste interdite quand elle amplifie (une action du visiteur
   déclenchant N requêtes : import, vérification des liens, rafraîchissement en
   masse) ou quand elle publie chez un tiers au nom de l'application (archivage
   Wayback). Voir `forbid_in_demo`.
3. Ce que le visiteur dépose est plafonné : `DEMO_MAX_LINKS` liens par espace, et
   un débit d'ajout limité par IP côté routes.
4. Rien de ce qu'un visiteur saisit n'est visible par un autre. Chaque visiteur
   reçoit son propre `User`, et la publication publique est refusée en démo.
5. Tout est temporaire : les espaces sont purgés après `settings.demo_ttl_hours`.

Un utilisateur de démo se reconnaît à son `oidc_sub` préfixé par `demo:`. Ce
marqueur évite d'ajouter une colonne au schéma : `oidc_sub` est déjà unique et
obligatoire, et une base de production ne peut pas en contenir puisque le préfixe
ne peut pas être émis par un IdP (les `sub` OIDC y sont des identifiants opaques
de l'émetteur, jamais préfixés par nos soins).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func
from sqlmodel import Session, select

from .auth import get_current_user
from .config import settings
from .models import Folder, Link, LinkTagLink, Tag, User
from .ratelimit import rate_limit
from .utils import refresh_link_fts

DEMO_SUB_PREFIX = "demo:"

# Plafond de liens par espace de démo. Le visiteur ajoute désormais ses propres
# URL : sans plafond, un script remplirait la base et ferait sortir autant de
# requêtes de récupération, entre deux passages de la purge.
DEMO_MAX_LINKS = 100

# Dossiers créés dans chaque espace de démo, dans cet ordre.
DEMO_FOLDERS = ["Veille technique", "À lire plus tard", "Références"]

# Catalogue fermé. Chaque entrée est un lien que le visiteur peut ajouter, et
# tout ce qui s'affiche à son sujet vient d'ici : rien n'est récupéré en ligne.
# `seed` marque les entrées déjà présentes à l'ouverture de l'espace.
# `reader` fournit le contenu de la vue lecteur ; il est rédigé pour la démo et
# renvoie vers la source plutôt que d'en reproduire le texte.
CATALOG: list[dict] = [
    {
        "url": "https://sqlite.org/fts5.html",
        "title": "SQLite FTS5 : recherche plein texte",
        "description": "L'extension de recherche plein texte de SQLite, son classement bm25 et sa syntaxe de requête.",
        "folder": "Références",
        "tags": ["sqlite", "recherche"],
        "note": "C'est ce qui fait tourner la recherche d'Excerpta, avec un bm25 pondéré pour que le titre pèse plus que l'URL.",
        "seed": True,
        "reader": (
            "<h2>SQLite FTS5</h2>"
            "<p>FTS5 est un module de table virtuelle qui ajoute la recherche plein texte à SQLite. "
            "Les colonnes indexées sont déclarées à la création de la table, et les requêtes utilisent "
            "l'opérateur <code>MATCH</code>.</p>"
            "<p>Le classement se fait avec la fonction <code>bm25()</code>, qui accepte un poids par "
            "colonne. C'est ce qui permet de faire compter un mot trouvé dans un titre davantage que "
            "le même mot trouvé dans une URL.</p>"
            "<p><em>Extrait de présentation rédigé pour la démonstration. Le texte original se trouve "
            "sur sqlite.org.</em></p>"
        ),
    },
    {
        "url": "https://fastapi.tiangolo.com/",
        "title": "FastAPI",
        "description": "Framework web Python asynchrone, typé, avec génération automatique de la documentation OpenAPI.",
        "folder": "Références",
        "tags": ["python", "web"],
        "note": "",
        "seed": True,
        "reader": (
            "<h2>FastAPI</h2>"
            "<p>FastAPI s'appuie sur les annotations de type Python pour valider les entrées, "
            "sérialiser les sorties et produire une documentation OpenAPI sans travail supplémentaire.</p>"
            "<p>Le système d'injection de dépendances sert aussi bien à fournir une session de base de "
            "données qu'à exiger une authentification sur un ensemble de routes.</p>"
            "<p><em>Extrait de présentation rédigé pour la démonstration. La documentation complète se "
            "trouve sur fastapi.tiangolo.com.</em></p>"
        ),
    },
    {
        "url": "https://developer.mozilla.org/fr/docs/Web/HTTP/Headers/Content-Security-Policy",
        "title": "Content-Security-Policy (MDN)",
        "description": "L'en-tête HTTP qui restreint les sources de scripts, de styles et d'images d'une page.",
        "folder": "Références",
        "tags": ["sécurité", "web"],
        "note": "",
        "seed": True,
        "reader": (
            "<h2>Content-Security-Policy</h2><p>Cet en-tête HTTP déclare les sources depuis "
            "lesquelles une page accepte de charger des scripts, des styles, des images ou des "
            "polices. Tout ce qui n'est pas déclaré est refusé par le navigateur.</p><p>C'est la "
            "principale défense contre l'injection de script : même si du code hostile parvient dans "
            "la page, il ne s'exécute pas s'il ne correspond pas à la politique, typiquement grâce à "
            "un <code>nonce</code> renouvelé à chaque réponse.</p><p><em>Extrait de présentation "
            "rédigé pour la démonstration. La source complète se trouve sur "
            "developer.mozilla.org.</em></p>"
        ),
    },
    {
        "url": "https://docs.docker.com/compose/",
        "title": "Docker Compose",
        "description": "Définir et lancer des applications multi-conteneurs à partir d'un fichier YAML.",
        "folder": "Veille technique",
        "tags": ["docker", "déploiement"],
        "note": "",
        "seed": True,
        "reader": (
            "<h2>Docker Compose</h2><p>Compose décrit dans un fichier YAML l'ensemble des conteneurs "
            "d'une application, leurs volumes, leurs réseaux et leurs variables "
            "d'environnement.</p><p>Un seul fichier versionné remplace une série de commandes "
            "<code>docker run</code>, ce qui rend le déploiement reproductible et "
            "lisible.</p><p><em>Extrait de présentation rédigé pour la démonstration. La source "
            "complète se trouve sur docs.docker.com.</em></p>"
        ),
    },
    {
        "url": "https://www.freshrss.org/",
        "title": "FreshRSS",
        "description": "Agrégateur RSS auto-hébergé, compatible avec l'API Google Reader.",
        "folder": "Veille technique",
        "tags": ["rss", "auto-hébergement"],
        "note": "Excerpta récupère automatiquement les articles marqués comme favoris ici.",
        "seed": True,
        "reader": (
            "<h2>FreshRSS</h2><p>Agrégateur de flux RSS et Atom auto-hébergé, écrit en PHP, prévu "
            "pour tourner sur une machine modeste.</p><p>Il expose une API compatible Google Reader, "
            "ce qui permet à des applications tierces de lire les flux et de récupérer les articles "
            "marqués comme favoris.</p><p><em>Extrait de présentation rédigé pour la démonstration. "
            "La source complète se trouve sur www.freshrss.org.</em></p>"
        ),
    },
    {
        "url": "https://openid.net/developers/how-connect-works/",
        "title": "Comment fonctionne OpenID Connect",
        "description": "La couche d'identité construite au-dessus d'OAuth 2.0, et le déroulé d'une authentification.",
        "folder": "À lire plus tard",
        "tags": ["oidc", "sécurité"],
        "note": "",
        "seed": True,
        "reader": (
            "<h2>OpenID Connect</h2><p>OpenID Connect ajoute une couche d'identité au-dessus d'OAuth "
            "2.0 : là où OAuth délègue une autorisation, OIDC répond à la question de savoir qui est "
            "l'utilisateur.</p><p>Le fournisseur d'identité émet un jeton signé contenant un "
            "identifiant stable, le <code>sub</code>, que l'application associe à son propre compte. "
            "Le mot de passe ne quitte jamais le fournisseur.</p><p><em>Extrait de présentation "
            "rédigé pour la démonstration. La source complète se trouve sur openid.net.</em></p>"
        ),
    },
    {
        "url": "https://letsencrypt.org/docs/",
        "title": "Documentation Let's Encrypt",
        "description": "Émission automatisée de certificats TLS, défis HTTP-01 et DNS-01.",
        "folder": "Références",
        "tags": ["tls", "sécurité"],
        "note": "",
        "seed": False,
        "reader": (
            "<h2>Let's Encrypt</h2><p>Autorité de certification gratuite qui délivre des certificats "
            "TLS par le protocole automatisé ACME.</p><p>Deux épreuves permettent de prouver la "
            "maîtrise d'un domaine : HTTP-01, qui sert un fichier sur le domaine, et DNS-01, qui "
            "publie un enregistrement TXT. La seconde est nécessaire pour un certificat générique ou "
            "un service non exposé.</p><p><em>Extrait de présentation rédigé pour la démonstration. "
            "La source complète se trouve sur letsencrypt.org.</em></p>"
        ),
    },
    {
        "url": "https://caddyserver.com/docs/",
        "title": "Serveur Caddy",
        "description": "Serveur web avec HTTPS automatique et configuration déclarative.",
        "folder": "Veille technique",
        "tags": ["web", "déploiement"],
        "note": "",
        "seed": False,
        "reader": (
            "<h2>Serveur Caddy</h2><p>Serveur web dont la particularité est d'obtenir et de "
            "renouveler les certificats TLS sans configuration explicite.</p><p>Sa configuration "
            "tient souvent en quelques lignes, et il sait servir des fichiers statiques comme jouer "
            "le rôle de proxy inverse.</p><p><em>Extrait de présentation rédigé pour la "
            "démonstration. La source complète se trouve sur caddyserver.com.</em></p>"
        ),
    },
    {
        "url": "https://wiki.archlinux.org/title/Systemd",
        "title": "systemd (ArchWiki)",
        "description": "Unités, minuteries et journalisation, avec les commandes de diagnostic usuelles.",
        "folder": "Références",
        "tags": ["linux"],
        "note": "",
        "seed": False,
        "reader": (
            "<h2>systemd</h2><p>systemd gère le démarrage et la supervision des services d'un système "
            "Linux. Chaque service est décrit par une unité, un fichier déclaratif précisant la "
            "commande, ses dépendances et sa politique de redémarrage.</p><p>Les minuteries "
            "remplacent avantageusement cron, avec journalisation intégrée et rattrapage des "
            "exécutions manquées.</p><p><em>Extrait de présentation rédigé pour la démonstration. La "
            "source complète se trouve sur wiki.archlinux.org.</em></p>"
        ),
    },
    {
        "url": "https://docs.python.org/3/library/sqlite3.html",
        "title": "Module sqlite3 de Python",
        "description": "L'interface DB-API 2.0 de la bibliothèque standard pour SQLite.",
        "folder": "Références",
        "tags": ["python", "sqlite"],
        "note": "",
        "seed": False,
        "reader": (
            "<h2>Module sqlite3</h2><p>La bibliothèque standard de Python expose SQLite via "
            "l'interface DB-API 2.0, sans aucune dépendance externe.</p><p>Les requêtes paramétrées y "
            "sont la règle : passer les valeurs séparément de la requête écarte l'injection SQL et "
            "laisse le moteur réutiliser son plan d'exécution.</p><p><em>Extrait de présentation "
            "rédigé pour la démonstration. La source complète se trouve sur docs.python.org.</em></p>"
        ),
    },
    {
        "url": "https://web.archive.org/",
        "title": "Wayback Machine",
        "description": "Archive du web, utilisée par Excerpta pour conserver une copie des pages enregistrées.",
        "folder": "À lire plus tard",
        "tags": ["archivage"],
        "note": "",
        "seed": False,
        "reader": (
            "<h2>Wayback Machine</h2><p>Archive du web tenue par l'Internet Archive, qui conserve des "
            "instantanés datés de pages publiques.</p><p>Un gestionnaire de favoris y trouve une "
            "réponse au pourrissement des liens : la page peut disparaître, l'instantané reste "
            "consultable.</p><p><em>Extrait de présentation rédigé pour la démonstration. La source "
            "complète se trouve sur web.archive.org.</em></p>"
        ),
    },
    {
        "url": "https://www.postgresql.org/docs/current/index.html",
        "title": "Documentation PostgreSQL",
        "description": "Manuel de référence du serveur de base de données PostgreSQL.",
        "folder": "À lire plus tard",
        "tags": ["base de données"],
        "note": "",
        "seed": False,
        "reader": (
            "<h2>PostgreSQL</h2><p>Système de gestion de base de données relationnelle, reconnu pour "
            "son respect des standards et la solidité de ses transactions.</p><p>Il dépasse le SQL "
            "classique avec le type <code>jsonb</code>, la recherche plein texte et les index "
            "partiels ou fonctionnels.</p><p><em>Extrait de présentation rédigé pour la "
            "démonstration. La source complète se trouve sur www.postgresql.org.</em></p>"
        ),
    },
]

CATALOG_BY_URL: dict[str, dict] = {entry["url"]: entry for entry in CATALOG}


def _utcnow() -> datetime:
    """Naïf en UTC, cohérent avec les datetime stockés par les modèles."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_demo_user(user: Optional[User]) -> bool:
    return bool(user and user.oidc_sub.startswith(DEMO_SUB_PREFIX))


def is_demo_user_id(session: Session, user_id: int) -> bool:
    """Variante par identifiant, pour les chemins qui ne portent pas l'objet User."""
    return is_demo_user(session.get(User, user_id))


def demo_active() -> bool:
    return settings.demo_mode


def assert_link_quota(session: Session, user_id: int) -> None:
    """Refuse un ajout au-delà de `DEMO_MAX_LINKS` liens dans un espace de démo.

    Transparent hors démo et pour un compte réel : c'est le pendant, côté
    stockage, du débit d'ajout limité par IP sur les routes.
    """
    if not demo_active() or not is_demo_user_id(session, user_id):
        return
    total = session.exec(
        select(func.count()).select_from(Link).where(Link.user_id == user_id)
    ).one()
    if total >= DEMO_MAX_LINKS:
        raise HTTPException(
            status_code=400,
            detail=f"Demo space limited to {DEMO_MAX_LINKS} links.",
        )


def create_demo_space(session: Session) -> User:
    """Crée un espace de démo isolé : un utilisateur jetable et son jeu de données."""
    user = User(
        oidc_sub=f"{DEMO_SUB_PREFIX}{uuid.uuid4()}",
        email="",
        name="Visiteur",
        is_admin=False,
        is_active=True,
        public_slug=None,
    )
    session.add(user)
    session.flush()

    folders: dict[str, Folder] = {}
    for order, name in enumerate(DEMO_FOLDERS):
        folder = Folder(user_id=user.id, name=name, sort_order=order)
        session.add(folder)
        session.flush()
        folders[name] = folder

    for entry in CATALOG:
        if entry.get("seed"):
            _add_catalog_link(session, user, entry, folders.get(entry["folder"]))

    session.commit()
    return user


def add_catalog_link(session: Session, user: User, url: str) -> Link:
    """Ajoute au visiteur un lien du catalogue. Refuse toute URL extérieure.

    Chemin d'ajout distinct du formulaire libre, qui est ouvert aux visiteurs
    depuis la version 1.2 : celui-ci sert le catalogue et n'accepte donc que ses
    URL. Ne pas lire ce refus comme une garantie de sécurité globale : c'est
    `net_guard` qui encadre les adresses fournies par un inconnu, et le contrat
    en tête de module qui en fixe les limites.
    """
    entry = CATALOG_BY_URL.get(url)
    if entry is None:
        raise HTTPException(status_code=400, detail="Lien hors catalogue de démonstration")

    existing = session.exec(
        select(Link).where(Link.user_id == user.id, Link.url == url)
    ).first()
    if existing:
        return existing

    folder = session.exec(
        select(Folder).where(Folder.user_id == user.id, Folder.name == entry["folder"])
    ).first()
    link = _add_catalog_link(session, user, entry, folder)
    session.commit()
    return link


def _add_catalog_link(
    session: Session, user: User, entry: dict, folder: Optional[Folder]
) -> Link:
    link = Link(
        user_id=user.id,
        url=entry["url"],
        title=entry["title"],
        description=entry["description"],
        note=entry.get("note", ""),
        favicon_url="",       # vide : évite toute requête sortante du proxy d'images
        thumbnail_url="",
        is_public=False,      # la publication est refusée en démo
        folder_id=folder.id if folder else None,
        archive_status=None,  # aucun archivage n'est planifié en démo
        reader_html=entry.get("reader"),
        reader_title=entry["title"] if entry.get("reader") else None,
        reader_extracted_at=_utcnow() if entry.get("reader") else None,
        reader_failed=False,
    )
    session.add(link)
    session.flush()

    tags = []
    for name in entry.get("tags", []):
        tag = session.exec(
            select(Tag).where(Tag.user_id == user.id, Tag.name == name)
        ).first()
        if tag is None:
            tag = Tag(user_id=user.id, name=name)
            session.add(tag)
            session.flush()
        tags.append(tag)
        session.add(LinkTagLink(link_id=link.id, tag_id=tag.id))

    session.flush()
    refresh_link_fts(session, link, tags)
    return link


def purge_expired_demo_users(session: Session, ttl_hours: Optional[int] = None) -> int:
    """Supprime les espaces de démo expirés et tout ce qui s'y rattache.

    Les relations SQLModel du projet ne déclarent pas de cascade au niveau SQL :
    la suppression est donc explicite, sans quoi liens, tags et dossiers
    survivraient à leur propriétaire et la base grossirait indéfiniment.
    """
    ttl = settings.demo_ttl_hours if ttl_hours is None else ttl_hours
    cutoff = _utcnow() - timedelta(hours=ttl)
    expired = session.exec(
        select(User).where(
            User.oidc_sub.startswith(DEMO_SUB_PREFIX),  # type: ignore[attr-defined]
            User.created_at < cutoff,
        )
    ).all()

    for user in expired:
        _delete_demo_user(session, user)
    session.commit()
    return len(expired)


def _delete_demo_user(session: Session, user: User) -> None:
    # Les lignes de `link_tags` sont gérées par l'ORM via la relation
    # Link.tags, et la table FTS est vidée par le trigger `links_ad`. Les
    # supprimer à la main en SQL ferait échouer l'ORM ensuite (StaleDataError :
    # il compte les lignes d'association qu'il s'attend à supprimer lui-même).
    for link in session.exec(select(Link).where(Link.user_id == user.id)).all():
        session.delete(link)
    session.flush()

    for tag in session.exec(select(Tag).where(Tag.user_id == user.id)).all():
        session.delete(tag)
    for folder in session.exec(select(Folder).where(Folder.user_id == user.id)).all():
        session.delete(folder)
    session.delete(user)


def forbid_in_demo(user: User) -> None:
    """Refuse une fonctionnalité indisponible en démo (403).

    Utilisé pour tout ce qui expose des données au-delà de l'espace du visiteur
    (publication publique, administration, synchronisation FreshRSS), pour les
    sorties réseau amplifiantes (import, vérification des liens, rafraîchissement
    en masse) et pour l'archivage Wayback, qui publierait chez un tiers l'adresse
    fournie par un inconnu au nom de l'application. La récupération d'une URL
    pour un seul lien, elle, est autorisée : voir le contrat en tête de module.
    """
    if demo_active() and is_demo_user(user):
        raise HTTPException(
            status_code=403,
            detail="Fonctionnalité désactivée dans la démonstration",
        )


async def forbid_in_demo_dep(user: User = Depends(get_current_user)) -> None:
    """Version dépendance, posable sur une route ou un routeur entier."""
    forbid_in_demo(user)


def demo_rate_limit(calls: int, period_seconds: int):
    """Débit par IP appliqué uniquement en mode démo, transparent ailleurs.

    Chaque ajout de lien déclenche une requête sortante : sur une instance
    ouverte à des inconnus, le débit se limite ici plutôt que dans les quotas
    d'une instance normale, où l'utilisateur est authentifié et connu.
    """
    limiter = rate_limit(calls, period_seconds)

    async def dependency(request: Request) -> None:
        if demo_active():
            await limiter(request)

    return dependency
