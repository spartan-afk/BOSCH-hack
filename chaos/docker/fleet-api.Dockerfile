# Same runtime as the app's own Dockerfile, but installs the app's pinned
# requirements.txt. The app's pyproject.toml uses ">=" ranges, which now pull
# FastAPI 0.143 / Starlette 1.x; those removed the TemplateResponse(name, {...})
# form the dashboard uses, so "/" and "/map" returned 500. No app code changes.

FROM python:3.12-slim AS production

RUN groupadd -r fleetmgr && useradd -r -g fleetmgr -m -d /app fleetmgr
WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt gunicorn==23.0.0

COPY --chown=fleetmgr:fleetmgr config/ config/
COPY --chown=fleetmgr:fleetmgr models/ models/
COPY --chown=fleetmgr:fleetmgr api/ api/
COPY --chown=fleetmgr:fleetmgr telematics/ telematics/
COPY --chown=fleetmgr:fleetmgr services/ services/
COPY --chown=fleetmgr:fleetmgr templates/ templates/
COPY --chown=fleetmgr:fleetmgr main.py cli.py __init__.py gunicorn_config.py ./

RUN mkdir -p /app/data && chown -R fleetmgr:fleetmgr /app/data

ENV API_HOST=0.0.0.0 \
    API_PORT=8000 \
    API_DEBUG=false \
    DB_DB_URL=sqlite:///data/fleet.db \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
USER fleetmgr
CMD ["gunicorn", "main:app", "-c", "gunicorn_config.py"]
