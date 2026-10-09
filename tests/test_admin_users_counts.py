"""Liste des utilisateurs de l'admin : compteurs exacts, sans produit cartésien.

Les deux LEFT JOIN (liens, étiquettes) multipliaient les lignes avant le
COUNT(DISTINCT). Le résultat restait juste mais coûtait liens × étiquettes
lignes par compte ; le passage aux sous-requêtes ne doit changer aucun compteur.
"""
import asyncio
from datetime import datetime

from app.models import Link, Tag, User
from app.routes import admin


def test_compteurs_par_utilisateur(session, monkeypatch):
    a = User(oidc_sub="a", public_slug="a", is_admin=True)
    b = User(oidc_sub="b", public_slug="b")
    session.add_all([a, b])
    session.commit()
    session.add_all([Link(user_id=a.id, url=f"https://{n}.example", created_at=datetime(2026, 1, n + 1))
                     for n in range(3)])
    session.add_all([Tag(user_id=a.id, name=f"t{n}") for n in range(4)])
    session.commit()

    captured = {}
    monkeypatch.setattr(admin.templates, "TemplateResponse",
                        lambda request, tpl, ctx, *a, **k: captured.update(ctx))
    monkeypatch.setattr(admin, "sidebar_data", lambda *args: {})
    asyncio.run(admin.users_list(request=None, admin=a, session=session))

    by_id = {r[0]: r for r in captured["rows"]}
    assert by_id[a.id][6:8] == (3, 4)
    assert str(by_id[a.id][8]).startswith("2026-01-03")
    assert by_id[b.id][6:9] == (0, 0, None)

