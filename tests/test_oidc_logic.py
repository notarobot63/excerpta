"""Non-régression sur _maybe_promote_admin (app/routes/oidc.py) : la logique
de promotion admin, jamais testée jusqu'ici. Le flux OIDC complet (PKCE,
callback réseau) reste hors scope, décision déjà prise dans conftest.py."""
import pytest

from app.config import settings
from app.models import User
from app.routes.oidc import _maybe_promote_admin


@pytest.fixture(autouse=True)
def _restore_settings():
    admin_email = settings.admin_email
    require_verified = settings.require_verified_email
    yield
    settings.admin_email = admin_email
    settings.require_verified_email = require_verified


def _make_user(session, **overrides):
    user = User(oidc_sub=overrides.pop("oidc_sub", "s1"), **overrides)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_admin_email_configure_sans_verification_ne_promeut_pas(session):
    settings.admin_email = "admin@example.com"
    settings.require_verified_email = True
    user = _make_user(session, email="admin@example.com")
    _maybe_promote_admin(session, user, is_new=False, email_verified=False)
    assert user.is_admin is False


def test_admin_email_configure_avec_verification_et_match_promeut(session):
    settings.admin_email = "admin@example.com"
    settings.require_verified_email = True
    user = _make_user(session, email="admin@example.com")
    _maybe_promote_admin(session, user, is_new=False, email_verified=True)
    assert user.is_admin is True


def test_admin_email_configure_mais_email_different_ne_promeut_pas(session):
    settings.admin_email = "admin@example.com"
    user = _make_user(session, email="autre@example.com")
    _maybe_promote_admin(session, user, is_new=False, email_verified=True)
    assert user.is_admin is False


def test_sans_admin_email_premier_utilisateur_est_promu(session):
    settings.admin_email = ""
    user = _make_user(session)  # seul utilisateur de la DB de test
    _maybe_promote_admin(session, user, is_new=True, email_verified=False)
    assert user.is_admin is True


def test_sans_admin_email_deuxieme_utilisateur_nest_pas_promu(session):
    settings.admin_email = ""
    _make_user(session, oidc_sub="s1")
    second = _make_user(session, oidc_sub="s2")
    _maybe_promote_admin(session, second, is_new=True, email_verified=False)
    assert second.is_admin is False


def test_sans_admin_email_utilisateur_existant_nest_jamais_promu(session):
    """is_new=False : même seul en base, un utilisateur déjà existant
    (reconnexion) ne doit pas être (re)promu par la branche "premier user"."""
    settings.admin_email = ""
    user = _make_user(session)
    _maybe_promote_admin(session, user, is_new=False, email_verified=False)
    assert user.is_admin is False
