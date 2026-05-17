.PHONY: up down seed seed2 ingest normalize agent test eval bench lint fmt bootstrap

# Superuser credentials — used only for schema setup and seeding
DB_BOOTSTRAP=DATABASE_URL=postgresql://d2c:d2c@localhost:5434/d2c
# App-role credentials — NOSUPERUSER NOBYPASSRLS; used for runtime SQLAlchemy paths
DB=DATABASE_URL=postgresql://d2c_app:d2c_app@localhost:5434/d2c
# Analytics URL for DuckDB sandbox — superuser for RLS bypass; isolation via WHERE clause
DB_ANALYTICS=DATABASE_URL_ANALYTICS=postgresql://d2c:d2c@localhost:5434/d2c

bootstrap: ## Full first-run: start db, seed, start api+ui
	docker compose up -d db
	until docker compose exec -T db pg_isready -U d2c -q; do sleep 1; done
	pip install -e ".[dev]" -q 2>/dev/null || pip install --break-system-packages -e ".[dev]" -q
	$(DB_BOOTSTRAP) python3 scripts/seed_demo_merchant.py
	$(DB_BOOTSTRAP) python3 scripts/seed_second_merchant.py
	$(DB_BOOTSTRAP) python3 scripts/normalize.py --merchant demo
	$(DB_BOOTSTRAP) python3 scripts/normalize.py --merchant demo2
	docker compose up -d api ui
	@echo "Done — UI at http://localhost:10002"

up:
	docker compose up -d db api ui

down:
	docker compose down

seed:
	pip install -e . -q 2>/dev/null || pip install --break-system-packages -e . -q
	$(DB_BOOTSTRAP) python3 scripts/seed_demo_merchant.py
	$(DB_BOOTSTRAP) python3 scripts/seed_second_merchant.py
	$(DB_BOOTSTRAP) python3 scripts/normalize.py --merchant demo
	$(DB_BOOTSTRAP) python3 scripts/normalize.py --merchant demo2

ingest:
	$(DB) python3 -m app.ingest.runner --all

normalize:
	$(DB_BOOTSTRAP) python3 scripts/normalize.py --merchant demo

agent:
	$(DB) $(DB_ANALYTICS) python3 scripts/run_agent.py --merchant demo

test:
	$(DB) $(DB_ANALYTICS) pytest -x -q

eval:
	$(DB_BOOTSTRAP) pytest tests/eval/ -v -s

bench:
	$(DB) python3 scripts/bench_ingest.py --merchants 10 --orders-per-merchant 20

lint:
	ruff check .

fmt:
	ruff format .
	ruff check --fix .
