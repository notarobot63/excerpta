"""Non-régression sur les deux derniers points de la revue.

- Le désétoilage en masse rouvrait une session Greader par article et lançait
  tout en parallèle sans borne : trois requêtes par article, toutes émises en
  même temps vers le serveur FreshRSS de l'utilisateur.
- `add_folder` et `edit_folder` acceptaient un nom vide, alors que
  `rename_folder` le refusait déjà : on obtenait un dossier invisible dans la
  barre latérale.
"""
import asyncio

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.models import Folder, FreshRSSConfig, User
from app.routes import freshrss as fr
from app.routes.folders import _validate_name, add_folder, edit_folder


# ── Désétoilage en masse ──────────────────────────────────────────────────────

@pytest.fixture
def config():
    return FreshRSSConfig(
        user_id=1, freshrss_url="https://rss.exemple.test",
        freshrss_user="u", freshrss_token="", group_name="FreshRSS",
    )


@pytest.fixture
def greader_espion(monkeypatch):
    """Compte les ouvertures de session et les appels utiles."""
    compteurs = {"auth": 0, "token": 0, "edit": 0, "simultanes": 0, "max_simultanes": 0}

    async def fake_auth(*a, **k):
        compteurs["auth"] += 1
        return "jeton-auth"

    async def fake_token(*a, **k):
        compteurs["token"] += 1
        return "jeton-t"

    async def fake_edit(cfg, auth, t_token, item_id):
        compteurs["edit"] += 1
        compteurs["simultanes"] += 1
        compteurs["max_simultanes"] = max(compteurs["max_simultanes"], compteurs["simultanes"])
        await asyncio.sleep(0.01)
        compteurs["simultanes"] -= 1
        return True

    monkeypatch.setattr(fr, "_greader_auth", fake_auth)
    monkeypatch.setattr(fr, "_greader_token", fake_token)
    monkeypatch.setattr(fr, "_edit_tag_unstar", fake_edit)
    monkeypatch.setattr(fr, "decrypt", lambda v: "clair")
    return compteurs


def test_une_seule_session_pour_tout_le_lot(config, greader_espion):
    echecs = asyncio.run(fr.unstar_items(config, [f"item-{i}" for i in range(40)]))

    assert echecs == 0
    assert greader_espion["edit"] == 40, "chaque article doit être traité"
    assert greader_espion["auth"] == 1, "une seule authentification pour le lot"
    assert greader_espion["token"] == 1, "un seul jeton d'écriture pour le lot"


def test_parallelisme_borne(config, greader_espion):
    asyncio.run(fr.unstar_items(config, [f"item-{i}" for i in range(40)]))

    assert greader_espion["max_simultanes"] <= fr._UNSTAR_CONCURRENCY, (
        f"{greader_espion['max_simultanes']} appels simultanés, "
        f"plafond attendu {fr._UNSTAR_CONCURRENCY}"
    )


def test_lot_vide_nouvre_aucune_session(config, greader_espion):
    assert asyncio.run(fr.unstar_items(config, [])) == 0
    assert greader_espion["auth"] == 0, "rien à faire ne doit rien ouvrir"


def test_session_refusee_compte_tous_les_echecs(config, monkeypatch):
    async def auth_ko(*a, **k):
        raise RuntimeError("auth refusée")

    monkeypatch.setattr(fr, "_greader_auth", auth_ko)
    monkeypatch.setattr(fr, "decrypt", lambda v: "clair")

    assert asyncio.run(fr.unstar_items(config, ["a", "b", "c"])) == 3


def test_unstar_item_reste_compatible(config, greader_espion):
    """L'appel unitaire, utilisé par la suppression et le déplacement de lien."""
    assert asyncio.run(fr.unstar_item(config, "item-1")) is True
    assert greader_espion["edit"] == 1
    assert greader_espion["auth"] == 1


# ── Nom de dossier ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vide", ["", "   ", "\t", "\n  "])
def test_nom_vide_refuse(vide):
    with pytest.raises(HTTPException) as exc:
        _validate_name(vide)
    assert exc.value.status_code == 422


def test_espaces_de_bordure_retires():
    assert _validate_name("  Veille  ") == "Veille"


def test_add_folder_refuse_un_nom_vide(session: Session):
    user = User(oidc_sub="sub-nom", name="Nom")
    session.add(user)
    session.commit()
    session.refresh(user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_folder(name="   ", is_public=None, parent_id=None, user=user, session=session))
    assert exc.value.status_code == 422
    assert session.query(Folder).count() == 0, "aucun dossier ne doit être créé"


def test_edit_folder_refuse_un_nom_vide(session: Session):
    user = User(oidc_sub="sub-nom2", name="Nom")
    session.add(user)
    session.flush()
    folder = Folder(user_id=user.id, name="Avant")
    session.add(folder)
    session.commit()
    session.refresh(user)
    session.refresh(folder)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(edit_folder(folder_id=folder.id, name="", is_public=None,
                                parent_id=None, user=user, session=session))
    assert exc.value.status_code == 422
    session.refresh(folder)
    assert folder.name == "Avant", "le nom d'origine doit être conservé"
