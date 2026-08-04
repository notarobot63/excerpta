"""Non-régression sur la garde SSRF (app/routes/links.py).

Le test historique (`test_extract_reader_rejects_private_url`) ne couvrait que
le littéral `http://127.0.0.1/`, et passait donc alors que deux contournements
étaient exploitables :

  1. une redirection 302 vers une cible interne était suivie par httpx, la
     requête interne partait, et seule l'URL *finale* était rejetée ensuite ;
  2. `::ffff:127.0.0.1` (IPv4-mapped IPv6) n'appartenait à aucun réseau de
     _PRIVATE_NETS et court-circuitait aussi la vérification DNS.

Ces tests exercent les deux vecteurs contre un vrai serveur local : ils
échouent sur le code d'avant correctif.
"""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.routes import freshrss as freshrss_mod
from app.routes.links import net_guard as links_mod
from app.routes.links import enrichment as enrichment_mod
from app.routes.links import reader as reader_mod
from app.routes.links import proxy as proxy_mod


# ── Serveurs de test ──────────────────────────────────────────────────────────

TOUCHED: list[str] = []


class _Interne(BaseHTTPRequestHandler):
    """Service interne qui ne devrait jamais recevoir de requête."""

    def do_GET(self):
        TOUCHED.append(self.path)
        body = b"contenu interne"
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Un 307 rejoue la méthode : sans traiter le POST, le handler répondrait 501
    # sans rien enregistrer, et TOUCHED resterait vide alors même que la requête
    # interne serait partie. Le test passerait pour une mauvaise raison.
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.do_GET()

    def log_message(self, *a):
        pass


class _Redirecteur(BaseHTTPRequestHandler):
    """Hôte « externe » qui renvoie un 302 vers la cible interne."""

    target = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.target)
        self.end_headers()

    def do_POST(self):
        # 307 : httpx rejoue la méthode ET le corps sur la cible, ce qui est
        # précisément le vecteur de fuite d'identifiants que couvre same_host_only.
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(307)
        self.send_header("Location", self.target)
        self.end_headers()

    def log_message(self, *a):
        pass


class _Externe(BaseHTTPRequestHandler):
    """Second hôte « externe », cible d'une redirection hors hôte."""

    def do_GET(self):
        TOUCHED.append("externe" + self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_POST = do_GET

    def log_message(self, *a):
        pass


def _serve(cls):
    srv = HTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def interne():
    TOUCHED.clear()
    srv = _serve(_Interne)
    yield srv
    srv.shutdown()


@pytest.fixture
def redirecteur(interne):
    _Redirecteur.target = f"http://127.0.0.1:{interne.server_port}/admin/secret"
    srv = _serve(_Redirecteur)
    yield srv
    srv.shutdown()


@pytest.fixture
def externe():
    TOUCHED.clear()
    srv = _serve(_Externe)
    yield srv
    srv.shutdown()


@pytest.fixture
def http_client():
    client = httpx.AsyncClient(follow_redirects=True, max_redirects=5)
    links_mod.set_http_client(client)
    yield client
    asyncio.run(client.aclose())


def _public_host(monkeypatch, port: int, *hostnames: str) -> None:
    """Fait passer des hôtes de test pour des hôtes publics résolvant en loopback.

    Sans cela, l'URL de départ serait rejetée dès la garde d'entrée et le test
    n'exercerait jamais le comportement sur redirection.
    """
    noms = hostnames or ("excerpta-test.invalid",)
    real_resolves = links_mod._hostname_resolves_public

    async def fake_resolves(hostname):
        return True if hostname in noms else await real_resolves(hostname)

    monkeypatch.setattr(links_mod, "_hostname_resolves_public", fake_resolves)
    monkeypatch.setattr(
        links_mod.socket, "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", port))],
    )


# ── _is_private_host ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "127.0.0.1",
    "localhost",
    "10.0.0.1",
    "192.168.1.254",
    "172.16.0.1",
    "169.254.169.254",     # métadonnées cloud
    "::1",
    "::ffff:127.0.0.1",    # IPv4-mapped : contournait la garde
    "::ffff:192.168.1.254",
    "224.0.0.1",           # multicast
    "0.0.0.0",
])
def test_hosts_prives_rejetes(host):
    assert links_mod._is_private_host(host) is True


@pytest.mark.parametrize("host", ["93.184.216.34", "1.1.1.1", "2606:4700::1111"])
def test_hosts_publics_acceptes(host):
    assert links_mod._is_private_host(host) is False


@pytest.mark.parametrize("url", [
    "http://[::ffff:127.0.0.1]/admin",
    "http://127.0.0.1/secret",
    "http://192.168.1.254/api",
    "file:///etc/passwd",
    "//evil.example/x",
    "gopher://127.0.0.1:70/",
])
def test_safe_url_rejette(url):
    assert links_mod._safe_url(url) is False


# ── Redirection ───────────────────────────────────────────────────────────────

