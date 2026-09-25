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

# Confiance aux en-têtes X-Forwarded-* : uvicorn s'en sert pour
# `request.client.host`, donc pour la limitation de débit, et pour le schéma
# d'URL. Il parcourt X-Forwarded-For depuis la droite et retient la première
# adresse qui n'est PAS dans cette liste : celle que le reverse proxy a ajoutée.
#
# Jamais `*` : uvicorn prend alors le PREMIER élément de la liste, c'est-à-dire
# celui qu'écrit le client. Changer cette valeur à chaque requête suffisait à
# repartir d'un compteur neuf et annulait toutes les limites de débit.
#
# Par défaut : boucle locale et réseaux privés, où se trouvent le proxy et la
# passerelle Docker. Restreindre à l'adresse exacte du proxy quand elle est
# connue (voir FORWARDED_ALLOW_IPS dans .env.example).
ENV FORWARDED_ALLOW_IPS=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7

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
