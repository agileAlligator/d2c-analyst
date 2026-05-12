.PHONY: up down seed seed2 ingest normalize agent test eval lint fmt bootstrap

DB=DATABASE_URL=postgresql://d2c:d2c@localhost:5434/d2c

bootstrap: ## Full first-run: start db, seed, start api+ui
	docker compose up -d db
	sleep 3
	pip install -e . -q
	$(DB) python scripts/seed_demo_merchant.py
	$(DB) python scripts/seed_second_merchant.py
	$(DB) python scripts/normalize.py --merchant demo
	$(DB) python scripts/normalize.py --merchant demo2
	docker compose up -d api ui
	@echo "Done — UI at http://localhost:10002"

up:
	docker compose up -d db api ui

down:
	docker compose down

seed:
	$(DB) python scripts/seed_demo_merchant.py
	$(DB) python scripts/seed_second_merchant.py
	$(DB) python scripts/normalize.py --merchant demo
	$(DB) python scripts/normalize.py --merchant demo2

ingest:
	$(DB) python -m app.ingest.runner --all

normalize:
	$(DB) python scripts/normalize.py --merchant demo

agent:
	$(DB) python scripts/run_agent.py --merchant demo

test:
	pytest -x -q

eval:
	pytest tests/eval/ -v -s

lint:
	ruff check .

fmt:
	ruff format .
	ruff check --fix .
