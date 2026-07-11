.PHONY: help install test evals evals\:binder lint lint-ruff lint-houserules clean
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

LINT_BASE ?= origin/dev
lint:  ## Lint with Ruff and houserules
	$(MAKE) lint-ruff
	$(MAKE) lint-houserules

lint-ruff:  ## Lint with Ruff
	uv run ruff check .

lint-houserules:  ## Lint changed lines with houserules (needs GEMINI_API_KEY)
	@untracked="$$(git ls-files --others --exclude-standard -- '*.py')"; \
	if [ -n "$$untracked" ]; then git add -N -- $$untracked; fi; \
	uv run houserules --base "$$(git merge-base $(LINT_BASE) HEAD)" --verbose .; \
	rc=$$?; \
	if [ -n "$$untracked" ]; then git reset -q -- $$untracked; fi; \
	exit $$rc

clean:  ## Remove the venv and Python caches
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
