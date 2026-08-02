"""Non-régression sur GET /api/v1/me : expose tags_enabled/folders_enabled,
lus une fois par l'app Android au démarrage pour masquer sa propre UI en
parité avec le web (voir excerpta-android ApiClient.fetchMe)."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.crypto import hmac_key
from app.database import get_session
from app.main import app
from app.models import User

_PLAIN_KEY = "test-api-key-0123456789abcdef"


def _make_client(engine, **user_kwargs):
    with Session(engine) as s:
        u = User(
            oidc_sub="apiuser", email="a@e.com", public_slug="apiuser",
            api_key=_PLAIN_KEY, api_key_hmac=hmac_key(_PLAIN_KEY),
            **user_kwargs,
        )
        s.add(u)
        s.commit()

    def _get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _get_session
    return TestClient(app)


@pytest.fixture
def client_default(engine):
    yield _make_client(engine)
    app.dependency_overrides.clear()


@pytest.fixture
def client_disabled(engine):
    yield _make_client(engine, tags_enabled=False, folders_enabled=False)
    app.dependency_overrides.clear()


def test_me_exposes_organization_prefs_by_default(client_default):
    r = client_default.get("/api/v1/me", headers={"X-API-Key": _PLAIN_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["tags_enabled"] is True
    assert body["folders_enabled"] is True


def test_me_reflects_disabled_prefs(client_disabled):
    r = client_disabled.get("/api/v1/me", headers={"X-API-Key": _PLAIN_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["tags_enabled"] is False
    assert body["folders_enabled"] is False


def test_me_requires_api_key(client_default):
    r = client_default.get("/api/v1/me")
    assert r.status_code == 401
