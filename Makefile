.PHONY: up db-up db-down db-logs db-shell install migrate migration seed run test lint openapi

up:
	docker compose up -d --build

db-up:
	docker compose up -d db

db-down:
	docker compose stop db

db-logs:
	docker compose logs -f db

db-shell:
	docker compose exec db psql -U app -d streaming

install:
	pip install -r requirements-dev.txt

migrate:
	flask --app wsgi db upgrade

migration:
	flask --app wsgi db migrate -m "$(m)"

seed:
	flask --app wsgi seed

run:
	flask --app wsgi run --debug

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check . && ruff format --check .

openapi:
	flask --app wsgi openapi write docs/openapi.json
