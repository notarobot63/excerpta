"""Non-régression sur les préférences d'organisation (User.tags_enabled /
User.folders_enabled) : masquage UI quand désactivé, et persistance de la
bascule via POST /settings/organization. Style TestClient + dependency
overrides, comme le fixture `client` de test_features.py."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import Folder, User


def _make_client(engine, **user_kwargs):
    with Session(engine) as s:
        u = User(oidc_sub="orguser", email="o@e.com", public_slug="orguser", **user_kwargs)
        s.add(u)
        s.commit()
        uid = u.id
        # Un dossier existant, même désactivé : sert à vérifier que le
        # sélecteur du formulaire d'ajout se masque bien malgré des données
        # présentes (sinon le test passerait aussi sans le correctif, faute
        # de folder_tree à masquer).
        s.add(Folder(user_id=uid, name="Dev"))
        s.commit()

    def _get_session():
        with Session(engine) as s:
            yield s

    def _get_user():
        with Session(engine) as s:
            u = s.get(User, uid)
            s.expunge(u)
            return u

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[get_current_user] = _get_user
    # base_url en https : le cookie de session a le flag Secure
    # (session_cookie_secure=True par défaut), sinon il ne survit pas
    # d'une requête à l'autre chez httpx et le CSRF échoue à tort.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client_default(engine):
    yield _make_client(engine)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_tags(engine):
    yield _make_client(engine, tags_enabled=False)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_folders(engine):
    yield _make_client(engine, folders_enabled=False)
    app.dependency_overrides.clear()


def _csrf_token(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf_token introuvable dans la page"
    return m.group(1)


# ── Valeurs par défaut : les deux activés ───────────────────────────────────

def test_defaults_show_both_in_sidebar(client_default):
    r = client_default.get("/")
    assert "Manage tags" in r.text
    assert "Manage folders" in r.text


# ── Masquage sidebar ─────────────────────────────────────────────────────────

def test_sidebar_hides_tags_when_disabled(client_no_tags):
    r = client_no_tags.get("/")
    assert "Manage tags" not in r.text
    assert "Manage folders" in r.text  # l'autre préférence reste active


def test_sidebar_hides_folders_when_disabled(client_no_folders):
    r = client_no_folders.get("/")
    assert "Manage folders" not in r.text
    assert "Manage tags" in r.text


# ── Masquage formulaire d'ajout/édition ─────────────────────────────────────

def test_add_form_hides_tags_input_when_disabled(client_no_tags):
    r = client_no_tags.get("/links/add")
    assert 'id="tags"' not in r.text


def test_add_form_shows_tags_input_by_default(client_default):
    r = client_default.get("/links/add")
    assert 'id="tags"' in r.text


def test_add_form_hides_folder_picker_when_disabled(client_no_folders):
    r = client_no_folders.get("/links/add")
    assert 'class="folder-opt"' not in r.text


# ── Bascule persistée via /settings/organization ────────────────────────────

def test_toggle_off_then_on_round_trips(client_default):
    r = client_default.get("/settings")
    assert 'name="tags_enabled"' in r.text and "checked" in r.text
    tok = _csrf_token(r.text)

    # Aucune case cochée envoyée -> les deux passent à False
    r = client_default.post(
        "/settings/organization", data={"csrf_token": tok}, follow_redirects=False,
    )
    assert r.status_code == 303

    r = client_default.get("/")
    assert "Manage tags" not in r.text
    assert "Manage folders" not in r.text

    # Réactivation
    tok = _csrf_token(client_default.get("/settings").text)
    r = client_default.post(
        "/settings/organization",
        data={"csrf_token": tok, "tags_enabled": "on", "folders_enabled": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = client_default.get("/")
    assert "Manage tags" in r.text
    assert "Manage folders" in r.text


def test_toggle_requires_csrf_token(client_default):
    r = client_default.post("/settings/organization", data={}, follow_redirects=False)
    assert r.status_code == 403
