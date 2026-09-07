.PHONY: help install test e2e evals assets lint lint\:ruff lint\:houserules clean
.DEFAULT_GOAL := help

help:  ## Show this help
	@awk '/^[a-zA-Z0-9_:\\-]+:.*## / {t=$$0; sub(/:[ \t]*##.*/,"",t); gsub(/\\/,"",t); d=$$0; sub(/^.*## /,"",d); printf "  \033[36m%-11s\033[0m %s\n", t, d}' $(MAKEFILE_LIST)

install:  ## Create the venv and install dev dependencies
	uv sync

test:  ## Run the unit test suite
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester

e2e:  ## Run benchspec's own end-to-end suite (real microVMs; needs claude+codex CLIs and provider credentials)
	uv run benchspec run --set e2e

# Keep modest: high fan-out trips the Gemini call's ~60s timeout (12-way -> throttling).
BINDER_WORKERS ?= 6
evals:  ## Run the binder corpus (binder quality, not framework function). Pass EVAL_ARGS="--collect-only -q" to dry-run collection.
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) evals/binder $(EVAL_ARGS)

assets:  ## Re-render the raster brand assets in docs/assets (terminal mock PNG/GIF, social card)
	uv run scripts/render_assets.py

lint:  ## Lint with Ruff and houserules
	$(MAKE) lint:ruff
	$(MAKE) lint:houserules

lint\:ruff:  ## Lint with Ruff
	uv run ruff check .

LINT_BASE ?= origin/main
lint\:houserules:  ## Lint changed and new Python files with houserules (needs GEMINI_API_KEY)
	uv run houserules --base "$$(git merge-base $(LINT_BASE) HEAD)" --verbose .
	@untracked_python_files="$$(git ls-files --others --exclude-standard -- '*.py')"; \
	if [ -n "$$untracked_python_files" ]; then \
		uv run houserules --verbose $$untracked_python_files; \
	fi

clean:  ## Remove the venv and Python caches
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
