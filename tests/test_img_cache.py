"""Anti-régression : borne mémoire du cache d'images (app/routes/links/proxy.py).

Le cache n'était plafonné qu'en nombre d'entrées (1000). Une image proxifiée
pouvant peser jusqu'à _MAX_IMG_BYTES (10 Mo), l'empreinte pouvait atteindre
plusieurs gigaoctets et faire tomber le conteneur, sans qu'aucune requête ne
soit anormale prise isolément.
"""
import time

import pytest

from app.routes.links import proxy as proxy_mod


@pytest.fixture(autouse=True)
def cache_vide():
    proxy_mod._img_cache.clear()
    proxy_mod._img_cache_bytes = 0
    yield
    proxy_mod._img_cache.clear()
    proxy_mod._img_cache_bytes = 0


def _store(url: str, taille: int) -> None:
    proxy_mod._cache_store(url, time.time() + 60, "image/png", b"x" * taille)


def test_le_cache_reste_sous_son_plafond_memoire():
    piece = 4 * 1024 * 1024
    combien = proxy_mod._IMG_CACHE_MAX_BYTES // piece + 5
    for i in range(combien):
        _store(f"https://exemple.test/{i}.png", piece)

    assert proxy_mod._img_cache_bytes <= proxy_mod._IMG_CACHE_MAX_BYTES
    assert len(proxy_mod._img_cache) < combien  # des entrées ont bien été évincées


def test_le_plafond_en_nombre_dentrees_reste_applique():
    for i in range(proxy_mod._IMG_CACHE_MAX + 20):
        _store(f"https://exemple.test/{i}.png", 16)
    assert len(proxy_mod._img_cache) == proxy_mod._IMG_CACHE_MAX


def test_le_compteur_suit_les_remplacements_et_les_retraits():
    _store("https://exemple.test/a.png", 1000)
    _store("https://exemple.test/a.png", 300)  # même URL : remplacement
    assert proxy_mod._img_cache_bytes == 300

    proxy_mod._cache_drop("https://exemple.test/a.png")
    assert proxy_mod._img_cache_bytes == 0
    assert "https://exemple.test/a.png" not in proxy_mod._img_cache


def test_une_entree_negative_ne_compte_pas_doctets():
    """Un 404 est mémorisé sans contenu : il occupe une place, pas de mémoire."""
    proxy_mod._cache_store("https://exemple.test/absent.png", time.time() + 60, None, None)
    assert proxy_mod._img_cache_bytes == 0
    assert "https://exemple.test/absent.png" in proxy_mod._img_cache


def test_eviction_par_anciennete():
    _store("https://exemple.test/vieux.png", 8)
    _store("https://exemple.test/recent.png", 8)
    for i in range(proxy_mod._IMG_CACHE_MAX):
        _store(f"https://exemple.test/bourrage{i}.png", 8)
    assert "https://exemple.test/vieux.png" not in proxy_mod._img_cache
