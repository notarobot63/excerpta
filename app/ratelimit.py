import asyncio
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
    """Retourne l'IP réelle du client, telle qu'uvicorn l'a résolue.

    Aucun en-tête n'est relu ici. uvicorn (`--proxy-headers`) a déjà remplacé
    `request.client` par l'adresse extraite de X-Forwarded-For, selon la liste
    de proxys de confiance FORWARDED_ALLOW_IPS : c'est le seul endroit qui sait
    quels sauts croire. Relire les en-têtes ici était pire qu'inutile : avec
    `FORWARDED_ALLOW_IPS=*`, uvicorn prenait déjà le premier élément, écrit par
    le client, et une adresse privée forgée ouvrait en plus la lecture de
    X-Real-IP, tout aussi forgeable. Voir le Dockerfile.
    """
    return request.client.host if request.client else "unknown"


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
