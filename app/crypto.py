import base64
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_log = logging.getLogger("excerpta.crypto")


def _fernet() -> Fernet:
    src = settings.encryption_key or settings.secret_key
    raw = hashlib.sha256(src.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return value
    if not value.startswith("gAAAAA"):
        _log.warning("decrypt: valeur non chiffrée reçue — vérifiez ENCRYPTION_KEY")
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def hmac_key(value: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def is_encrypted(value: str) -> bool:
    return value.startswith("gAAAAA")
