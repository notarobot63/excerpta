"""Package `links` : routes liées aux liens, scindées par responsabilité
(voir net_guard/enrichment/crud/proxy/reader/archive). Ce fichier réexporte
tout ce qu'`api.py`, `freshrss.py`, `settings.py` et `main.py` importaient
depuis l'ancien module unique `links.py`, pour que ces imports externes
restent inchangés.
"""
from fastapi import APIRouter

from .archive import _archive_many, _wayback_archive
from .archive import router as _archive_router
from .constants import MAX_DESC_LEN, MAX_TAGS_PER_LINK
from .crud import _fts_escape, create_link
from .crud import router as _crud_router
from .enrichment import _fetch_and_update_meta, _fetch_meta
from .net_guard import (_assert_public_url, _safe_stream, _safe_url, _UnsafeRedirect,
                        safe_request, set_http_client)
from .proxy import warm_img_cache
from .proxy import router as _proxy_router
from .reader import _extract_reader, _READER_ATTRS, _READER_TAGS
from .reader import router as _reader_router

router = APIRouter()
router.include_router(_crud_router)
router.include_router(_proxy_router)
router.include_router(_reader_router)
router.include_router(_archive_router)
