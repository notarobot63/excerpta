import asyncio
import ipaddress
import time
from collections import defaultdict

from fastapi import HTTPException, Request

# clé -> (période de la fenêtre en secondes, horodatages des appels).
# La période est stockée par clé : _calls est partagé par tous les limiteurs
# (de 60 s à 3600 s), et un balayage ne peut pas appliquer la période de celui
# qui le déclenche aux entrées des autres, sous peine de remettre à zéro leurs
# quotas.
_calls: dict[str, tuple[int, list[float]]] = {}
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
                # X-Forwarded-For est une liste que le proxy *complète* : le
                # client contrôle les premiers éléments. Le seul saut de
                # confiance est le DERNIER, ajouté par notre propre proxy.
                # Prendre [0] laissait un client forger une IP arbitraire et
                # obtenir un compteur neuf à chaque requête.
                ip = request.headers.get("X-Real-IP", "").strip()
                if not ip:
                    forwarded = request.headers.get("X-Forwarded-For", "")
                    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
                    ip = parts[-1] if parts else ""
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
            previous = _calls.get(key)
            window = [t for t in previous[1] if now - t < period_seconds] if previous else []
            if len(window) >= calls:
                raise HTTPException(
                    status_code=429,
                    detail=f"Trop de requêtes - réessayez dans {period_seconds // 60} min.",
                    headers={"Retry-After": str(period_seconds)},
                )
            window.append(now)
            _calls[key] = (period_seconds, window)
            _cleanup_counter += 1
            if _cleanup_counter >= _CLEANUP_EVERY:
                _cleanup_counter = 0
                do_cleanup = True
        if do_cleanup:
            # Sans balayage actif, une entrée n'était libérée que si sa fenêtre
            # était vide, ce qui n'arrive jamais : le dict croissait
            # indéfiniment (une entrée par couple IP × endpoint). Chaque clé est
            # évaluée avec SA propre période, pas celle du limiteur déclencheur.
            async with _lock:
                stale = [
                    k for k, (kperiod, ts) in _calls.items()
                    if not ts or now - max(ts) >= kperiod
                ]
                for k in stale:
                    del _calls[k]
    return dependency
