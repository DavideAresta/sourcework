# The hosted distribution, as an image. Same base as the mesh image, plus the
# sourcework-cloud package: it is what registers the `sourcework.auth` entry
# point that puts the sign-in guard on the app, and it brings the Postgres
# driver.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# lxml (via python-docx/bs4) needs a compiler on slim images
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY cloud/pyproject.toml cloud/
COPY cloud/src ./cloud/src
RUN pip install --no-cache-dir ".[ingest]" && pip install --no-cache-dir "./cloud"

RUN useradd --create-home --uid 10001 sourcework && mkdir -p /workspace && chown sourcework /workspace
USER sourcework

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8080}/healthz" || exit 1

CMD ["python", "-m", "sourcework_cloud", "serve", "--host", "0.0.0.0"]
