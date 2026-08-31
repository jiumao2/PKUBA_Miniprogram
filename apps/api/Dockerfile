FROM python:3.13-slim AS runtime-dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.lock requirements-dev.lock ./

RUN pip install --no-cache-dir -r requirements.lock

EXPOSE 8000

FROM runtime-dependencies AS development
COPY config ./config
COPY core ./core
COPY manage.py ./
RUN pip install --no-cache-dir -r requirements-dev.lock \
    && pip install --no-cache-dir --no-deps -e .
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM runtime-dependencies AS production
# Production deliberately receives only runtime packages. Tests, local fixtures,
# caches and development-only dependency layers never enter the final image.
COPY config ./config
COPY core/*.py ./core/
COPY core/assets ./core/assets
COPY core/management/__init__.py ./core/management/__init__.py
COPY core/management/commands/__init__.py \
    core/management/commands/audit_season_integrity.py \
    core/management/commands/bootstrap_first_superadmin.py \
    core/management/commands/check_no_synthetic_public_data.py \
    core/management/commands/deployment_preflight.py \
    core/management/commands/expire_reschedules.py \
    core/management/commands/export_openapi.py \
    core/management/commands/process_archives.py \
    core/management/commands/process_outbox.py \
    core/management/commands/process_scoresheets.py \
    core/management/commands/reconcile_game_media_uploads.py \
    core/management/commands/reconcile_inbox_tasks.py \
    core/management/commands/reschedule_route_activation_preflight.py \
    core/management/commands/restore_system_backup.py \
    ./core/management/commands/
COPY core/migrations ./core/migrations
COPY core/scoresheet_v2 ./core/scoresheet_v2
COPY core/services ./core/services
COPY manage.py ./
RUN pip install --no-cache-dir --no-deps -e . \
    && test ! -e /app/core/tests \
    && test ! -e /app/core/management/commands/sample_2026_schedule_v3.py \
    && ! find /app/core/management/commands -maxdepth 1 -type f \
      \( -name '*sample*' -o -name '*demo*' -o -name '*seed*' -o -name '*fixture*' \) \
      -print -quit | grep -q . \
    && python manage.py help export_openapi >/dev/null \
    && python manage.py help bootstrap_first_superadmin >/dev/null \
    && python manage.py help process_scoresheets >/dev/null \
    && python manage.py help process_archives >/dev/null \
    && python manage.py help restore_system_backup >/dev/null
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "600"]
