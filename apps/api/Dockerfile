FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.lock requirements-dev.lock ./
COPY config ./config
COPY core ./core
COPY manage.py ./

RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

FROM base AS development
RUN pip install --no-cache-dir -r requirements-dev.lock
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM base AS production
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "600"]
