"""Non-régression : un seul archivage en masse à la fois par compte.

Chaque clic relançait une série complète en parallèle de la précédente, soit
des soumissions en double chez Wayback, qui limite l'archivage anonyme.
"""
import asyncio

import pytest
from fastapi import BackgroundTasks

import app.routes.settings as st
from app.models import Link, User


@pytest.fixture(autouse=True)
def _reset():
    st._archive_running.clear()
    yield
    st._archive_running.clear()


def _setup(session):
    user = User(oidc_sub="arc", public_slug="arc")
    session.add(user)
    session.commit()
    for i in range(3):
        session.add(Link(user_id=user.id, url=f"https://e{i}.example"))
    session.commit()
    return user


def test_un_second_clic_ne_relance_pas_la_serie(session):
    user = _setup(session)
    first, second = BackgroundTasks(), BackgroundTasks()
    r1 = asyncio.run(st.archive_all(first, user=user, session=session))
    r2 = asyncio.run(st.archive_all(second, user=user, session=session))
    assert len(first.tasks) == 1 and len(second.tasks) == 0
    assert r1.headers["location"] == r2.headers["location"] == "/settings?archiving=3"


def test_la_fin_de_la_serie_libere_le_compte(session, monkeypatch):
    user = _setup(session)
    done = []

    async def _fake_many(ids):
        done.extend(ids)

    monkeypatch.setattr(st, "_archive_many", _fake_many)
    tasks = BackgroundTasks()
    asyncio.run(st.archive_all(tasks, user=user, session=session))
    asyncio.run(tasks())
    assert len(done) == 3
    assert user.id not in st._archive_running
