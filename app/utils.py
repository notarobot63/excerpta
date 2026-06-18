import re
import unicodedata

from sqlalchemy import text
from sqlmodel import Session, select

from .models import Folder, Link, Tag, User


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


def folder_alpha_key(name: str) -> str:
    """Clé de tri dossier : insensible à la casse et aux accents (FR)."""
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().casefold()


def build_folder_tree(folders: list[Folder]) -> list[tuple]:
    """Retourne [(folder, depth), ...] en ordre arborescent, trié par sort_order puis nom."""
    result = []

    def walk(parent_id, depth):
        children = sorted(
            [f for f in folders if f.parent_id == parent_id],
            key=lambda f: (f.sort_order, f.name),
        )
        for f in children:
            result.append((f, depth))
            walk(f.id, depth + 1)

    walk(None, 0)
    return result


def folder_cumulative_counts(all_folders: list[Folder], direct_counts: dict) -> dict:
    """Calcule les compteurs cumulatifs (dossier + tous ses descendants)."""
    cumulative = {}
    tree = build_folder_tree(all_folders)
    folder_map = {f.id: f for f, _ in tree}

    def count(folder_id: int) -> int:
        if folder_id in cumulative:
            return cumulative[folder_id]
        total = direct_counts.get(folder_id, 0)
        for f in all_folders:
            if f.parent_id == folder_id:
                total += count(f.id)
        cumulative[folder_id] = total
        return total

    for f in all_folders:
        count(f.id)
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
