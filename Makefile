.PHONY: install dev test lint fmt serve-all ui status demo up down logs clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[ingest,dev]"
	@echo "Ready. Copy .env.example to .env and fill it in."

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check src tests

fmt:
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

serve-all:
	$(VENV)/bin/prdforge-agent serve-all

ui:
	$(VENV)/bin/prdforge-agent ui

status:
	$(VENV)/bin/prdforge-agent status

demo:
	PRDFORGE_LLM__STUB=1 $(PY) scripts/demo.py

up:
	docker compose up -d --build
	@sleep 5 && docker compose ps

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__ *.egg-info
