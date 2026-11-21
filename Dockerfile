FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker/entrypoint.sh ./docker/entrypoint.sh

RUN pip install --no-cache-dir .

EXPOSE 8000
ENTRYPOINT ["sh", "docker/entrypoint.sh"]
