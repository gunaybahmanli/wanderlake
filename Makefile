.DEFAULT_GOAL := help
.PHONY: help setup lint format test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

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
