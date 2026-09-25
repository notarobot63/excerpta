"""Non-régression : le vérificateur de liens vérifie vraiment toute la collection.

Le délai de 30 s entourait aussi l'attente du sémaphore : au-delà d'une
centaine de liens, tous ceux restés dans la file expiraient sans avoir été
contactés, et le compteur les donnait pour faits.
"""
import asyncio

import pytest

import app.routes.settings as st
from app.models import Link, User


@pytest.fixture
def user_with_links(session, engine, monkeypatch):
    monkeypatch.setattr(st, "db_engine", engine)
    user = User(oidc_sub="chk", public_slug="chk")
    session.add(user)
    session.commit()
    for i in range(20):
        session.add(Link(user_id=user.id, url=f"https://e{i}.example"))
    session.commit()
    return user


def test_la_file_d_attente_ne_consomme_pas_le_delai(session, user_with_links, monkeypatch):
    checked = []

    async def _check(url):
        await asyncio.sleep(0.1)
        checked.append(url)
        return {"status": 200, "broken": False, "error": None}

    monkeypatch.setattr(st, "_check_url", _check)
    # 20 liens, 5 à la fois, ~0,3 s chacun : environ 1,2 s au total, bien
    # au-delà du délai, alors qu'aucune vérification ne le dépasse.
    monkeypatch.setattr(st, "_CHECK_TIMEOUT", 0.5)

    asyncio.run(st._run_check_background(user_with_links.id))

    assert len(checked) == 20
    job = st._check_jobs[user_with_links.id]
    assert job["done"] == 20 and not job["running"]
    session.expire_all()
    links = session.query(Link).filter(Link.user_id == user_with_links.id).all()
    assert all(lk.last_checked_at is not None for lk in links)


def test_un_site_qui_ne_repond_pas_est_marque_casse(session, user_with_links, monkeypatch):
    async def _hang(url):
        await asyncio.sleep(10)

    monkeypatch.setattr(st, "_check_url", _hang)
    monkeypatch.setattr(st, "_CHECK_TIMEOUT", 0.05)
    monkeypatch.setattr(st, "_CHECK_CONCURRENCY", 20)

    asyncio.run(st._run_check_background(user_with_links.id))

    session.expire_all()
    links = session.query(Link).filter(Link.user_id == user_with_links.id).all()
    assert all(lk.is_broken and lk.check_status is None for lk in links)
