"""Tests dossiers : clé de tri alphabétique et réordonnancement par frères.

On teste la logique métier directement (clé de tri + build_folder_tree),
sans monter FastAPI - cf. conftest.
"""
from collections import defaultdict

from sqlmodel import select

from app.models import Folder, User
from app.utils import build_folder_tree, folder_alpha_key


def test_folder_alpha_key_ignore_casse_et_accents():
    noms = ["Zèbre", "école", "Archives", "bateau", "Éléphant", "archives"]
    assert sorted(noms, key=folder_alpha_key) == [
        "Archives", "archives", "bateau", "école", "Éléphant", "Zèbre",
    ]


def _sort_alpha(folders):
    """Reproduit la logique de la route /folders/sort-alpha."""
    groups = defaultdict(list)
    for f in folders:
        groups[f.parent_id].append(f)
    for siblings in groups.values():
        siblings.sort(key=lambda f: folder_alpha_key(f.name))
        for i, f in enumerate(siblings):
            f.sort_order = i


def _make_user(session, sub):
    user = User(oidc_sub=sub, email=f"{sub}@example.com", public_slug=sub)
    session.add(user)
    session.commit()
    return user


def test_sort_alpha_par_groupe_de_freres(session):
    user = _make_user(session, "sorter")

    # Racine dans le désordre + sous-dossiers d'un parent dans le désordre
    veille = Folder(user_id=user.id, name="Veille", sort_order=0)
    archives = Folder(user_id=user.id, name="archives", sort_order=1)
    session.add_all([veille, archives])
    session.commit()

    session.add_all([
        Folder(user_id=user.id, name="Zoom", parent_id=veille.id, sort_order=0),
        Folder(user_id=user.id, name="Actu", parent_id=veille.id, sort_order=1),
    ])
    session.commit()

    _sort_alpha(list(session.exec(select(Folder)).all()))
    session.commit()

    tree = build_folder_tree(list(session.exec(select(Folder)).all()))
    noms = [f.name for f, _ in tree]
    # Racine triée (archives avant Veille), enfants de Veille triés (Actu avant Zoom)
    assert noms == ["archives", "Veille", "Actu", "Zoom"]


def test_rename_folder_logique(session):
    user = _make_user(session, "renamer")
    f = Folder(user_id=user.id, name="Ancien", sort_order=0)
    session.add(f)
    session.commit()

    f.name = "Nouveau"
    session.add(f)
    session.commit()

    assert session.get(Folder, f.id).name == "Nouveau"
