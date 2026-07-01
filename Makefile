.PHONY: help install test evals\:binder lint clean
.DEFAULT_GOAL := help

help:  ## Show this help
	@awk '/^[a-zA-Z_:\\-]+:.*## / {t=$$0; sub(/:[ \t]*##.*/,"",t); gsub(/\\/,"",t); d=$$0; sub(/^.*## /,"",d); printf "  \033[36m%-11s\033[0m %s\n", t, d}' $(MAKEFILE_LIST)

install:  ## Create the venv and install dev dependencies
	uv sync

test:  ## Run the unit test suite
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/evalspec

# Keep modest: high fan-out trips the host CLI's ~60s timeout (12-way -> SIGKILL).
BINDER_WORKERS ?= 6
evals\:binder:  ## Run the live binder corpus eval on Haiku (gate: false-positive -> 0; costs money, needs a Claude credential)
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) tests/evalspec/evals/test_binder_corpus.py

lint:  ## Lint with ruff
	uv run ruff check .

clean:  ## Remove the venv and Python caches
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
