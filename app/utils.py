import re
import unicodedata

from sqlalchemy import text
from sqlmodel import Session, select

from .crypto import hmac_key
from .models import Folder, Link, Tag, User


def resolve_api_user(session: Session, x_api_key: str) -> User | None:
    """Résout un utilisateur à partir d'une clé API (comparaison par HMAC).

    Ne pose aucun rate-limit ici : chaque routeur appelant garde le contrôle
    du sien (fréquences différentes entre l'API publique et le sync FreshRSS).
    Retourne None si la clé est absente/invalide ou l'utilisateur inactif —
    à l'appelant de lever l'HTTPException 401 appropriée.
    """
    if not x_api_key:
        return None
    computed_hmac = hmac_key(x_api_key)
    user = session.exec(select(User).where(User.api_key_hmac == computed_hmac)).first()
    if not user or not user.is_active:
        return None
    return user


def safe_next(raw: str | None) -> str:
    """Chemin de retour interne, ou « / ».

    Refuse tout ce qui pourrait sortir du site : URL absolue, `//evil.example`
    que le navigateur lit comme un hôte, et `/\\evil.example` que certains
    interprètent de même. Un simple `startswith("/")` laisse passer les deux
    dernières formes : toute route qui redirige vers un paramètre doit passer
    par ici, sans quoi elle devient un redirecteur ouvert.
    """
    if not raw or not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    return raw


def slugify(value: str) -> str:
    """Normalise une chaîne en slug URL (ASCII, minuscules, tirets)."""
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value


def unique_public_slug(session: Session, name: str, user_id: int) -> str:
    """Génère un slug unique pour la page publique d'un utilisateur.

    Base = slugify(name) ou 'u<id>' si le nom ne donne rien. En cas de
    collision, suffixe l'id puis un compteur.
    """
    base = slugify(name) or f"u{user_id}"
    candidate = base
    suffix = 0
    while True:
        existing = session.exec(
            select(User).where(User.public_slug == candidate, User.id != user_id)
        ).first()
        if not existing:
            return candidate
        suffix += 1
        candidate = f"{base}-{user_id}" if suffix == 1 else f"{base}-{user_id}-{suffix}"


def get_or_create_tag(session: Session, user_id: int, name: str) -> Tag:
    tag = session.exec(select(Tag).where(Tag.user_id == user_id, Tag.name == name)).first()
    if not tag:
        tag = Tag(user_id=user_id, name=name)
        session.add(tag)
        session.flush()
    return tag


def refresh_link_fts(session: Session, link: Link, tags: list[Tag]):
    tags_str = " ".join(t.name for t in tags)
    session.execute(text("DELETE FROM fts_links WHERE rowid = :id"), {"id": link.id})
    session.execute(
        text(
            "INSERT INTO fts_links(rowid, title, description, note, url, tags)"
            " VALUES (:lid, :t, :d, :n, :u, :tg)"
        ),
        {"lid": link.id, "t": link.title, "d": link.description,
         "n": link.note, "u": link.url, "tg": tags_str},
    )


def descendant_folder_ids(all_folders: list[Folder], root_id: int) -> list[int]:
    """Retourne root_id + tous les IDs enfants récursivement."""
    by_parent: dict = {}
    for f in all_folders:
        by_parent.setdefault(f.parent_id, []).append(f.id)
    result: list[int] = []
    seen: set[int] = set()
    queue = [root_id]
    while queue:
        fid = queue.pop(0)
        if fid in seen:
            continue
        seen.add(fid)
        result.append(fid)
        queue.extend(by_parent.get(fid, []))
    return result


def creates_cycle(parents: dict, child_id: int, new_parent_id) -> bool:
    """Rattacher `child_id` sous `new_parent_id` fermerait-il une boucle ?

    `parents` associe chaque id de dossier à celui de son parent. Un cycle rend
    des dossiers inatteignables depuis la racine et faisait diverger le calcul
    des compteurs cumulés : il doit être refusé à l'écriture, pas rattrapé à la
    lecture. Le garde-fou existait côté navigateur (`isDescendant` dans app.js),
    ce qui ne protégeait ni l'API ni un formulaire rejoué.
    """
    if new_parent_id is None:
        return False
    if new_parent_id == child_id:
        return True
    seen: set = set()
    current = new_parent_id
    while current is not None:
        if current == child_id:
            return True
        if current in seen:  # boucle déjà présente en base, en amont du parent visé
            return True
        seen.add(current)
        current = parents.get(current)
    return False


