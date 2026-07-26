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
    require_verified_email: bool = True  # n'accorde l'admin qu'à un email vérifié par l'IdP
    freshrss_sync_interval: int = 30  # minutes entre chaque sync automatique
    encryption_key: str = ""  # Clé Fernet dédiée (indépendante de secret_key)
    session_cookie_secure: bool = True  # flag Secure sur le cookie de session
    testing: bool = False  # autorise le host "testserver" (TestClient Starlette)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        if self.secret_key in _WEAK_KEYS or len(self.secret_key) < 32:
            # La clé générée n'est jamais journalisée : les logs sont
            # centralisés, l'y écrire en clair reviendrait à publier le secret
            # qui signe les sessions.
            logger.warning(
                "SECRET_KEY missing or too weak. A temporary one was generated: "
                "sessions will expire on the next restart. Set a persistent "
                "SECRET_KEY (python3 -c 'import secrets; "
                "print(secrets.token_hex(32))') in your .env."
            )
            self.secret_key = secrets.token_hex(32)
        return self


settings = Settings()
