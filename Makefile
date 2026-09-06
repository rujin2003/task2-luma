.PHONY: test lint typecheck check-float ci seed seed-trivial migrate migration install demo demo-companies onboard serve frontend gemini-smoke

PYTHON ?= python3
SEED ?= 42
DB ?= sqlite:///novatech.db

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check backend tests scripts
	$(PYTHON) -m ruff format --check backend tests scripts

fmt:
	$(PYTHON) -m ruff check --fix backend tests scripts
	$(PYTHON) -m ruff format backend tests scripts

typecheck:
	$(PYTHON) -m mypy

check-float:
	$(PYTHON) scripts/check_no_float.py

ci: lint typecheck check-float test

seed:
	DATABASE_URL="$(DB)" SEED=$(SEED) $(PYTHON) -m backend.seed

# Applies migrations rather than create_all, so the seed exercises the same
# schema a deploy would get.
migrate:
	DATABASE_URL="$(DB)" $(PYTHON) -m alembic upgrade head

migration:
	@test -n "$(M)" || (echo "usage: make migration M='describe the change'"; exit 1)
	DATABASE_URL="$(DB)" $(PYTHON) -m alembic revision --autogenerate -m "$(M)"

seed-trivial: migrate
	DATABASE_URL="$(DB)" $(PYTHON) -m backend.seed.trivial

# Fixture-backed Monday: cycle → breach → war room → stress fail → replan → recommendation.
demo:
	WARROOM_LLM=replay $(PYTHON) -m scripts.demo

serve:
	env -u WARROOM_LLM $(PYTHON) -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

gemini-smoke:
	$(PYTHON) -m scripts.gemini_smoke

# The four demo tenants' *source* databases -- four different schemas for the DB Agent
# to read. Written to var/demo/ with a manifest the Data Source screen lists.
demo-companies:
	$(PYTHON) -m scripts.demo_company

# Walk the DB Agent end to end on one tenant, on the command line:
#   make onboard TENANT=northgate
TENANT ?= helios
onboard: demo-companies
	$(PYTHON) -m scripts.onboard --tenant $(TENANT)
