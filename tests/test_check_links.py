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


def test_deux_lancements_rapproches_ne_lancent_qu_une_verification(monkeypatch):
    """La place est réservée avant le lancement : la tâche ne marquait son état
    qu'à son premier passage, et un second POST en lançait une seconde."""
    spawned = []
    monkeypatch.setattr(st, "spawn", lambda coro, name=None: (spawned.append(name), coro.close()))
    monkeypatch.setattr(st, "_check_jobs", {})
    user = User(id=4242, oidc_sub="c")
    asyncio.run(st.check_links_run(user=user))
    asyncio.run(st.check_links_run(user=user))
    assert spawned == ["check-links-4242"]


def test_progression_compte_en_sql_sans_charger_les_liens(session, user_with_links):
    """Le compteur interrogé toutes les 2 s chargeait chaque lien complet."""
    from datetime import datetime

    from sqlalchemy import event

    links = session.exec(st.select(Link).where(Link.user_id == user_with_links.id)).all()
    for lk in links[:3]:
        lk.is_broken = True
        lk.last_checked_at = datetime(2026, 1, 1)
    links[3].last_checked_at = datetime(2026, 1, 1)
    session.commit()
    user_id = user_with_links.id
    session.expunge_all()
    user = session.get(User, user_id)

    loaded = []
    listener = lambda target, _ctx: loaded.append(target.id)  # noqa: E731
    event.listen(Link, "load", listener)
    try:
        out = asyncio.run(st.check_links_status(user=user, session=session))
    finally:
        event.remove(Link, "load", listener)
    assert (out["broken_count"], out["checked_count"]) == (3, 4)
    assert loaded == []
