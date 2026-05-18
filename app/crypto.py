import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    raw = hashlib.sha256(settings.secret_key.encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value or not value.startswith("gAAAAA"):
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
