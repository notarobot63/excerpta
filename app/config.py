import logging
import secrets

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("excerpta")

_WEAK_KEYS = {"changeme", "changeme_generate_a_real_one", "secret", ""}


class Settings(BaseSettings):
    database_url: str = "sqlite:////app/data/excerpta.db"
    secret_key: str = ""
    base_url: str = "http://localhost:8000"
    extra_allowed_hosts: str = ""  # hostnames additionnels autorisés (CSV), ex: domaine miroir de la page publique
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_issuer: str = ""
    app_name: str = "Excerpta"
    admin_email: str = ""
    freshrss_sync_interval: int = 30  # minutes entre chaque sync automatique
    encryption_key: str = ""  # Clé Fernet dédiée (indépendante de secret_key)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        if self.secret_key in _WEAK_KEYS or len(self.secret_key) < 32:
            generated = secrets.token_hex(32)
            logger.warning(
                "⚠️  SECRET_KEY absente ou non sécurisée. "
                "Clé temporaire générée - les sessions expireront au prochain redémarrage. "
                "Définissez SECRET_KEY=%s dans docker-compose.yml",
                generated,
            )
            self.secret_key = generated
        return self


settings = Settings()
