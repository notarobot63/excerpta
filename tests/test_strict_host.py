"""StrictHostMiddleware : un Host refusé répond 400 sans faire planter la pile.

Les middlewares `@app.middleware("http")` de main.py s'exécutent avant lui
(ajoutés après). Ce test garantit qu'ils supportent un Host malformé, cas que
le commentaire de main.py relie au défaut « BadHost » de Starlette.
"""
import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.mark.parametrize("host", [
    "evil.example",
    "testserver:99999999999999999",
    "testserver:evil.example",
    "testserver:80@evil.example",
])
def test_host_refuse_en_400_avec_les_en_tetes_de_securite(host):
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/health", headers={"Host": host})
    assert resp.status_code == 400
    assert "content-security-policy" in resp.headers


def test_host_autorise_passe():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
