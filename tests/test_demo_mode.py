"""Contrat de sécurité du mode démo.

Ces tests existent pour qu'une régression soit bruyante. Le mode démo ouvre
l'application à des inconnus : chacune des garanties vérifiées ici est ce qui
empêche un visiteur de faire sortir une requête du serveur, de publier quoi que
ce soit, ou de laisser des données derrière lui.
"""
import os

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test" * 16)

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import demo
from app.config import settings
from app.models import Folder, Link, Tag, User


@pytest.fixture
def demo_mode():
    """Active le mode démo pour la durée du test, puis le remet à sa valeur."""
    previous = settings.demo_mode
    settings.demo_mode = True
    yield
    settings.demo_mode = previous


@pytest.fixture
def demo_user(session, demo_mode):
    return demo.create_demo_space(session)


def test_espace_cree_avec_dossiers_et_liens(session, demo_user):
    folders = session.exec(select(Folder).where(Folder.user_id == demo_user.id)).all()
    links = session.exec(select(Link).where(Link.user_id == demo_user.id)).all()
    assert {f.name for f in folders} == set(demo.DEMO_FOLDERS)
    assert len(links) == sum(1 for e in demo.CATALOG if e.get("seed"))
    # Une démo qui s'ouvre sur une page vide ne démontre rien.
    assert links


def test_aucun_lien_de_demo_n_est_public(session, demo_user):
    """La publication est ce qui exposerait le contenu d'un inconnu au public."""
    links = session.exec(select(Link).where(Link.user_id == demo_user.id)).all()
    assert all(link.is_public is False for link in links)
    assert demo_user.public_slug is None


def test_aucun_archivage_planifie(session, demo_user):
    """archive_status à 'pending' déclencherait un envoi vers la Wayback Machine."""
    links = session.exec(select(Link).where(Link.user_id == demo_user.id)).all()
    assert all(link.archive_status is None for link in links)


def test_aucune_url_d_image_a_recuperer(session, demo_user):
    """Favicon et vignette vides : le proxy d'images n'a rien à aller chercher."""
    links = session.exec(select(Link).where(Link.user_id == demo_user.id)).all()
    assert all(link.favicon_url == "" and link.thumbnail_url == "" for link in links)


def test_ajout_hors_catalogue_refuse(session, demo_user):
    with pytest.raises(HTTPException) as exc:
        demo.add_catalog_link(session, demo_user, "https://exemple.invalid/page")
    assert exc.value.status_code == 400


def test_ajout_depuis_le_catalogue_accepte(session, demo_user):
    entry = next(e for e in demo.CATALOG if not e.get("seed"))
    link = demo.add_catalog_link(session, demo_user, entry["url"])
    assert link.title == entry["title"]
    assert link.is_public is False
    assert link.archive_status is None


def test_ajout_du_catalogue_idempotent(session, demo_user):
    entry = next(e for e in demo.CATALOG if not e.get("seed"))
    first = demo.add_catalog_link(session, demo_user, entry["url"])
    second = demo.add_catalog_link(session, demo_user, entry["url"])
    assert first.id == second.id


def test_espaces_isoles_entre_visiteurs(session, demo_mode):
    a = demo.create_demo_space(session)
    b = demo.create_demo_space(session)
    assert a.id != b.id
    assert a.oidc_sub != b.oidc_sub
    links_a = session.exec(select(Link).where(Link.user_id == a.id)).all()
    assert all(link.user_id == a.id for link in links_a)


def test_utilisateur_de_demo_jamais_administrateur(session, demo_user):
    assert demo_user.is_admin is False


def test_reconnaissance_d_un_utilisateur_de_demo(session, demo_user):
    assert demo.is_demo_user(demo_user) is True
    assert demo.is_demo_user_id(session, demo_user.id) is True
    ordinaire = User(oidc_sub="pocketid|42", email="a@b.c", name="Réel")
    session.add(ordinaire)
    session.commit()
    assert demo.is_demo_user(ordinaire) is False


def test_forbid_in_demo_bloque_le_visiteur_et_epargne_l_utilisateur_reel(session, demo_user):
    with pytest.raises(HTTPException) as exc:
        demo.forbid_in_demo(demo_user)
    assert exc.value.status_code == 403

    ordinaire = User(oidc_sub="pocketid|43", email="d@e.f", name="Réel")
    session.add(ordinaire)
    session.commit()
    demo.forbid_in_demo(ordinaire)  # ne lève pas


def test_forbid_in_demo_inactif_hors_mode_demo(session):
    """Sans DEMO_MODE, la présence du marqueur ne doit rien restreindre."""
    settings.demo_mode = False
    user = User(oidc_sub=f"{demo.DEMO_SUB_PREFIX}orphelin", email="", name="")
    session.add(user)
    session.commit()
    demo.forbid_in_demo(user)  # ne lève pas


def test_purge_supprime_l_espace_expire_en_entier(session, demo_mode):
    """Sans purge complète, la base grossit indéfiniment : les relations du
    projet ne déclarent pas de cascade au niveau SQL."""
    user = demo.create_demo_space(session)
    user_id = user.id
    assert session.exec(select(Link).where(Link.user_id == user_id)).all()

    supprimes = demo.purge_expired_demo_users(session, ttl_hours=0)

    assert supprimes == 1
    assert session.get(User, user_id) is None
    assert session.exec(select(Link).where(Link.user_id == user_id)).all() == []
    assert session.exec(select(Tag).where(Tag.user_id == user_id)).all() == []
    assert session.exec(select(Folder).where(Folder.user_id == user_id)).all() == []


def test_purge_epargne_les_espaces_recents(session, demo_mode):
    user = demo.create_demo_space(session)
    assert demo.purge_expired_demo_users(session, ttl_hours=6) == 0
    assert session.get(User, user.id) is not None


def test_purge_epargne_les_comptes_reels(session, demo_mode):
    """Un compte OIDC ne doit jamais être emporté par la purge, même ancien."""
    from datetime import datetime, timedelta, timezone

    reel = User(
        oidc_sub="pocketid|99",
        email="vrai@exemple.fr",
        name="Compte réel",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400),
    )
    session.add(reel)
    session.commit()

    demo.purge_expired_demo_users(session, ttl_hours=0)

    assert session.get(User, reel.id) is not None


def test_toutes_les_entrees_ont_un_contenu_lecteur():
    """Sans contenu préparé, la vue lecteur affiche « indisponible » : en démo
    l'extraction est coupée, il n'y a donc aucun rattrapage possible."""
    sans_lecteur = [e["url"] for e in demo.CATALOG if not e.get("reader")]
    assert sans_lecteur == []


def test_le_contenu_lecteur_respecte_les_balises_autorisees():
    """reader_html est inséré tel quel dans la page (filtre `safe`) : il doit
    déjà être conforme à ce que la sanitisation nh3 laisserait passer."""
    import nh3

    from app.routes.links import _READER_ATTRS, _READER_TAGS

    for entree in demo.CATALOG:
        propre = nh3.clean(entree["reader"], tags=_READER_TAGS, attributes=_READER_ATTRS)
        assert propre == entree["reader"], entree["url"]


def test_catalogue_sans_doublon_et_dossiers_declares():
    urls = [e["url"] for e in demo.CATALOG]
    assert len(urls) == len(set(urls))
    assert all(e["folder"] in demo.DEMO_FOLDERS for e in demo.CATALOG)
    assert set(demo.CATALOG_BY_URL) == set(urls)
