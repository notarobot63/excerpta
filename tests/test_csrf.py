"""Non-régression sur app/csrf.py : c'est le contrôle de sécurité le plus
critique de l'app et il n'avait jusqu'ici aucun test. Style _FakeRequest,
comme test_ratelimit.py — pas de TestClient (cf. conftest.py)."""
import asyncio

import pytest
from fastapi import HTTPException

from app import csrf


class _FakeURL:
    def __init__(self, path):
        self.path = path


class _FakeRequest:
    def __init__(self, method, path, session=None, headers=None, form=None):
        self.method = method
        self.url = _FakeURL(path)
        self.session = session if session is not None else {}
        self.headers = headers or {}
        self._form = form or {}

    async def form(self):
        return self._form


def test_methode_non_mutante_passe_sans_session():
    req = _FakeRequest("GET", "/links/add")
    asyncio.run(csrf.csrf_protect(req))  # ne lève pas


def test_bypass_routes_oidc_meme_sans_session():
    req = _FakeRequest("POST", "/auth/oidc/callback")
    asyncio.run(csrf.csrf_protect(req))  # ne lève pas, aucune session requise


def test_token_absent_en_session_rejette():
    req = _FakeRequest("POST", "/links/1/delete")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(csrf.csrf_protect(req))
    assert exc.value.status_code == 403


def test_token_header_valide_accepte():
    tok = "a" * 64
    req = _FakeRequest(
        "POST", "/links/1/delete",
        session={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    asyncio.run(csrf.csrf_protect(req))  # ne lève pas


def test_token_header_invalide_puis_form_absent_rejette():
    tok = "a" * 64
    req = _FakeRequest(
        "POST", "/links/1/delete",
        session={"csrf_token": tok},
        headers={"X-CSRF-Token": "b" * 64},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(csrf.csrf_protect(req))
    assert exc.value.status_code == 403


def test_token_form_valide_accepte_sans_header():
    tok = "a" * 64
    req = _FakeRequest(
        "POST", "/links/1/delete",
        session={"csrf_token": tok},
        form={"csrf_token": tok},
    )
    asyncio.run(csrf.csrf_protect(req))  # ne lève pas


def test_token_form_invalide_rejette():
    tok = "a" * 64
    req = _FakeRequest(
        "POST", "/links/1/delete",
        session={"csrf_token": tok},
        form={"csrf_token": "wrong"},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(csrf.csrf_protect(req))
    assert exc.value.status_code == 403


def test_get_or_create_token_est_stable_et_persiste_en_session():
    session = {}
    req = _FakeRequest("GET", "/", session=session)
    tok1 = csrf._get_or_create_token(req)
    tok2 = csrf._get_or_create_token(req)
    assert tok1 == tok2
    assert session["csrf_token"] == tok1
