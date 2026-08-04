"""Non-régression sur le chargement de la configuration.

Le fichier `.env` est aussi l'`env_file` de docker-compose : il porte donc des
variables destinées à l'infrastructure et non à l'application (REGISTRY_IMAGE,
FORWARDED_ALLOW_IPS lue par uvicorn…). Avec le `extra="forbid"` par défaut de
pydantic-settings, chacune d'elles fait échouer l'instanciation de Settings,
donc le démarrage du conteneur, avec une erreur de validation obscure.
"""
import pytest

from app.config import Settings, _WEAK_KEYS


def test_variables_dinfra_dans_le_env_ne_bloquent_pas_le_demarrage(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "SECRET_KEY=" + "a" * 64 + "\n"
        "FORWARDED_ALLOW_IPS=10.0.0.2\n"
        "REGISTRY_IMAGE=registry.example/excerpta:latest\n"
    )
    # Sans cela, les variables du processus de test prendraient le pas sur le fichier.
    for name in ("SECRET_KEY", "TESTING"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=str(env))

    assert settings.secret_key == "a" * 64


def test_une_secret_key_faible_est_remplacee(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    for weak in _WEAK_KEYS:
        settings = Settings(_env_file=None, secret_key=weak)
        assert settings.secret_key not in _WEAK_KEYS
        assert len(settings.secret_key) >= 32


@pytest.mark.parametrize("champ,defaut", [
    ("session_cookie_secure", True),
    ("require_verified_email", True),
    ("demo_mode", False),
])
def test_defauts_surs(champ, defaut, monkeypatch):
    """Ces trois défauts sont des choix de sécurité : un défaut inversé ouvre
    l'instance (cookie en clair, admin sans email vérifié, accès sans compte)."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert getattr(Settings(_env_file=None), champ) is defaut
