"""Purge des espaces de démonstration expirés.

À lancer périodiquement sur l'instance de démo, par exemple toutes les heures :

    docker compose exec -T excerpta python -m app.demo_purge

Sans cette purge, chaque visiteur laisse derrière lui un utilisateur, ses
dossiers, ses étiquettes et ses liens : la base grossit indéfiniment.
"""

import logging
import sys

from sqlmodel import Session

from .config import settings
from .database import engine
from .demo import purge_expired_demo_users

logger = logging.getLogger("excerpta.demo_purge")


def main() -> int:
    if not settings.demo_mode:
        print("DEMO_MODE désactivé : rien à purger.", file=sys.stderr)
        return 1
    with Session(engine) as session:
        supprimes = purge_expired_demo_users(session)
    print(f"{supprimes} espace(s) de démonstration purgé(s) "
          f"(durée de vie : {settings.demo_ttl_hours} h).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
