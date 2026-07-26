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

RUN mkdir -p data

ARG GIT_COMMIT=unknown
ENV APP_VERSION=$GIT_COMMIT

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
