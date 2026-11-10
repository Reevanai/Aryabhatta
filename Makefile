COMPOSE := docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/dev.env
DEMO_TENANT := 0192f000-0000-7000-8000-00000000d001
TOOLS := $(COMPOSE) run --rm tools sentinel-admin
PROFILE ?= demo
SYNTH_DIR ?= var/synth/$(PROFILE)

.PHONY: help install deps-up up down seed demo baseline test lint typecheck check licences clean

help:
	@echo "make up        start the full development stack (builds images, applies migrations)"
	@echo "make seed      create the demo tenant, generate synthetic data and load it (PROFILE=tiny|demo|small)"
	@echo "make baseline  measure naive rules against the labels of the generated data"
	@echo "make demo      show quarantine, tokenised events, audited reveal and audit verification"
	@echo "make test      run all tests with coverage (starts PostgreSQL, Redpanda and OpenBao)"
	@echo "make check     lint, type-check, import boundaries and licence allow-list"
	@echo "make down      stop the stack and delete its data"

install:
	uv sync --frozen

deps-up:
	$(COMPOSE) up -d --wait postgres redpanda openbao valkey
	$(COMPOSE) up openbao-init

up:
	$(COMPOSE) --profile full up -d --build --wait

down:
	$(COMPOSE) --profile full --profile tools down -v --remove-orphans

seed: install
	$(TOOLS) create-tenant --id $(DEMO_TENANT) --name "Demo Co-operative Bank (synthetic)" \
	  --issuer http://localhost:8080/realms/sentinel-demo \
	  --jwks-uri http://keycloak:8080/realms/sentinel-demo/protocol/openid-connect/certs
	uv run sentinel-synth generate --profile $(PROFILE) --out $(SYNTH_DIR)
	SENTINEL_SYNTH_DIR=$(SYNTH_DIR) uv run python scripts/demo.py seed

demo: install
	SENTINEL_SYNTH_DIR=$(SYNTH_DIR) uv run python scripts/demo.py demo
	$(TOOLS) audit-anchor --tenant $(DEMO_TENANT)

test: install deps-up
	uv run pytest --cov --cov-report=term --cov-report=xml

lint: install
	uv run ruff format --check .
	uv run ruff check .

typecheck: install
	uv run mypy

baseline: install
	uv run python scripts/synth_baseline.py $(SYNTH_DIR)

licences: install
	uv run python scripts/check_licences.py

check: lint typecheck licences
	uv run lint-imports

clean:
	rm -rf .venv .mypy_cache .ruff_cache .pytest_cache .coverage coverage.xml var
