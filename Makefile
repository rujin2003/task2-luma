.PHONY: test lint typecheck check-float ci seed seed-trivial migrate migration install demo

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

# Reset → seed → shock pack → deterministic narrative (Person 1 Phase 11).
demo:
	DATABASE_URL="sqlite:///warroom-demo.db" SEED=$(SEED) \
		$(PYTHON) scripts/demo.py --seed $(SEED) --db sqlite:///warroom-demo.db \
		--golden tests/fixtures/demo/golden_seed_$(SEED).json
