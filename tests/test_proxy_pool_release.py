"""Anti-régression : les routes qui font un appel HTTP externe lent après
l'authentification (proxy_image, api_fetch_meta) doivent libérer leur
connexion DB avant cet appel, sous peine de saturer le pool SQLAlchemy
(5 + 10 overflow par défaut) dès qu'une page charge plusieurs vignettes
en parallèle. Voir incident du 2026-07-11 : QueuePool timeout pendant
qu'une rafale de /proxy/img tenait toutes les connexions ouvertes.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from app.routes import links


def test_session_close_releases_connection_immediately(engine):
    """Propriété SQLAlchemy dont dépend le fix : fermer la session la rend
    au pool tout de suite, pas seulement à la sortie du `with`."""
    pool = engine.pool

    with Session(engine) as session:
        session.exec(__import__("sqlalchemy").text("SELECT 1"))
        assert pool.checkedout() == 1
        session.close()
        assert pool.checkedout() == 0


class _FakeRequest:
    headers = {}


@pytest.mark.anyio
async def test_proxy_image_closes_session_before_http_call():
    """Le coeur du fix : proxy_image doit fermer sa session DB avant
    d'attendre la réponse HTTP externe, pas après."""
    session = MagicMock()
    events = []
    session.close.side_effect = lambda: events.append("session_closed")

    fake_stream_cm = MagicMock()
    fake_resp = MagicMock(status_code=404)  # sort tôt, pas besoin d'aller plus loin

    async def _aenter():
        events.append("http_call_started")
        return fake_resp

    fake_stream_cm.__aenter__ = AsyncMock(side_effect=_aenter)
    fake_stream_cm.__aexit__ = AsyncMock(return_value=False)

    fake_client = MagicMock()
    fake_client.stream = MagicMock(return_value=fake_stream_cm)
    original_client = links._http_client
    links.set_http_client(fake_client)

    with patch.object(links, "_safe_url", return_value=True):
        try:
            await links.proxy_image(
                request=_FakeRequest(),
                url="http://example.com/thumb.png",
                user=MagicMock(),
                session=session,
            )
        except links.HTTPException:
            pass  # le 404 simulé lève HTTPException(404), attendu
        finally:
            links.set_http_client(original_client)

    session.close.assert_called_once()
    assert events == ["session_closed", "http_call_started"], (
        "la session doit être fermée AVANT l'appel HTTP externe, pas après : " f"{events}"
    )


@pytest.mark.anyio
async def test_api_fetch_meta_closes_session_before_http_call():
    session = MagicMock()
    events = []
    session.close.side_effect = lambda: events.append("session_closed")

    async def _fake_fetch_meta(url):
        events.append("http_call_started")
        return {"title": "", "description": "", "favicon_url": ""}

    with patch.object(links, "_fetch_meta", side_effect=_fake_fetch_meta):
        await links.api_fetch_meta(url="http://example.com", user=MagicMock(), session=session)

    session.close.assert_called_once()
    assert events == ["session_closed", "http_call_started"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
