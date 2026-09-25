.PHONY: help install test e2e evals assets lint lint\:ruff lint\:ty lint\:houserules clean
.DEFAULT_GOAL := help

help:  ## Show this help
	@awk '/^[a-zA-Z0-9_:\\-]+:.*## / {t=$$0; sub(/:[ \t]*##.*/,"",t); gsub(/\\/,"",t); d=$$0; sub(/^.*## /,"",d); printf "  \033[36m%-11s\033[0m %s\n", t, d}' $(MAKEFILE_LIST)

install:  ## Create the venv and install dev dependencies
	uv sync

test:  ## Run the unit test suite
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester

# Shared by e2e and evals. Keep it a fixed, modest number rather than `auto`:
# each e2e cell reserves a 2 GB sandbox, and binder fan-out past ~12 trips the
# Gemini call's ~60s timeout.
WORKERS ?= 6
# Three samples per cell, so every delta carries a noise band; COUNT=1 for a quick pass.
COUNT ?= 3
# A collect-only pass writes no iteration, so it has no accounting to check.
CHECK_ACCOUNTING = $(if $(findstring --collect-only,$(EVAL_ARGS)),,uv run scripts/check_sample_accounting.py)
# The second run puts the binder, the judge, and every harness on OpenRouter, so one
# command proves both the default path and the single-key path. The judge stays on
# Codex but grades on a Google slug: the arms are Anthropic and OpenAI models. After
# each run, the accounting check fails the target if any clean sample lacks its time or
# token split, which is where a harness CLI's output drifting first shows.
e2e:  ## Run the in-repo hello suite end to end, natively and through OpenRouter, COUNT samples per cell (default 3) on WORKERS sandboxes (default 6); EVAL_ARGS appends pytest args. Prerequisites: README, Development
	uv run benchspec run --set e2e -- --count $(COUNT) -n $(WORKERS) $(EVAL_ARGS)
	$(CHECK_ACCOUNTING)
	uv run benchspec run --set e2e-openrouter --judge-provider openrouter --judge-model google/gemini-3.5-flash --binder-provider openrouter -- --count $(COUNT) -n $(WORKERS) $(EVAL_ARGS)
	$(CHECK_ACCOUNTING)

evals:  ## Run the binder corpus (binder quality, not framework function) on WORKERS workers (default 6); EVAL_ARGS="--collect-only -q" dry-runs collection
	uv run pytest -m binder_corpus -n $(WORKERS) evals/binder $(EVAL_ARGS)

assets:  ## Re-render the raster brand assets in docs/assets (terminal mock PNG/GIF, social card)
	uv run scripts/render_assets.py

lint:  ## Lint with Ruff, type-check with ty, then houserules
	$(MAKE) lint:ruff
	$(MAKE) lint:ty
	$(MAKE) lint:houserules

lint\:ruff:  ## Lint with Ruff
	uv run ruff check .

lint\:ty:  ## Type-check with ty
	uv run ty check

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
