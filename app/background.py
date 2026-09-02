"""Tâches de fond détachées d'une requête.

`asyncio.create_task` ne retient qu'une référence faible à la tâche créée : sans
référence forte gardée ailleurs, le ramasse-miettes peut la collecter avant
qu'elle ne s'achève, et le travail disparaît en silence. C'est documenté dans la
bibliothèque standard, et c'est le cas d'usage exact des désétoilages FreshRSS
et de la vérification des liens, lancés sans que personne n'attende leur
résultat.

`spawn` garde la référence jusqu'à la fin de la tâche, et journalise l'exception
qui n'aurait autrement laissé qu'un « Task exception was never retrieved » au
moment de la collecte, sans contexte utile.

À ne pas confondre avec `BackgroundTasks` de FastAPI, à préférer partout où la
tâche peut attendre la fin de la réponse : elle y est exécutée et supervisée par
le serveur. `spawn` sert les appels lancés hors de ce cadre (boucle de
synchronisation, tâche déjà en cours d'exécution).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

logger = logging.getLogger("excerpta.background")

# Références fortes aux tâches en vol, retirées à leur achèvement.
_running: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _running.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Tâche de fond %s en échec : %s", task.get_name(), exc, exc_info=exc)


def spawn(coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
    """Lance `coro` en tâche de fond en gardant une référence jusqu'à sa fin."""
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_on_done)
    return task


def pending_count() -> int:
    """Nombre de tâches encore en vol. Sert aux tests."""
    return len(_running)
