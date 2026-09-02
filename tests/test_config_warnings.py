"""Non-régression : régénérer SECRET_KEY doit être signalé pour les clés API.

`crypto.hmac_key` dérive de SECRET_KEY seule. Régénérer cette clé invalide donc
toutes les empreintes `users.api_key_hmac`, et l'API comme l'application mobile
répondent 401. L'avertissement existant ne parlait que du déchiffrement et était
conditionné à l'absence d'ENCRYPTION_KEY : avec une ENCRYPTION_KEY renseignée,
les clés API cassaient sans le moindre message.
"""
import logging

import pytest

from app.config import Settings


def _warnings(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_weak_secret_key_warns_about_api_keys(caplog):
    with caplog.at_level(logging.WARNING, logger="excerpta"):
        Settings(secret_key="", encryption_key="k" * 44)

    messages = _warnings(caplog)
    assert "API key" in messages, "la conséquence sur les clés API doit être signalée"
    assert "401" in messages


def test_warning_fires_even_with_encryption_key_set(caplog):
    """Le cas précisément non couvert avant : ENCRYPTION_KEY renseignée."""
    with caplog.at_level(logging.WARNING, logger="excerpta"):
        Settings(secret_key="trop-court", encryption_key="une-cle-fernet-persistante")

    messages = _warnings(caplog)
    assert "API key" in messages
    # L'avertissement sur le déchiffrement ne concerne que l'absence de clé.
    assert "ENCRYPTION_KEY is not set" not in messages


def test_decryption_warning_still_fires_without_encryption_key(caplog):
    with caplog.at_level(logging.WARNING, logger="excerpta"):
        Settings(secret_key="", encryption_key="")

    messages = _warnings(caplog)
    assert "ENCRYPTION_KEY is not set" in messages
    assert "API key" in messages


def test_strong_secret_key_warns_about_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="excerpta"):
        settings = Settings(secret_key="a" * 64, encryption_key="b" * 44)

    assert not caplog.records
    assert settings.secret_key == "a" * 64, "une clé valide ne doit pas être remplacée"


@pytest.mark.parametrize("weak", ["", "changeme", "secret", "court"])
def test_weak_values_are_replaced(weak):
    settings = Settings(secret_key=weak, encryption_key="b" * 44)
    assert settings.secret_key != weak
    assert len(settings.secret_key) >= 32
