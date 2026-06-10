"""Multi-tenant page publique + génération de slug."""
import pytest

from app.models import User
from app.utils import slugify, unique_public_slug


@pytest.mark.parametrize("raw,expected", [
    ("Alice", "alice"),
    ("Thomas Sabatier", "thomas-sabatier"),
    ("Café Crème", "cafe-creme"),
    ("  multiple   espaces  ", "multiple-espaces"),
    ("a/b\\c", "a-b-c"),
    ("", ""),
    ("!!!", ""),
])
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_unique_slug_no_collision(session):
    u = User(oidc_sub="s1", name="Bob")
    session.add(u)
    session.flush()
    assert unique_public_slug(session, "Bob", u.id) == "bob"


def test_unique_slug_collision_suffixes_id(session):
    u1 = User(oidc_sub="s1", name="Bob", public_slug="bob")
    session.add(u1)
    session.flush()
    u2 = User(oidc_sub="s2", name="Bob")
    session.add(u2)
    session.flush()
    slug = unique_public_slug(session, "Bob", u2.id)
    assert slug != "bob"
    assert slug.startswith("bob-")


def test_unique_slug_empty_name_fallback(session):
    u = User(oidc_sub="s1", name="")
    session.add(u)
    session.flush()
    assert unique_public_slug(session, "", u.id) == f"u{u.id}"


def test_slug_is_unique_per_user(session):
    """Régression multi-tenant : deux users ne partagent jamais le même slug."""
    u1 = User(oidc_sub="s1", name="Alice", public_slug="alice")
    u2 = User(oidc_sub="s2", name="Alice")
    session.add_all([u1, u2])
    session.flush()
    u2.public_slug = unique_public_slug(session, "Alice", u2.id)
    session.commit()
    slugs = {u1.public_slug, u2.public_slug}
    assert len(slugs) == 2
