import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request

_calls: dict[str, list[float]] = defaultdict(list)
_lock = Lock()
_CLEANUP_EVERY = 1000
_cleanup_counter = 0


def rate_limit(calls: int, period_seconds: int):
    """Dépendance FastAPI : max `calls` appels par `period_seconds` et par endpoint."""
    def dependency(request: Request) -> None:
        global _cleanup_counter
        client_ip = (request.client.host if request.client else "unknown")
        key = f"{client_ip}:{request.url.path}"
        now = time.monotonic()
        with _lock:
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
                dead_keys = [k for k, v in _calls.items() if not v]
                for k in dead_keys:
                    del _calls[k]
    return dependency
