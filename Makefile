.PHONY: up down seed seed2 ingest normalize agent test eval lint fmt

DB=DATABASE_URL=postgresql://d2c:d2c@localhost:5432/d2c

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
