"""Parité du chemin de création de lien (UI web + API mobile).

`create_link` est la fonction unique appelée par les deux handlers. Ces tests
garantissent qu'un lien créé par n'importe quelle source planifie bien
l'enrichissement des métadonnées (titre/extrait/favicon) et l'archivage Wayback.

Régression visée : l'API mobile créait les liens sans jamais déclencher
`_fetch_and_update_meta`, d'où des titres/extraits jamais remplis côté Android.
"""
from fastapi import BackgroundTasks
from sqlmodel import Session, select

from app.models import Link, User
from app.routes.links import _fetch_and_update_meta, _wayback_archive, create_link


def _user(session: Session, sub: str) -> User:
    u = User(oidc_sub=sub, email=f"{sub}@e.com", public_slug=sub)
    session.add(u)
    session.commit()
    return u


def test_create_link_schedules_enrichment(session):
    u = _user(session, "creator")
    bg = BackgroundTasks()

    link, created = create_link(
        session,
        bg,
        user_id=u.id,
        url="https://example.com/article",
        title="",
        tag_names=["lecture", "veille"],
    )

    assert created is True
    assert link.archive_status == "pending"
    assert link.title == "https://example.com/article"  # placeholder avant enrichissement
    assert link.description == ""                        # extrait rempli en tâche de fond

    scheduled = {t.func for t in bg.tasks}
    assert _fetch_and_update_meta in scheduled  # le bug : cette tâche manquait côté API
    assert _wayback_archive in scheduled


def test_create_link_dedupes_without_rescheduling(session):
    u = _user(session, "dedup")
    create_link(session, BackgroundTasks(), user_id=u.id, url="https://example.com/x")

    bg = BackgroundTasks()
    link, created = create_link(session, bg, user_id=u.id, url="https://example.com/x")

    assert created is False
    assert bg.tasks == []  # pas de re-fetch ni de ré-archivage sur doublon
    count = len(session.exec(select(Link).where(Link.user_id == u.id)).all())
    assert count == 1
