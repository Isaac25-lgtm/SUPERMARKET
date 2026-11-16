# syntax=docker/dockerfile:1.7
# Single Render web service: FastAPI + in-process Tryton 8.0 engine + built React UI.

# ---- 1. build the web UI --------------------------------------------------------
FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# Pin npm: the lockfile is generated with this version (newer npm versions are stricter about optional deps).
RUN npm install -g npm@11.19.1 --no-audit --no-fund && npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. python runtime ----------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC \
    TRYTOND_TZ=UTC \
    MOTION_ENV=production \
    MOTION_WEB_DIST=/app/web/dist \
    MOTION_FILESTORE=/tmp/motion-filestore \
    PYTHONPATH=/app/api
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 motion
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY tryton-modules/ ./tryton-modules/
RUN pip install ./tryton-modules/motion_branch
COPY api/motion_api/ ./api/motion_api/
COPY --from=web /web/dist ./web/dist
USER motion
EXPOSE 10000
# Port binds immediately; database bootstrap (migrations, Tryton module activation,
# base setup, optional TEST DATA) runs in a background thread when MOTION_AUTO_BOOTSTRAP=1.
# One worker: Tryton keeps per-process caches and the gateway learns lock needs per process.
CMD ["sh", "-c", "exec uvicorn motion_api.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers"]
