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

    # Mode démo : instance publique jetable, à n'activer QUE sur l'instance de
    # démonstration isolée, jamais sur une instance qui détient de vraies données.
    # Il ouvre l'application sans authentification, ce qui n'a de sens que sur une
    # base dédiée dont le contenu est destiné à être détruit.
    demo_mode: bool = False
    demo_ttl_hours: int = 6  # durée de vie d'un espace de démo avant purge

    # extra="ignore" : le même .env sert de env_file à docker-compose, il porte
    # donc aussi des variables d'infrastructure (REGISTRY_IMAGE,
    # FORWARDED_ALLOW_IPS lue par uvicorn…). Avec le "forbid" par défaut de
    # pydantic-settings, chacune d'elles empêche l'application de démarrer.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        was_weak = self.secret_key in _WEAK_KEYS or len(self.secret_key) < 32
        if was_weak:
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
            # crypto.hmac_key dérive de secret_key SEULE, quelle que soit la
            # valeur d'ENCRYPTION_KEY : les empreintes stockées dans
            # users.api_key_hmac ne correspondent plus, et toute clé API
            # existante est refusée en 401. L'avertissement ne portait que sur
            # le déchiffrement, il ne se déclenchait donc pas quand
            # ENCRYPTION_KEY était renseignée, alors que les clés API cassaient
            # tout autant : c'était un échec silencieux, y compris pour
            # l'application mobile.
            logger.warning(
                "SECRET_KEY was just regenerated: existing API keys will be "
                "rejected (401), because their lookup fingerprint is derived "
                "from SECRET_KEY. Set a persistent SECRET_KEY, or regenerate "
                "each API key from the admin panel."
            )
            if not self.encryption_key:
                # crypto.py dérive aussi sa clé Fernet de secret_key quand
                # encryption_key est vide : régénérer secret_key à chaque
                # redémarrage rend alors indéchiffrables les secrets déjà
                # chiffrés en base (api_key, freshrss_token).
                logger.warning(
                    "ENCRYPTION_KEY is not set and SECRET_KEY was just "
                    "regenerated: any value already encrypted with a previous "
                    "SECRET_KEY (API key, FreshRSS token) will fail to decrypt "
                    "silently. Set a persistent ENCRYPTION_KEY or SECRET_KEY."
                )
        return self


settings = Settings()
