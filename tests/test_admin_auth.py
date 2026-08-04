"""Non-régression sur app/auth.py (get_current_user, get_admin_user) : les
deux gardes qui protègent respectivement toute route connectée et toute route
admin, jusqu'ici jamais testées. Appel direct des dependencies FastAPI comme
fonctions (on passe les paramètres explicitement, sans résolution DI)."""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.auth import NotAuthenticated, get_admin_user, get_current_user
from app.models import FreshRSSConfig, User
from app.routes import admin as admin_mod


class _FakeRequest:
    def __init__(self, session=None):
        self.session = session if session is not None else {}


def _make_user(session, **overrides):
    user = User(oidc_sub=overrides.pop("oidc_sub", "s1"), **overrides)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_sans_user_id_en_session_leve_not_authenticated(session):
    req = _FakeRequest()
    with pytest.raises(NotAuthenticated):
        asyncio.run(get_current_user(req, session))


def test_utilisateur_inactif_leve_not_authenticated(session):
    user = _make_user(session, is_active=False)
    req = _FakeRequest({"user_id": user.id, "session_version": user.session_version})
    with pytest.raises(NotAuthenticated):
        asyncio.run(get_current_user(req, session))


def test_user_id_inconnu_leve_not_authenticated(session):
    req = _FakeRequest({"user_id": 999999, "session_version": 0})
    with pytest.raises(NotAuthenticated):
        asyncio.run(get_current_user(req, session))


def test_session_version_perimee_vide_la_session_et_leve(session):
    user = _make_user(session)
    sess = {"user_id": user.id, "session_version": user.session_version + 1}
    req = _FakeRequest(sess)
    with pytest.raises(NotAuthenticated):
        asyncio.run(get_current_user(req, session))
    assert sess == {}  # session invalidée, pas juste un refus silencieux


def test_utilisateur_valide_est_retourne(session):
    user = _make_user(session)
    req = _FakeRequest({"user_id": user.id, "session_version": user.session_version})
    result = asyncio.run(get_current_user(req, session))
    assert result.id == user.id


def test_get_admin_user_rejette_non_admin(session):
    user = _make_user(session, is_admin=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_admin_user(user=user))
    assert exc.value.status_code == 403


def test_get_admin_user_accepte_admin(session):
    user = _make_user(session, is_admin=True)
    result = asyncio.run(get_admin_user(user=user))
    assert result.id == user.id


# ── Suppression de compte : cascade complète ──────────────────────────────────

def test_supprimer_un_compte_efface_sa_config_freshrss(session):
    """La config FreshRSS porte l'URL, l'identifiant et le jeton chiffré du
    compte, et référence users.id : absente de la cascade, elle survivait à la
    suppression, en laissant au passage une clé étrangère orpheline."""
    admin = _make_user(session, oidc_sub="admin", is_admin=True)
    cible = _make_user(session, oidc_sub="cible")
    # Capturé avant l'appel : le commit de la route expire les objets de cette
    # session, et relire `cible.id` ensuite tenterait de recharger une ligne
    # supprimée (ObjectDeletedError). En production chaque requête a sa session.
    cible_id = cible.id
    session.add(FreshRSSConfig(
        user_id=cible_id, freshrss_url="https://rss.exemple.test",
        freshrss_user="cible", freshrss_token="jeton-chiffre",
    ))
    session.commit()

    asyncio.run(admin_mod.delete_user(uid=cible_id, admin=admin, session=session))

    restant = session.execute(
        text("SELECT COUNT(*) FROM freshrss_configs WHERE user_id=:id"), {"id": cible_id}
    ).scalar()
    assert restant == 0
    reste_user = session.execute(
        text("SELECT COUNT(*) FROM users WHERE id=:id"), {"id": cible_id}
    ).scalar()
    assert reste_user == 0
