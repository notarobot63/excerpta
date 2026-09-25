"""Non-régression sur le compteur de requêtes (app/ratelimit.py).

Deux pièges couverts :

1. `_calls` est partagé par tous les limiteurs (de 60 s à 3600 s). Un balayage
   déclenché depuis un endpoint à 60 s ne doit pas évaluer avec cette période
   les entrées d'un endpoint à 3600 s, sous peine de remettre leurs quotas à
   zéro.
2. X-Forwarded-For est une liste que le proxy complète : le client contrôle les
   premiers éléments. Avec `FORWARDED_ALLOW_IPS=*`, uvicorn prenait [0], et
   changer cette valeur à chaque requête donnait un compteur neuf. Le test
   de bout en bout rejoue la pile réelle : uvicorn, avec la valeur par défaut du
   Dockerfile, devant l'application.
"""
import asyncio
import re
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import ratelimit


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeURL:
    def __init__(self, path):
        self.path = path


class _FakeRequest:
    def __init__(self, path, host="8.8.8.8", headers=None):
        self.client = _FakeClient(host)
        self.url = _FakeURL(path)
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _reset():
    ratelimit._calls.clear()
    ratelimit._cleanup_counter = 0
    yield
    ratelimit._calls.clear()


def test_limite_atteinte_puis_429():
    dep = ratelimit.rate_limit(2, 60)
    req = _FakeRequest("/x")
    asyncio.run(dep(req))
    asyncio.run(dep(req))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(req))
    assert exc.value.status_code == 429


def test_le_balayage_ne_reinitialise_pas_les_quotas_longs():
    """Le cœur de la régression : un cleanup venu d'un limiteur 60 s ne doit
    pas purger l'entrée d'un limiteur 3600 s vieille de seulement 2 minutes."""
    lent = ratelimit.rate_limit(2, 3600)
    req_lent = _FakeRequest("/settings/refresh-metadata")
    asyncio.run(lent(req_lent))
    asyncio.run(lent(req_lent))

    # on vieillit artificiellement l'entrée de 2 minutes
    key = "8.8.8.8:/settings/refresh-metadata"
    period, ts = ratelimit._calls[key]
    ratelimit._calls[key] = (period, [t - 120 for t in ts])

    # un limiteur rapide déclenche le balayage
    rapide = ratelimit.rate_limit(1000, 60)
    ratelimit._cleanup_counter = ratelimit._CLEANUP_EVERY - 1
    asyncio.run(rapide(_FakeRequest("/u/thomas")))

    assert key in ratelimit._calls, "quota horaire efface par un balayage 60 s"
    with pytest.raises(HTTPException):
        asyncio.run(lent(req_lent))


def test_le_balayage_libere_les_cles_reellement_expirees():
    dep = ratelimit.rate_limit(5, 60)
    asyncio.run(dep(_FakeRequest("/vieux")))
    key = "8.8.8.8:/vieux"
    period, ts = ratelimit._calls[key]
    ratelimit._calls[key] = (period, [t - 3600 for t in ts])

    ratelimit._cleanup_counter = ratelimit._CLEANUP_EVERY - 1
    asyncio.run(dep(_FakeRequest("/recent")))

    assert key not in ratelimit._calls, "les cles expirees doivent etre liberees"


def test_client_ip_ne_relit_aucun_entete():
    """L'adresse vient d'uvicorn seul : relire les en-têtes ici rouvrait la
    porte à X-Real-IP / X-Forwarded-For forgés."""
    req = _FakeRequest(
        "/x", host="10.0.0.5",
        headers={"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "1.1.1.1, 2.2.2.2"},
    )
    assert ratelimit._client_ip(req) == "10.0.0.5"


def _dockerfile_trusted_hosts() -> str:
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    match = re.search(r"^ENV FORWARDED_ALLOW_IPS=(\S+)$", dockerfile.read_text(), re.M)
    assert match, "FORWARDED_ALLOW_IPS introuvable dans le Dockerfile"
    return match.group(1)


def test_dockerfile_ne_fait_pas_confiance_a_tout():
    assert "*" not in _dockerfile_trusted_hosts().split(",")


def _client_derriere_le_proxy(engine):
    from fastapi.testclient import TestClient
    from sqlmodel import Session
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from app.database import get_session
    from app.main import app

    def _get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _get_session
    stack = ProxyHeadersMiddleware(app, trusted_hosts=_dockerfile_trusted_hosts())
    # La connexion arrive du reverse proxy, sur le réseau Docker.
    return TestClient(stack, base_url="https://testserver", client=("172.18.0.2", 40000))


def test_xff_forge_ne_contourne_pas_la_limite(engine):
    """Le client réécrit le début de X-Forwarded-For à chaque requête, le proxy
    ajoute sa vraie adresse à la fin : le compteur doit rester le même."""
    from app.main import app

    client = _client_derriere_le_proxy(engine)
    try:
        codes = [
            client.get(
                "/u/inconnu",
                headers={"X-Forwarded-For": f"198.51.100.{i % 250 + 1}, 203.0.113.7"},
            ).status_code
            for i in range(61)
        ]
    finally:
        app.dependency_overrides.clear()
    assert codes[:60].count(429) == 0
    assert codes[60] == 429, "une IP forgée en tête de liste a obtenu un compteur neuf"


def test_clients_distincts_gardent_des_compteurs_distincts(engine):
    """Le correctif ne doit pas mettre tous les visiteurs dans le compteur du proxy."""
    from app.main import app

    client = _client_derriere_le_proxy(engine)
    try:
        for _ in range(60):
            client.get("/u/inconnu", headers={"X-Forwarded-For": "203.0.113.7"})
        autre = client.get("/u/inconnu", headers={"X-Forwarded-For": "203.0.113.8"})
    finally:
        app.dependency_overrides.clear()
    assert autre.status_code == 404
