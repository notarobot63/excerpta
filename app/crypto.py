import base64
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_log = logging.getLogger("excerpta.crypto")

_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        src = settings.encryption_key or settings.secret_key
        raw = hashlib.sha256(src.encode()).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(raw))
    return _fernet_instance


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return value
    if not value.startswith("gAAAAA"):
        _log.warning("decrypt: received unencrypted value, check ENCRYPTION_KEY")
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        _log.error(
            "decrypt: échec de déchiffrement (clé invalide ou rotée depuis le "
            "chiffrement de cette valeur ?) — valeur traitée comme vide",
            exc_info=True,
        )
        return ""


def hmac_key(value: str) -> str:
    """Empreinte de recherche d'une clé API (users.api_key_hmac).

    Dérive de `secret_key` seule, et non de `encryption_key or secret_key`
    comme `_fernet` : changer SECRET_KEY invalide donc toutes les clés API
    existantes, même avec une ENCRYPTION_KEY stable. Aligner les deux
    demanderait de réécrire les empreintes déjà stockées, ce que `config.py`
    signale plutôt par un avertissement au démarrage.
    """
    return hmac.new(
        settings.secret_key.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def is_encrypted(value: str) -> bool:
    return value.startswith("gAAAAA")