def folder_alpha_key(name: str) -> str:
    """Clé de tri dossier : insensible à la casse et aux accents (FR)."""
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().casefold()


def build_folder_tree(folders: list[Folder]) -> list[tuple]:
    """Retourne [(folder, depth), ...] en ordre arborescent, trié par sort_order puis nom.

    Tolère une base incohérente : un dossier pris dans un cycle n'est atteint
    par aucun chemin depuis la racine et disparaîtrait de l'interface, donc de
    toute possibilité de correction. Ces dossiers sont rattachés à la racine
    plutôt que d'être tus. Écrire un cycle est refusé en amont, voir
    `creates_cycle`.
    """
    result = []
    emitted: set = set()

    def walk(parent_id, depth):
        children = sorted(
            [f for f in folders if f.parent_id == parent_id and f.id not in emitted],
            key=lambda f: (f.sort_order, f.name),
        )
        for f in children:
            emitted.add(f.id)
            result.append((f, depth))
            walk(f.id, depth + 1)

    walk(None, 0)
    for f in sorted(folders, key=lambda f: (f.sort_order, f.name)):
        if f.id not in emitted:
            emitted.add(f.id)
            result.append((f, 0))
            walk(f.id, 1)
    return result


def folder_cumulative_counts(all_folders: list[Folder], direct_counts: dict) -> dict:
    """Calcule les compteurs cumulatifs (dossier + tous ses descendants).

    `visiting` coupe la récursion sur une base incohérente : la mémoïsation ne
    s'écrivant qu'au retour, un cycle faisait ici un RecursionError, et donc un
    500 sur toute page appelant `sidebar_data` — c'est-à-dire presque toutes.
    """
    cumulative: dict = {}
    children_by_parent: dict = {}
    for f in all_folders:
        children_by_parent.setdefault(f.parent_id, []).append(f)

    def count(folder_id: int, visiting: frozenset) -> int:
        if folder_id in cumulative:
            return cumulative[folder_id]
        if folder_id in visiting:
            return 0
        deeper = visiting | {folder_id}
        total = direct_counts.get(folder_id, 0)
        for child in children_by_parent.get(folder_id, []):
            total += count(child.id, deeper)
        cumulative[folder_id] = total
        return total

    for f in all_folders:
        count(f.id, frozenset())
    return cumulative


def sidebar_data(session: Session, user_id: int) -> dict:
    all_tags = list(
        session.exec(select(Tag).where(Tag.user_id == user_id).order_by(Tag.name)).all()
    )
    all_folders = list(
        session.exec(
            select(Folder).where(Folder.user_id == user_id)
            .order_by(Folder.sort_order, Folder.name)
        ).all()
    )
    total_links = session.execute(
        text("SELECT COUNT(*) FROM links WHERE user_id = :uid"), {"uid": user_id}
    ).scalar_one()
    tag_counts = {
        row[0]: row[1]
        for row in session.execute(
            text(
                "SELECT t.name, COUNT(lt.link_id) FROM tags t"
                " LEFT JOIN link_tags lt ON lt.tag_id = t.id"
                " WHERE t.user_id = :uid GROUP BY t.id, t.name"
            ),
            {"uid": user_id},
        ).fetchall()
    }
    direct_counts = {
        row[0]: row[1]
        for row in session.execute(
            text(
                "SELECT f.id, COUNT(l.id) FROM folders f"
                " LEFT JOIN links l ON l.folder_id = f.id"
                " WHERE f.user_id = :uid GROUP BY f.id"
            ),
            {"uid": user_id},
        ).fetchall()
    }
    folder_counts = folder_cumulative_counts(all_folders, direct_counts)
    return {
        "all_tags": all_tags,
        "all_folders": all_folders,
        "folder_tree": build_folder_tree(all_folders),
        "tag_counts": tag_counts,
        "folder_counts": folder_counts,
        "total_links": total_links,
    }
