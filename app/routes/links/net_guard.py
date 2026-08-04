"""Garde SSRF unique + client HTTP partagé, utilisés par tout le package `links`.

Extrait tel quel de l'ancien `app/routes/links.py` (aucun changement de
comportement) : `_assert_public_url` doit être appelé avant toute requête
sortante, `_safe_stream` revalide chaque saut de redirection.
"""
import asyncio
import ipaddress
import socket
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx

_http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient) -> None:
    global _http_client
    _http_client = client


# Plafonds de téléchargement (anti-DoS mémoire)
_MAX_HTML_BYTES = 5 * 1024 * 1024   # 5 Mo pour le HTML d'une page (extraction meta / reader)
_MAX_API_BYTES = 10 * 1024 * 1024   # 10 Mo pour une réponse d'API JSON (page d'articles FreshRSS)
_MAX_REDIRECTS = 5


class _TooLarge(Exception):
    pass


class _UnsafeRedirect(Exception):
    """Redirection vers une cible que la garde SSRF refuse."""


async def _read_limited(resp: httpx.Response, max_bytes: int) -> bytes:
    """Lit le corps d'une réponse httpx en streaming, en coupant à max_bytes.

    Rejette tôt via Content-Length si annoncé, sinon borne pendant la lecture.
    L'appelant doit avoir ouvert resp via client.stream(...).
    """
    cl = resp.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > max_bytes:
                raise _TooLarge()
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise _TooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


_DNS_CACHE_TTL = 600
_dns_cache: dict[str, tuple[float, bool]] = {}

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Vraie/fausse pour une IP déjà parsée, en dépliant les formes IPv4-mapped.

    `::ffff:127.0.0.1` est une adresse IPv6 qui désigne 127.0.0.1 : elle
    n'appartient à aucun réseau IPv4 de _PRIVATE_NETS, d'où la nécessité de la
    replier sur son IPv4 avant comparaison.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or getattr(ip, "sixtofour", None)
        if mapped is not None:
            ip = mapped
    if any(ip in net for net in _PRIVATE_NETS):
        return True
    # Filet de sécurité pour tout ce que _PRIVATE_NETS n'énumère pas
    # (multicast, réservé, documentation, 198.18.0.0/15 bench, etc.).
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_private_host(host: str) -> bool:
    if not host:
        return True
    if host.lower() in ("localhost", "local", "broadcasthost", "0.0.0.0"):
        return True
    try:
        return _is_private_ip(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return False  # hostname DNS valide - autorisé


async def _hostname_resolves_public(hostname: str) -> bool:
    """Anti-DNS-rebinding : vérifie que le hostname ne résout pas vers une IP privée."""
    now = time.time()
    cached = _dns_cache.get(hostname)
    if cached and now < cached[0]:
        return cached[1]
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
        result = all(not _is_private_host(r[4][0]) for r in results)
        _dns_cache[hostname] = (now + _DNS_CACHE_TTL, result)
        return result
    except OSError:
        return False


def _safe_url(url: str) -> bool:
    if not url or url.startswith("//"):
        return False
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return False
        return not _is_private_host(p.hostname or "")
    except Exception:
        return False


async def _assert_public_url(url: str) -> bool:
    """Garde SSRF unique, à appeler avant toute requête sortante.

    Combine les deux contrôles auparavant recopiés dans _fetch_meta,
    proxy_image et _extract_reader : forme de l'URL + IP littérale privée,
    puis résolution DNS pour les hostnames.
    """
    if not _safe_url(url):
        return False
    host = (urlparse(url).hostname or "").strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return await _hostname_resolves_public(host)
    return True  # IP littérale déjà validée par _safe_url


@asynccontextmanager
async def _safe_stream(method: str, url: str, same_host_only: bool = False, **kwargs):
    """Ouvre une requête sortante en validant CHAQUE saut de redirection.

    httpx avec follow_redirects=True suit un 302 vers une cible interne et ne
    laisse à l'appelant que l'URL finale : la requête interne a déjà été émise.
    On désactive donc le suivi automatique et on revalide la garde SSRF avant
    de suivre chaque Location.

    `same_host_only` refuse en plus tout changement d'hôte. À utiliser dès que
    la requête porte des identifiants (httpx rejoue le corps et les en-têtes sur
    un 307/308) : sans cela, une redirection les livrerait à l'hôte d'arrivée,
    fût-il public.

    L'appelant doit avoir validé `url` via _assert_public_url au préalable.
    """
    remaining = _MAX_REDIRECTS
    current = url
    while True:
        async with _http_client.stream(method, current, follow_redirects=False, **kwargs) as resp:
            location = resp.headers.get("location") if resp.is_redirect else None
            if not location:
                yield resp
                return
        remaining -= 1
        if remaining < 0:
            raise _UnsafeRedirect("trop de redirections")
        previous = current
        current = str(httpx.URL(current).join(location))
        if not await _assert_public_url(current):
            raise _UnsafeRedirect(f"redirection refusée vers {current}")
        if same_host_only and httpx.URL(current).host != httpx.URL(previous).host:
            raise _UnsafeRedirect(f"redirection hors hôte refusée vers {current}")


async def safe_request(
    method: str,
    url: str,
    max_bytes: int = _MAX_API_BYTES,
    same_host_only: bool = False,
    **kwargs,
) -> httpx.Response:
    """Requête sortante complète (corps lu et borné) derrière la garde SSRF.

    Équivalent de `client.request(...)` pour les appels dont on veut le corps
    d'un coup — API JSON, réponses courtes — là où `_safe_stream` sert les
    téléchargements. Valide l'URL de départ, revalide chaque redirection, et
    plafonne la taille lue.
    """
    if not await _assert_public_url(url):
        raise _UnsafeRedirect(f"URL refusée : {url}")
    async with _safe_stream(method, url, same_host_only=same_host_only, **kwargs) as resp:
        body = await _read_limited(resp, max_bytes)
        status, headers, request = resp.status_code, resp.headers, resp.request
    return httpx.Response(status_code=status, headers=headers, content=body, request=request)
