"""Normalisation de l'URL de base FreshRSS saisie dans les paramètres.

`HTTPS://RSS.exemple.org` fonctionne — urlparse et httpx normalisent ce qu'ils
interprètent — mais la valeur stockée s'affiche telle quelle et sert à des
comparaisons de chaînes. On la range à la saisie.
"""
import pytest

from app.routes.freshrss import normalise_base_url


@pytest.mark.parametrize("saisie,attendu", [
    ("HTTPS://RSS.exemple.org", "https://rss.exemple.org"),
    ("HTTP://RSS.Exemple.ORG/", "http://rss.exemple.org"),
    ("https://rss.exemple.org", "https://rss.exemple.org"),
    ("  https://rss.exemple.org/  ", "https://rss.exemple.org"),
    ("https://rss.exemple.org:8443", "https://rss.exemple.org:8443"),
])
def test_schema_et_hote_en_minuscules(saisie, attendu):
    assert normalise_base_url(saisie) == attendu


def test_casse_du_chemin_preservee():
    """Le chemin est significatif côté serveur, il ne doit pas être touché."""
    assert normalise_base_url("HTTPS://RSS.Exemple.org/FreshRSS") == "https://rss.exemple.org/FreshRSS"


def test_valeur_vide():
    assert normalise_base_url("") == ""
    assert normalise_base_url("   ") == ""
    assert normalise_base_url(None) == ""


def test_saisie_sans_schema_laissee_telle_quelle():
    """Rien à normaliser sans schéma : la garde d'URL la refusera plus loin,
    et la renvoyer inchangée laisse l'utilisateur voir ce qu'il a tapé."""
    assert normalise_base_url("rss.exemple.org") == "rss.exemple.org"


def test_slash_final_retire_une_seule_fois():
    """Les URL sont ensuite construites par f-string avec un / explicite."""
    assert normalise_base_url("https://rss.exemple.org/").endswith("org")
