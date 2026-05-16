from sqlalchemy import text
from sqlmodel import Session, select

from .models import Group, Link, Tag


def get_or_create_tag(session: Session, user_id: int, name: str) -> Tag:
    tag = session.exec(select(Tag).where(Tag.user_id == user_id, Tag.name == name)).first()
    if not tag:
        tag = Tag(user_id=user_id, name=name)
        session.add(tag)
        session.flush()
    return tag


def refresh_link_fts(session: Session, link: Link, tags: list[Tag]):
    tags_str = " ".join(t.name for t in tags)
    session.execute(text("DELETE FROM fts_links WHERE link_id = :id"), {"id": link.id})
    session.execute(
        text(
            "INSERT INTO fts_links(link_id, title, description, note, url, tags)"
            " VALUES (:lid, :t, :d, :n, :u, :tg)"
        ),
        {"lid": link.id, "t": link.title, "d": link.description,
         "n": link.note, "u": link.url, "tg": tags_str},
    )


def descendant_group_ids(all_groups: list[Group], root_id: int) -> list[int]:
    """Retourne root_id + tous les IDs enfants récursivement."""
    by_parent: dict = {}
    for g in all_groups:
        by_parent.setdefault(g.parent_id, []).append(g.id)
    result: list[int] = []
    queue = [root_id]
    while queue:
        gid = queue.pop(0)
        result.append(gid)
        queue.extend(by_parent.get(gid, []))
    return result


def build_group_tree(groups: list[Group]) -> list[tuple]:
    """Retourne [(group, depth), ...] en ordre arborescent."""
    result = []

    def walk(parent_id, depth):
        children = sorted(
            [g for g in groups if g.parent_id == parent_id],
            key=lambda g: g.name,
        )
        for g in children:
            result.append((g, depth))
            walk(g.id, depth + 1)

    walk(None, 0)
    return result


def sidebar_data(session: Session, user_id: int) -> dict:
    all_tags = list(
        session.exec(select(Tag).where(Tag.user_id == user_id).order_by(Tag.name)).all()
    )
    all_groups = list(
        session.exec(select(Group).where(Group.user_id == user_id).order_by(Group.name)).all()
    )
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
    group_counts = {
        row[0]: row[1]
        for row in session.execute(
            text(
                "SELECT g.id, COUNT(lg.link_id) FROM groups g"
                " LEFT JOIN link_groups lg ON lg.group_id = g.id"
                " WHERE g.user_id = :uid GROUP BY g.id"
            ),
            {"uid": user_id},
        ).fetchall()
    }
    return {
        "all_tags": all_tags,
        "all_groups": all_groups,
        "group_tree": build_group_tree(all_groups),
        "tag_counts": tag_counts,
        "group_counts": group_counts,
    }
