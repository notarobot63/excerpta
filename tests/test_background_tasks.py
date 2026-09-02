"""Non-régression : une tâche de fond ne doit pas pouvoir être ramassée en vol.

`asyncio.create_task` ne garde qu'une référence faible. Les désétoilages
FreshRSS et la vérification des liens étaient lancés sans référence forte : le
ramasse-miettes pouvait les collecter avant leur terme, et le travail
disparaissait sans trace.
"""
import asyncio
import gc
import logging

import pytest

from app.background import pending_count, spawn


def test_spawned_task_survives_garbage_collection():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        done = []

        async def work():
            started.set()
            await release.wait()
            done.append(True)

        spawn(work(), name="test-survivor")
        await started.wait()
        assert pending_count() >= 1, "la tâche doit être référencée pendant son exécution"

        # Un cycle de collecte pendant que la tâche est suspendue : sans
        # référence forte, c'est ici qu'elle disparaissait.
        gc.collect()

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert done == [True], "la tâche n'est pas allée à son terme"

    asyncio.run(scenario())


def test_reference_is_released_after_completion():
    async def scenario():
        before = pending_count()

        async def work():
            return None

        task = spawn(work(), name="test-cleanup")
        await task
        await asyncio.sleep(0)
        assert pending_count() == before, "la référence doit être relâchée à la fin"

    asyncio.run(scenario())


def test_failure_is_logged_not_swallowed(caplog):
    async def scenario():
        async def boom():
            raise RuntimeError("échec attendu")

        task = spawn(boom(), name="test-failure")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger="excerpta.background"):
        asyncio.run(scenario())

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "test-failure" in logged, "l'échec doit être journalisé avec le nom de la tâche"
    assert "échec attendu" in logged, "le message de l'exception doit apparaître"


def test_cancellation_is_not_reported_as_failure(caplog):
    async def scenario():
        async def forever():
            await asyncio.sleep(3600)

        task = spawn(forever(), name="test-cancelled")
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger="excerpta.background"):
        asyncio.run(scenario())

    assert not caplog.records, "une annulation n'est pas un échec"
