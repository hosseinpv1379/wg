FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/wireguard-service
COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY deploy/node/install.sh ./deploy/node/install.sh
COPY alembic.ini ./
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