def test_redirection_vers_cible_interne_nemet_aucune_requete(redirecteur, interne, http_client):
    """La cible interne ne doit recevoir AUCUNE requête, même rejetée après coup."""
    url = f"http://127.0.0.1:{redirecteur.server_port}/x.png"

    async def run():
        # L'hôte de départ est loopback, donc déjà refusé en amont : on court-circuite
        # la garde d'entrée pour n'éprouver que le comportement sur redirection.
        async with links_mod._safe_stream("GET", url, timeout=5) as resp:
            return resp.status_code

    with pytest.raises(links_mod._UnsafeRedirect):
        asyncio.run(run())
    assert TOUCHED == [], f"le service interne a été atteint : {TOUCHED}"


def test_fetch_meta_ne_suit_pas_la_redirection_interne(redirecteur, interne, http_client, monkeypatch):
    """Même scénario via _fetch_meta, avec un hôte de départ réputé public.

    Sans le monkeypatch, l'URL de départ (loopback) serait rejetée dès la garde
    d'entrée et le test passerait sans jamais exercer la redirection : c'est
    exactement le faux positif que ce fichier cherche à éviter.
    """
    real_resolves = links_mod._hostname_resolves_public
    start = f"http://excerpta-test.invalid:{redirecteur.server_port}/page.html"

    async def fake_resolves(hostname):
        if hostname == "excerpta-test.invalid":
            return True  # simule un hôte externe légitime
        return await real_resolves(hostname)

    monkeypatch.setattr(links_mod, "_hostname_resolves_public", fake_resolves)
    monkeypatch.setattr(
        links_mod.socket, "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", redirecteur.server_port))],
    )

    meta = asyncio.run(enrichment_mod._fetch_meta(start))
    assert meta == {"title": "", "description": "", "favicon_url": ""}
    assert TOUCHED == [], f"le service interne a été atteint : {TOUCHED}"


# ── Bout en bout sur les trois appelants ──────────────────────────────────────

def test_extract_reader_rejette_ipv4_mapped(interne, http_client):
    url = f"http://[::ffff:127.0.0.1]:{interne.server_port}/secret"
    assert asyncio.run(reader_mod._extract_reader(url)) is None
    assert TOUCHED == []


def test_extract_reader_rejette_url_privee(interne, http_client):
    assert asyncio.run(reader_mod._extract_reader("http://127.0.0.1/secret")) is None
    assert TOUCHED == []


def test_assert_public_url_rejette_ipv4_mapped(interne, http_client):
    url = f"http://[::ffff:127.0.0.1]:{interne.server_port}/secret"
    assert asyncio.run(links_mod._assert_public_url(url)) is False


def test_svg_refuse_par_le_proxy():
    """Un SVG servi depuis notre origine peut porter du script."""
    assert "image/svg+xml" in proxy_mod._FORBIDDEN_IMG_TYPES


# ── safe_request : la même garde pour les appels d'API ────────────────────────

def test_safe_request_refuse_une_url_privee(interne, http_client):
    url = f"http://127.0.0.1:{interne.server_port}/api"
    with pytest.raises(links_mod._UnsafeRedirect):
        asyncio.run(links_mod.safe_request("GET", url, timeout=5))
    assert TOUCHED == []


def test_safe_request_ne_suit_pas_la_redirection_interne(redirecteur, interne, http_client, monkeypatch):
    _public_host(monkeypatch, redirecteur.server_port)
    url = f"http://excerpta-test.invalid:{redirecteur.server_port}/api"
    with pytest.raises(links_mod._UnsafeRedirect):
        asyncio.run(links_mod.safe_request("GET", url, timeout=5))
    assert TOUCHED == [], f"le service interne a été atteint : {TOUCHED}"


def test_same_host_only_refuse_le_changement_dhote(externe, http_client, monkeypatch):
    """Une redirection vers un autre hôte, même public, ne doit pas rejouer les
    identifiants portés par la requête."""
    _Redirecteur.target = f"http://excerpta-cible.invalid:{externe.server_port}/x"
    redir = _serve(_Redirecteur)
    try:
        _public_host(monkeypatch, externe.server_port,
                     "excerpta-test.invalid", "excerpta-cible.invalid")
        url = f"http://excerpta-test.invalid:{redir.server_port}/api"

        with pytest.raises(links_mod._UnsafeRedirect):
            asyncio.run(links_mod.safe_request("GET", url, same_host_only=True, timeout=5))
        assert TOUCHED == [], f"l'hôte de destination a été atteint : {TOUCHED}"

        # Sans le drapeau, la redirection vers un hôte public reste légitime.
        resp = asyncio.run(links_mod.safe_request("GET", url, timeout=5))
        assert resp.status_code == 200
        assert TOUCHED == ["externe/x"]
    finally:
        redir.shutdown()


def test_greader_auth_ne_livre_pas_les_identifiants_en_interne(
    redirecteur, interne, http_client, monkeypatch
):
    """Bout en bout : le POST ClientLogin ne doit pas atteindre la cible d'un 307."""
    _public_host(monkeypatch, redirecteur.server_port)
    base = f"http://excerpta-test.invalid:{redirecteur.server_port}"

    with pytest.raises(links_mod._UnsafeRedirect):
        asyncio.run(freshrss_mod._greader_auth(base, "utilisateur", "secret"))
    assert TOUCHED == [], f"les identifiants sont partis vers : {TOUCHED}"
