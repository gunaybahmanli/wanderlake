.DEFAULT_GOAL := help
.PHONY: help setup lint format test clean up down ps logs psql s3-ls backfill test-dags \
	tofu-init tofu-fmt tofu-validate tofu-plan tofu-apply

PROFILE ?= core
SERVICE ?=
BUCKET ?= raw
DAG ?=
START ?=
END ?=

# OpenTofu: load the root .env (credentials + TF_VAR_*) into the environment, then run in infra/tofu.
TOFU_DIR := infra/tofu
TOFU_PLAN := wanderlake.tfplan
TOFU = test -f .env || { echo "Missing .env: run 'cp .env.example .env' and fill it in."; exit 1; }; \
	set -a && . ./.env && set +a && cd $(TOFU_DIR) && tofu
COMPOSE := docker compose --env-file .env -f infra/compose/compose.yaml --profile $(PROFILE)

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install dev dependencies and git hooks
	@uv sync
	@uv run pre-commit install

lint: ## Run all pre-commit hooks on every file
	@uv run pre-commit run --all-files

format: ## Format and auto-fix Python code with ruff
	@uv run ruff format .
	@uv run ruff check --fix .

test: ## Run the test suite (DAG integrity tests are skipped without Airflow)
	@uv run pytest

test-dags: ## Run DAG integrity tests inside the running Airflow container
	@$(COMPOSE) exec airflow bash -c 'set -e; \
		export PYTHONPATH="/tmp/test-deps:$$PYTHONPATH" DAGS_FOLDER=/opt/airflow/dags; \
		python -c "import pytest" 2>/dev/null || pip install --quiet --disable-pip-version-check --target /tmp/test-deps pytest; \
		python -m pytest -p no:cacheprovider --rootdir=/tmp -v /opt/wanderlake/tests/dags'

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

s3-ls: ## List objects in a bucket (default: make s3-ls BUCKET=raw)
	@$(COMPOSE) run --rm --no-deps --entrypoint aws s3-init s3 ls "s3://$(BUCKET)" --recursive --human-readable

backfill: ## Backfill a DAG: make backfill DAG=frankfurter_rates START=2026-11-11 END=2026-11-17
	@test -n "$(DAG)" -a -n "$(START)" -a -n "$(END)" || { echo "Usage: make backfill DAG=<dag_id> START=YYYY-MM-DD END=YYYY-MM-DD"; exit 1; }
	@$(COMPOSE) exec airflow airflow backfill create --dag-id "$(DAG)" --from-date "$(START)" --to-date "$(END)"

tofu-init: ## OpenTofu: download pinned providers (updates .terraform.lock.hcl)
	@$(TOFU) init -input=false

tofu-fmt: ## OpenTofu: format all .tf files
	@$(TOFU) fmt -recursive

tofu-validate: ## OpenTofu: validate the configuration
	@$(TOFU) validate

tofu-plan: ## OpenTofu: show changes and save an encrypted plan for tofu-apply
	@$(TOFU) plan -input=false -out=$(TOFU_PLAN)

tofu-apply: ## OpenTofu: apply exactly the plan saved by tofu-plan
	@test -f $(TOFU_DIR)/$(TOFU_PLAN) || { echo "No saved plan: run 'make tofu-plan' first and review it."; exit 1; }
	@$(TOFU) apply -input=false $(TOFU_PLAN) && rm -f $(TOFU_PLAN)
