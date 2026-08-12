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
RUN pip install --no-cache-dir ".[ingest]"

# The CLI backends (claude-code, opencode-cli, copilot-cli, codex-cli, agy-cli)
# are deliberately NOT
# installed here. They authenticate as a *person* - a stored login, a
# subscription - which is a developer-machine idea, not a container one. The
# image runs the litellm backend; to use a CLI backend in a container you also
# have to get its credentials in, and that decision belongs to whoever is
# deploying, not to this file.
RUN useradd --create-home --uid 10001 sourcework && mkdir -p /workspace && chown sourcework /workspace
USER sourcework

EXPOSE 8000-8007

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8000}/healthz" || exit 1

CMD ["sourcework", "serve", "orchestrator"]
