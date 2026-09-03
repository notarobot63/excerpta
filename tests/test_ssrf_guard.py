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
import socket
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

    Neutralise aussi le contrôle de l'adresse du pair : ces serveurs écoutent en
    loopback, et le simulacre ne tiendrait pas jusqu'à la connexion. Les tests
    qui visent ce contrôle ne passent donc pas par ce helper.
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
    monkeypatch.setattr(links_mod, "_assert_peer_public", lambda resp, url: None)


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


# ── Rebinding DNS : l'adresse réellement contactée ────────────────────────────
#
# `_hostname_resolves_public` valide une résolution, puis httpx en fait une
# autre pour se connecter. Un DNS hostile à TTL très court peut répondre
# publiquement à la première et pointer une adresse interne à la seconde. Seule
# l'adresse du pair, relevée sur la connexion établie, tranche.


class _FauxStream:
    def __init__(self, addr):
        self._addr = addr

    def get_extra_info(self, name):
        return self._addr if name == "server_addr" else None


def _reponse_depuis(addr):
    return httpx.Response(
        200, content=b"secret interne",
        extensions={"network_stream": _FauxStream(addr)} if addr else {},
    )


@pytest.mark.parametrize("ip", [
    "127.0.0.1",
    "10.0.0.5",
    "192.168.1.254",
    "169.254.169.254",     # métadonnées cloud
    "::1",
    "::ffff:127.0.0.1",    # IPv4-mapped
])
def test_pair_prive_refuse(ip):
    with pytest.raises(links_mod._UnsafeRedirect):
        links_mod._assert_peer_public(_reponse_depuis((ip, 80)), "http://exemple.test/")


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:4700::1111"])
def test_pair_public_accepte(ip):
    links_mod._assert_peer_public(_reponse_depuis((ip, 443)), "http://exemple.test/")


def test_pair_inconnu_laisse_passer():
    """Transport de test : pas de connexion réelle, donc rien à valider."""
    links_mod._assert_peer_public(_reponse_depuis(None), "http://exemple.test/")


def test_rebinding_bloque_avant_toute_lecture(interne, http_client):
    """Bout en bout, vrai socket : la garde d'entrée a dit « public », la
    connexion aboutit en loopback, aucun contenu ne doit remonter."""
    url = f"http://127.0.0.1:{interne.server_port}/admin/secret"

    async def scenario():
        async with links_mod._safe_stream("GET", url, timeout=5) as resp:
            return await resp.aread()

    with pytest.raises(links_mod._UnsafeRedirect):
        asyncio.run(scenario())


def test_pair_controle_a_chaque_saut(redirecteur, interne, http_client, monkeypatch):
    """La cible d'une redirection est tout aussi rebindable que l'URL de départ :
    le contrôle doit s'exécuter à chaque itération, pas seulement au premier saut."""
    _public_host(monkeypatch, redirecteur.server_port)

    # La revalidation d'URL refuserait la cible interne avant qu'on y arrive :
    # on la neutralise pour isoler ce qu'on mesure, le contrôle du pair.
    async def toujours_public(url):
        return True

    monkeypatch.setattr(links_mod, "_assert_public_url", toujours_public)

    appels = []
    monkeypatch.setattr(
        links_mod, "_assert_peer_public", lambda resp, url: appels.append(url)
    )
    url = f"http://excerpta-test.invalid:{redirecteur.server_port}/depart"

    async def scenario():
        async with links_mod._safe_stream("GET", url, timeout=5) as resp:
            return resp.status_code

    asyncio.run(scenario())
    assert len(appels) == 2, f"contrôle exécuté {len(appels)} fois, attendu 2 (départ + cible)"
    assert appels[1].endswith("/admin/secret"), f"second contrôle sur {appels[1]}"


# ── Réponses compressées ──────────────────────────────────────────────────────
#
# `safe_request` lit le corps en streaming, ce qui le décode. Recoller ce corps
# aux en-têtes d'origine laissait un `Content-Encoding: gzip` mensonger, et la
# première lecture de `.json()` retentait la décompression. La synchronisation
# FreshRSS était cassée par ce seul défaut : ClientLogin passait (réponse courte
# non compressée), la liste des articles étoilés échouait.


class _ServeurGzip(BaseHTTPRequestHandler):
    """Renvoie du JSON compressé, comme le fait un FreshRSS derrière un proxy."""

    def do_GET(self):
        import gzip as _gzip
        body = _gzip.compress(b'{"items": [{"id": "1"}], "continuation": null}')
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *a):
        pass


@pytest.fixture
def serveur_gzip():
    srv = _serve(_ServeurGzip)
    yield srv
    srv.shutdown()


