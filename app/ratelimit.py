import asyncio
import ipaddress
import time
from collections import defaultdict

from fastapi import HTTPException, Request

_calls: dict[str, list[float]] = defaultdict(list)
_lock = asyncio.Lock()
_CLEANUP_EVERY = 1000
_cleanup_counter = 0


def _client_ip(request: Request) -> str:
    """Retourne l'IP réelle du client.

    Derrière un reverse proxy (Traefik, nginx), l'hôte connectant est une IP
    privée : on fait alors confiance à X-Real-IP / X-Forwarded-For qu'il injecte.
    Si la connexion vient d'une IP publique directement, ces headers ne sont pas
    de confiance et on utilise l'IP de connexion brute.
    """
    connecting = request.client.host if request.client else None
    if connecting:
        try:
            if ipaddress.ip_address(connecting).is_private:
                ip = (
                    request.headers.get("X-Real-IP")
                    or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                )
                if ip:
                    return ip
        except ValueError:
            pass
    return connecting or "unknown"


def rate_limit(calls: int, period_seconds: int):
    """Dépendance FastAPI : max `calls` appels par `period_seconds` et par endpoint."""
    async def dependency(request: Request) -> None:
        global _cleanup_counter
        client_ip = _client_ip(request)
        key = f"{client_ip}:{request.url.path}"
        now = time.monotonic()
        do_cleanup = False
        async with _lock:
            window = [t for t in _calls[key] if now - t < period_seconds]
            if len(window) >= calls:
                raise HTTPException(
                    status_code=429,
                    detail=f"Trop de requêtes - réessayez dans {period_seconds // 60} min.",
                    headers={"Retry-After": str(period_seconds)},
                )
            window.append(now)
            _calls[key] = window
            _cleanup_counter += 1
            if _cleanup_counter >= _CLEANUP_EVERY:
                _cleanup_counter = 0
                do_cleanup = True
        if do_cleanup:
            dead_keys = [k for k, v in _calls.items() if not v]
            for k in dead_keys:
                del _calls[k]
    return dependency
