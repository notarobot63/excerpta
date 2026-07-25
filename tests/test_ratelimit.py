"""Non-régression sur le compteur de requêtes (app/ratelimit.py).

Deux pièges couverts :

1. `_calls` est partagé par tous les limiteurs (de 60 s à 3600 s). Un balayage
   déclenché depuis un endpoint à 60 s ne doit pas évaluer avec cette période
   les entrées d'un endpoint à 3600 s, sous peine de remettre leurs quotas à
   zéro.
2. X-Forwarded-For est une liste que le proxy complète : le client contrôle les
   premiers éléments. Lire [0] laissait forger une IP et repartir d'un compteur
   neuf à chaque requête.
"""
import asyncio
import time

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


def test_xff_forge_par_le_client_est_ignore():
    """Derrière un proxy, seul le dernier saut de XFF est de confiance."""
    req = _FakeRequest(
        "/x", host="10.0.0.5",
        headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"},
    )
    assert ratelimit._client_ip(req) == "203.0.113.7"


def test_x_real_ip_prioritaire():
    req = _FakeRequest(
        "/x", host="10.0.0.5",
        headers={"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "1.1.1.1, 2.2.2.2"},
    )
    assert ratelimit._client_ip(req) == "203.0.113.9"


def test_ip_publique_directe_ignore_les_entetes():
    req = _FakeRequest("/x", host="93.184.216.34",
                       headers={"X-Forwarded-For": "1.1.1.1"})
    assert ratelimit._client_ip(req) == "93.184.216.34"