def test_safe_request_rend_un_corps_lisible_quand_le_serveur_compresse(
    serveur_gzip, http_client, monkeypatch
):
    _public_host(monkeypatch, serveur_gzip.server_port)
    url = f"http://excerpta-test.invalid:{serveur_gzip.server_port}/reader/api/0/stream"

    resp = asyncio.run(links_mod.safe_request("GET", url, timeout=5))

    assert resp.json() == {"items": [{"id": "1"}], "continuation": None}
    assert resp.text.startswith("{")


def test_safe_request_nannonce_plus_un_encodage_consomme(
    serveur_gzip, http_client, monkeypatch
):
    """Les en-têtes rendus doivent décrire le corps rendu, pas celui du réseau."""
    _public_host(monkeypatch, serveur_gzip.server_port)
    url = f"http://excerpta-test.invalid:{serveur_gzip.server_port}/x"

    resp = asyncio.run(links_mod.safe_request("GET", url, timeout=5))

    assert "content-encoding" not in resp.headers, "encodage déjà consommé, ne pas l'annoncer"
    # httpx recalcule Content-Length sur le corps qu'on lui donne : il doit
    # décrire le corps rendu, pas les octets compressés reçus du réseau.
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert resp.headers["content-type"] == "application/json"


def test_greader_starred_lit_une_reponse_compressee(
    serveur_gzip, http_client, monkeypatch
):
    """Bout en bout sur le chemin réellement cassé en production."""
    _public_host(monkeypatch, serveur_gzip.server_port)
    base = f"http://excerpta-test.invalid:{serveur_gzip.server_port}"

    items = asyncio.run(freshrss_mod._greader_starred(base, "jeton"))

    assert items == [{"id": "1"}]


# ── Résolution DNS temporairement indisponible ────────────────────────────────
#
# Au démarrage du conteneur, la boucle FreshRSS part au bout d'une minute et
# tombait parfois avant que le résolveur ne réponde. L'échec était traité comme
# « URL invalide » et la synchronisation repoussée d'un cycle entier, soit
# trente minutes.


def _gaierror(code):
    err = socket.gaierror(code, "simulé")
    err.errno = code
    return err


def test_resolution_reessayee_sur_panne_temporaire(monkeypatch):
    appels = []

    async def resolveur(hostname):
        appels.append(hostname)
        if len(appels) == 1:
            raise _gaierror(socket.EAI_AGAIN)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(links_mod, "_getaddrinfo", resolveur)
    monkeypatch.setattr(links_mod, "_DNS_RETRY_DELAY", 0)
    links_mod._dns_cache.clear()

    assert asyncio.run(links_mod._hostname_resolves_public("exemple.test")) is True
    assert len(appels) == 2, "la panne temporaire doit donner lieu à un réessai"


def test_nom_inconnu_nest_pas_reessaye(monkeypatch):
    """Un nom qui n'existe pas doit être rejeté tout de suite : pas de latence
    ajoutée au refus des URL invalides."""
    appels = []

    async def resolveur(hostname):
        appels.append(hostname)
        raise _gaierror(socket.EAI_NONAME)

    monkeypatch.setattr(links_mod, "_getaddrinfo", resolveur)
    monkeypatch.setattr(links_mod, "_DNS_RETRY_DELAY", 0)
    links_mod._dns_cache.clear()

    assert asyncio.run(links_mod._hostname_resolves_public("inexistant.invalid")) is False
    assert len(appels) == 1, "un nom inconnu ne doit pas être réessayé"


def test_panne_persistante_refusee_sans_mise_en_cache(monkeypatch):
    async def resolveur(hostname):
        raise _gaierror(socket.EAI_AGAIN)

    monkeypatch.setattr(links_mod, "_getaddrinfo", resolveur)
    monkeypatch.setattr(links_mod, "_DNS_RETRY_DELAY", 0)
    links_mod._dns_cache.clear()

    assert asyncio.run(links_mod._hostname_resolves_public("exemple.test")) is False
    assert "exemple.test" not in links_mod._dns_cache, "un échec ne doit pas être mémorisé"


def test_hote_prive_toujours_refuse_apres_reessai(monkeypatch):
    """Le réessai ne doit pas devenir un contournement de la garde."""
    appels = []

    async def resolveur(hostname):
        appels.append(hostname)
        if len(appels) == 1:
            raise _gaierror(socket.EAI_AGAIN)
        return [(2, 1, 6, "", ("192.168.1.10", 0))]

    monkeypatch.setattr(links_mod, "_getaddrinfo", resolveur)
    monkeypatch.setattr(links_mod, "_DNS_RETRY_DELAY", 0)
    links_mod._dns_cache.clear()

    assert asyncio.run(links_mod._hostname_resolves_public("interne.test")) is False
