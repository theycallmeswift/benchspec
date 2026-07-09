.PHONY: help install test evals evals\:binder lint lint\:custom clean
.DEFAULT_GOAL := help

help:  ## Show this help
	@awk '/^[a-zA-Z_:\\-]+:.*## / {t=$$0; sub(/:[ \t]*##.*/,"",t); gsub(/\\/,"",t); d=$$0; sub(/^.*## /,"",d); printf "  \033[36m%-11s\033[0m %s\n", t, d}' $(MAKEFILE_LIST)

install:  ## Create the venv and install dev dependencies
	uv sync

test:  ## Run the unit test suite
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester

# Keep modest: high fan-out trips the Gemini call's ~60s timeout (12-way -> throttling).
BINDER_WORKERS ?= 6
evals:  ## Run the live eval suite. Pass EVAL_ARGS="--collect-only -q" to dry-run collection.
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) evals/binder $(EVAL_ARGS)

evals\:binder:  ## Alias for `make evals` while the binder corpus is the only live eval suite
	$(MAKE) evals BINDER_WORKERS=$(BINDER_WORKERS) EVAL_ARGS="$(EVAL_ARGS)"

lint:  ## Lint with ruff and verified style checks
	uv run ruff check .
	uv run python bin/linters/style_lint.py --verify-findings --verbose .

lint\:custom:  ## Run custom advisory style checks
	uv run python bin/linters/style_lint.py

clean:  ## Remove the venv and Python caches
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
