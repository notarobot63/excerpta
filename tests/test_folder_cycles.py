"""Non-régression : une hiérarchie de dossiers ne doit jamais contenir de boucle.

Avant correctif, `_validate_parent` n'interdisait que l'auto-parentage direct.
Rendre A parent de B puis B parent de A était accepté, et `sidebar_data` levait
alors un RecursionError sur toute page de l'application.
"""
import pytest
from sqlmodel import Session

from app.models import Folder, User
from app.utils import (build_folder_tree, creates_cycle, folder_cumulative_counts,
                       sidebar_data)


@pytest.fixture
def user(session: Session) -> User:
    u = User(oidc_sub="sub-cycles", name="Cycle")
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _folder(session: Session, user: User, name: str, parent_id=None) -> Folder:
    f = Folder(user_id=user.id, name=name, parent_id=parent_id)
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


# ── Le prédicat ───────────────────────────────────────────────────────────────

def test_creates_cycle_detects_self_parenting():
    assert creates_cycle({1: None}, 1, 1) is True


def test_creates_cycle_detects_indirect_loop():
    # B est sous A : rattacher A sous B fermerait la boucle.
    assert creates_cycle({1: None, 2: 1}, 1, 2) is True


def test_creates_cycle_detects_deep_loop():
    # A -> B -> C : rattacher A sous C ferme une boucle de longueur 3.
    assert creates_cycle({1: None, 2: 1, 3: 2}, 1, 3) is True


def test_creates_cycle_allows_legitimate_moves():
    parents = {1: None, 2: 1, 3: None}
    assert creates_cycle(parents, 3, 1) is False   # 3 sous 1
    assert creates_cycle(parents, 3, 2) is False   # 3 sous 2
    assert creates_cycle(parents, 2, None) is False  # 2 remonte à la racine


def test_creates_cycle_terminates_on_preexisting_loop():
    # Base déjà incohérente : le prédicat doit rendre la main, pas boucler.
    assert creates_cycle({1: 2, 2: 1}, 3, 1) is True


# ── La route ──────────────────────────────────────────────────────────────────

def test_validate_parent_refuses_closing_loop(session: Session, user: User):
    from app.routes.folders import _validate_parent

    a = _folder(session, user, "A")
    b = _folder(session, user, "B", parent_id=a.id)

    # Ce que faisait l'interface d'édition : donner B comme parent à A.
    assert _validate_parent(session, b.id, user.id, exclude_id=a.id) is None
    # Un déplacement légitime reste accepté.
    c = _folder(session, user, "C")
    assert _validate_parent(session, a.id, user.id, exclude_id=c.id) == a.id


def test_validate_parent_still_refuses_other_users_folder(session: Session, user: User):
    from app.routes.folders import _validate_parent

    other = User(oidc_sub="sub-other", name="Autre")
    session.add(other)
    session.commit()
    session.refresh(other)
    theirs = _folder(session, other, "Chez l'autre")
    mine = _folder(session, user, "Chez moi")

    assert _validate_parent(session, theirs.id, user.id, exclude_id=mine.id) is None


# ── Le filet de sécurité en lecture ───────────────────────────────────────────

def test_sidebar_survives_preexisting_cycle(session: Session, user: User):
    """Une base déjà corrompue doit rester consultable, donc réparable."""
    a = _folder(session, user, "A")
    b = _folder(session, user, "B")
    # Écriture directe : reproduit l'état qu'une version antérieure pouvait créer.
    a.parent_id = b.id
    b.parent_id = a.id
    session.add(a)
    session.add(b)
    session.commit()

    data = sidebar_data(session, user.id)  # ne doit pas lever RecursionError

    shown = {f.id for f, _ in data["folder_tree"]}
    assert shown == {a.id, b.id}, "un dossier pris dans un cycle resterait invisible"


def test_cumulative_counts_survive_cycle(session: Session, user: User):
    a = _folder(session, user, "A")
    b = _folder(session, user, "B")
    a.parent_id = b.id
    b.parent_id = a.id
    session.add(a)
    session.add(b)
    session.commit()

    counts = folder_cumulative_counts([a, b], {a.id: 2, b.id: 3})
    assert set(counts) == {a.id, b.id}


def test_tree_unchanged_without_cycle(session: Session, user: User):
    """Le filet ne doit rien changer au cas normal."""
    a = _folder(session, user, "A")
    b = _folder(session, user, "B", parent_id=a.id)
    c = _folder(session, user, "C")

    tree = build_folder_tree([a, b, c])
    assert [(f.name, d) for f, d in tree] == [("A", 0), ("B", 1), ("C", 0)]
