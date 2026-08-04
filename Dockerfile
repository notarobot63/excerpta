FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY app/static/ ./static/

# Catalogues i18n : les .po sont versionnés, les .mo sont compilés ici. Un
# catalogue mal formé fait échouer le build plutôt que de partir en production
# avec des traductions muettes. Voir docs/i18n.md.
RUN pybabel compile -d app/translations

# Compte de service dédié : rien ici n'a besoin de root, et une exécution en root
# transforme une évasion de conteneur en compromission de l'hôte. UID fixe (et
# non attribué au hasard) pour que le propriétaire du volume de données reste le
# même d'une image à la suivante.
#
# ATTENTION à la mise à jour d'une instance existante : son volume appartient à
# root, l'application ne pourra plus y écrire. Une fois, conteneur arrêté :
#   docker run --rm -v excerpta_data:/data alpine chown -R 10001:0 /data
RUN useradd --system --uid 10001 --gid 0 --no-create-home excerpta \
    && mkdir -p data \
    && chown -R 10001:0 /app \
    && chmod -R g=u /app

ARG GIT_COMMIT=unknown
ENV APP_VERSION=$GIT_COMMIT

# Confiance aux en-têtes X-Forwarded-* : uvicorn ne s'en sert que pour
# `request.client.host` et le schéma d'URL. `*` accepte n'importe quelle source,
# ce qui n'est correct que si le conteneur est joignable uniquement par le
# reverse proxy. Dès que le port est exposé sur un réseau partagé, restreindre à
# l'adresse du proxy (voir FORWARDED_ALLOW_IPS dans .env.example).
ENV FORWARDED_ALLOW_IPS=*

USER 10001

EXPOSE 8000

# L'en-tête Host doit porter le hostname de BASE_URL : StrictHostMiddleware
# répond 400 à tout le reste, y compris à un appel sur 127.0.0.1 — un
# healthcheck naïf resterait rouge en permanence.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, sys, urllib.request as u; from urllib.parse import urlparse; \
host = urlparse(os.environ.get('BASE_URL', 'http://localhost')).hostname or 'localhost'; \
sys.exit(0 if u.urlopen(u.Request('http://127.0.0.1:8000/health', headers={'Host': host}), timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
