.DEFAULT_GOAL := help
.PHONY: help setup lint format test clean up down ps logs psql

PROFILE ?= core
SERVICE ?=
COMPOSE := docker compose --env-file .env -f infra/compose/compose.yaml --profile $(PROFILE)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

setup: ## Install dev dependencies and git hooks
	@uv sync
	@uv run pre-commit install

lint: ## Run all pre-commit hooks on every file
	@uv run pre-commit run --all-files

format: ## Format and auto-fix Python code with ruff
	@uv run ruff format .
	@uv run ruff check --fix .

test: ## Run the test suite
	@uv run pytest

clean: ## Remove tool caches
	@rm -rf .ruff_cache .pytest_cache
	@find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +

up: ## Start the stack for PROFILE (default: core), e.g. make up PROFILE=core
	@test -f .env || { echo "Missing .env: run 'cp .env.example .env' and fill it in."; exit 1; }
	@$(COMPOSE) up -d

down: ## Stop the stack for PROFILE (data volumes are kept)
	@$(COMPOSE) down

ps: ## Show service status and health
	@$(COMPOSE) ps -a

logs: ## Follow logs (all services, or one: make logs SERVICE=airflow)
	@$(COMPOSE) logs -f --tail=200 $(SERVICE)

psql: ## Open psql in the wanderlake database
	@$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
