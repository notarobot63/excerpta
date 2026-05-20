FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY app/static/ ./static/

RUN mkdir -p data

ARG GIT_COMMIT=unknown
ENV APP_VERSION=$GIT_COMMIT

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
