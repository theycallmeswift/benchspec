# Markdown Eval Format and Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace skill-root eval discovery with a recursive `**/evals/` crawl for `eval.md` / `*.eval.md`, rename the frontmatter `seed:[{role,text}]` block to `history:[{role,content}]`, move per-eval `fixtures/` → `workspace/` and suite-level `setup.sh` → per-eval `setup.sh`, and key each case on a folder-derived `(group, eval_id)` pair no longer tied to a skill directory.

**Architecture:** `discovery.discover_eval_cases` walks the whole tree (pruning `tmp/`, `.git`, `__pycache__`, and dot-dirs), collects `eval.md`/`*.eval.md` under any `evals/<group>/` folder, derives `(group, eval_id)` from the folder and filename, and fails loudly on a duplicate pair. `mdformat.parse_eval_md` derives the id from the filename and reads a `history` frontmatter block; `schema` validates it; `room.render_history` flattens turns into the prompt prefix and `room.seed_room` copies `workspace/` into the sandbox; `sandbox.run_setup_sh` runs a per-eval `setup.sh` located by the eval dir's relative path. `EvalCase.skill` remains an alias for `group` so the two-axis artifact path, grading records, and report keys need no shape change this phase.

**Tech Stack:** Python 3.11+, pytest (unit suite runs via `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester`), `ruff` + `bin/linters/style_lint.py` for lint, YAML frontmatter, microsandbox for live arms.

## Global Constraints

- **No compatibility shims.** The old `prompt.md` dir shape, the `seed:` key, the `text` turn field, and the `fixtures/` dir name are removed outright; in-repo consumers move to the new surface. There is no dual-read.
- **Trigger discovery is left intact.** `discover_trigger_cases`, `_skill_dirs`, `resolve_eval_roots`, and `parse_trigger` keep working against `skills/<skill>/evals/trigger-evals.md`. Phase 4 retires them; Phase 3 must leave trigger routing working.
- **Kebab-case ids.** Both `group` (folder name) and `eval_id` (folder name for `eval.md`, file stem for `*.eval.md`) must match `^[a-z0-9]+(-[a-z0-9]+)*$`; a non-kebab segment fails loudly at collection.
- **Deny-list pruning is fixed, not configurable.** The crawl prunes `tmp/` (the artifact root), `.git`, `__pycache__`, and every dot-prefixed dir. It is a hardcoded set, never surfaced as config.
- **`eval_roots` stops governing eval discovery.** `[tool.evalspec] eval_roots` and `--evalspec-eval-roots` are read only by trigger discovery until Phase 4.
- **Case identity is a `(group, eval_id)` pair preserving the two-axis artifact path.** `group` takes the old `<skill>` slot; `workspace.arm_dir`, `execution` grading records, `report` keys, and `execution.detect_skill` need no shape change — `EvalCase.skill` aliases `group`.
- **`history` flattens into the prompt prefix.** `render_history` renders turns into a `<transcript>` block prepended to the graded prompt exactly as `seed` did — no real multi-turn replay is introduced.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/evalspec/schema.py` | Modify | Rename `_validate_seed`→`_validate_history` (`text`→`content`); add `is_kebab`; rename `slug`→`id` and `seed`→`history` in `_validate_evals_v1`. |
| `src/evalspec/mdformat.py` | Modify | `_EVAL_FM = {"history"}`; `parse_eval_md` derives `id` from filename (`eval.md`→parent name, `*.eval.md`→stem) and reads `history`; remove `load_suite_dir` and `NON_EVAL_MD`. |
| `src/evalspec/room.py` | Modify | `render_seed`→`render_history` (`content` key); rename `seed_room` param `fixture_dir`→`workspace_dir`. |
| `src/evalspec/discovery.py` | Modify | New `_evals_dirs` crawl + `_eval_files`; rewrite `EvalCase` to `(group, eval_dir, eval_file, eval)`; rewrite `discover_eval_cases` with `(group, eval_id)` dup guard; leave `_skill_dirs`/`discover_trigger_cases` intact. |
| `src/evalspec/sandbox.py` | Modify | `run_setup_sh` runs `PROJECT_MOUNT/<reldir>/setup.sh`; `SandboxSession`/`arm_session` take `setup_reldir` instead of `skill`. |
| `src/evalspec/execution.py` | Modify | Use `render_history(eval_case.history, …)`; compute `setup_reldir` and thread it through `_run_arm_turns`. |
| `src/evalspec/lint.py` | Modify | Read `case.eval_file` instead of `case.skill_dir / "evals" / case.slug / "prompt.md"`; drop `eval_roots` from eval discovery. |
| `src/evalspec/cases.py` | Modify | `seed_room(eval_case.workspace_dir, …)`. |
| `src/evalspec/plugin.py` | Modify | Call `discover_eval_cases(repo_root)` (no `eval_roots`); keep `resolve_eval_roots` for triggers. |
| `src/evalspec/__init__.py` | Modify | Docstring: `evals/<slug>/prompt.md` → the crawl-based layout. |
| `docs/schema.md` | Modify | Document `eval.md`/`*.eval.md` discovery, `history`, `workspace/`, per-eval `setup.sh`, `(group, eval_id)` identity + duplicate rule. |
| `README.md` | Modify | Update the eval-authoring example to the new layout and `history`. |
| `docs/quickstart.md` | Modify | Update the eval-authoring example to the new layout. |
| `tests/test_schema.py` | Modify | `slug`→`id`, `seed`→`history`/`content`. |
| `tests/test_mdformat.py` | Modify | `eval.md`/`*.eval.md` id derivation, `history`; drop `load_suite_dir`/`NON_EVAL_MD` tests. |
| `tests/test_room_history.py` | Rename from `test_room_seed.py` | `render_history` with `content`. |
| `tests/test_room.py` | Modify | `seed_room` positional call unchanged; assert workspace copy. |
| `tests/test_discovery.py` | Modify | Crawl, filename shapes, `(group, eval_id)` dup, pruning. |
| `tests/test_execution.py` | Modify | `EvalCase` new shape; `history`; `setup_reldir`. |
| `tests/test_sandbox.py` | Modify | `run_setup_sh(setup_reldir=…)`; `arm_session(setup_reldir=…)`. |
| `tests/test_lint.py` | Modify | Write `eval.md`; assert `Finding.file == eval_file`. |
| `tests/test_plugin.py` | Modify | Projects write `evals/<group>/eval.md`. |
| `tests/test_readme_examples.py` | Modify | Write the README block as `eval.md`. |

---

## Task 1: Rename `seed` → `history` (frontmatter, schema, render)

Rename the transcript-context block from `seed:[{role, text}]` to `history:[{role, content}]` end to end, keeping `prompt.md`/`slug`/`fixtures` discovery untouched so the tree stays green. The `history` block still flattens into the prompt prefix.

**Files:**
- Modify `src/evalspec/schema.py` (`_validate_seed` at `schema.py:156-168`; `_validate_evals_v1` `allowed_eval` at `schema.py:185`, seed call at `schema.py:199-200`)
- Modify `src/evalspec/mdformat.py` (`_EVAL_FM` at `mdformat.py:39`; `parse_eval_md` seed block at `mdformat.py:190-195`)
- Modify `src/evalspec/room.py` (`render_seed` at `room.py:66-81`)
- Modify `src/evalspec/execution.py` (import at `execution.py:26`; call at `execution.py:315`)
- Modify `README.md` (the tested `---\nseed:` example block)
- Test: `tests/test_schema.py`, `tests/test_mdformat.py`, `tests/test_room_seed.py`→`tests/test_room_history.py`, `tests/test_execution.py`, `tests/test_discovery.py` (the one `seed:` fixture — keeps the old `prompt.md`/`fixtures/` shape, still valid until Task 2)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `schema._validate_history(history: object, path: str) -> None` — validates a list of `{role, content}` turns.
  - `room.render_history(history: list[dict] | None, today: str | None = None) -> str` — `<transcript>` prefix from `content` turns.
  - `mdformat.parse_eval_md` result carries `history` (not `seed`); `EvalCase.history` still returns `self.eval.get("history", [])` (renamed in Task 2 — in this task `EvalCase.seed` is renamed to `EvalCase.history` reading `self.eval.get("history", [])`).
  - Allowed eval frontmatter key: `history` only.

- [ ] **Step 1: Write failing schema test for `history`/`content`.**
  In `tests/test_schema.py` replace `test_seed_ok`, `test_seed_missing_text_rejected`, `test_seed_extra_key_rejected` with:
  ```python
  def test_history_ok() -> None:
      """Verify history ok."""
      _validate(
          _doc(
              [
                  {
                      "slug": "seeded",
                      "prompt": "continue",
                      "history": [
                          {"role": "user", "content": "hi"},
                          {"role": "assistant", "content": "ok"},
                      ],
                      "assertions": ["the reply continued"],
                  }
              ]
          )
      )


  def test_history_missing_content_rejected() -> None:
      """Verify history missing content rejected."""
      with pytest.raises(SchemaError, match="content"):
          _validate(
              _doc([{"slug": "a", "prompt": "p", "assertions": ["x"], "history": [{"role": "user"}]}])
          )


  def test_history_extra_key_rejected() -> None:
      """Verify history extra key rejected."""
      with pytest.raises(SchemaError, match="unknown field"):
          _validate(
              _doc(
                  [
                      {
                          "slug": "a",
                          "prompt": "p",
                          "assertions": ["x"],
                          "history": [{"role": "user", "content": "hi", "name": "alice"}],
                      }
                  ]
              )
          )
  ```
- [ ] **Step 2: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_schema.py -k history -q`
  Expect: `test_history_ok` fails with `SchemaError: root.evals[0]: unknown field(s) ['history']`.
- [ ] **Step 3: Rename `_validate_seed` → `_validate_history` in `schema.py`.**
  Replace `schema.py:156-168` with:
  ```python
  def _validate_history(history: object, path: str) -> None:
      """Validate history conversation turns."""
      if not isinstance(history, list):
          raise SchemaError(f"{path}: expected list, got {type(history).__name__}")
      for index, turn in enumerate(history):
          turn_path = f"{path}[{index}]"
          if not isinstance(turn, dict):
              raise SchemaError(f"{turn_path}: expected object, got {type(turn).__name__}")
          _reject_extra_keys(turn, {"role", "content"}, turn_path)
          for key in ("role", "content"):
              value = _require(turn, key, str, turn_path)
              if not value.strip():
                  raise SchemaError(f"{turn_path}.{key}: must be non-empty")
  ```
  In `_validate_evals_v1` change `allowed_eval = {"slug", "seed", "prompt", "assertions"}` to `{"slug", "history", "prompt", "assertions"}`, and change the block at `schema.py:199-200` to:
  ```python
          if "history" in item:
              _validate_history(item["history"], f"{path}.history")
  ```
- [ ] **Step 4: Run to confirm pass.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_schema.py -q` → all pass.
- [ ] **Step 5: Write failing mdformat + room tests for `history`.**
  In `tests/test_mdformat.py` rename `test_parse_eval_md_with_seed` / `test_parse_eval_md_malformed_seed_rejected` bodies to use `history:` / `content:` and assert `ev["history"] == [{"role": "user", "content": "scope my plan"}, {"role": "assistant", "content": "which part?"}]` and `pytest.raises(schema.SchemaError, match="history")`. Also change `test_parse_eval_md_minimal`'s final assertion `assert "seed" not in ev` → `assert "history" not in ev`.
  Rename `tests/test_room_seed.py` → `tests/test_room_history.py` and rewrite:
  ```python
  """Tests for room history."""

  import pytest

  from evalspec.room import render_history


  def test_render_history_none_is_empty() -> None:
      """Verify render history none is empty."""
      assert render_history(None) == ""
      assert render_history([]) == ""


  def test_render_history_renders_block() -> None:
      """Verify render history renders block."""
      out = render_history(
          [
              {"role": "user", "content": "set up my vault"},
              {"role": "assistant", "content": "done"},
          ],
      )

      assert out == ("<transcript>\nuser: set up my vault\nassistant: done\n</transcript>\n\n")


  def test_render_history_substitutes_today() -> None:
      """Verify render history substitutes today."""
      out = render_history([{"role": "user", "content": "today is {TODAY}"}], today="2026-06-23")
      assert "today is 2026-06-23" in out
      assert "{TODAY}" not in out


  def test_render_history_missing_role_rejected() -> None:
      """Verify render history missing role rejected."""
      with pytest.raises(ValueError, match="role"):
          render_history([{"content": "no role here"}])


  def test_render_history_missing_content_rejected() -> None:
      """Verify render history missing content rejected."""
      with pytest.raises(ValueError, match="content"):
          render_history([{"role": "user"}])


  def test_render_history_non_string_rejected() -> None:
      """Verify render history non string rejected."""
      with pytest.raises(ValueError, match="content"):
          render_history([{"role": "user", "content": 7}])


  def test_render_history_non_dict_turn_rejected() -> None:
      """Verify render history non dict turn rejected."""
      with pytest.raises(ValueError, match="mapping"):
          render_history(["set up my vault"])


  def test_render_history_stray_placeholder_rejected() -> None:
      """Verify render history stray placeholder rejected."""
      with pytest.raises(ValueError, match="placeholder"):
          render_history([{"role": "user", "content": "write to {WORKDIR}/x"}], today="2026-06-23")
  ```
- [ ] **Step 6: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_room_history.py tests/test_mdformat.py -k "history" -q`
  Expect import/attribute errors (`cannot import name 'render_history'`) and `history` assertion failures.
- [ ] **Step 7: Rename in `mdformat.py`, `room.py`, `execution.py` and the `EvalCase.seed` property.**
  `mdformat.py:39`: `_EVAL_FM = {"history"}`. Replace `mdformat.py:190-195` with:
  ```python
      if "history" in fm:
          # Validate here so the single-file path is as strict as discovery: a bare
          # parse_eval_md call (linter, per-file tooling) must still reject a malformed
          # history block, not defer that to schema inside discovery.
          schema._validate_history(fm["history"], f"{path}: history")
          result["history"] = fm["history"]
  ```
  `room.py:66-81`: rename to `render_history`, iterate `("role", "content")`, use `turn["content"]`, and messages `history[{turn_index}]: turn must be a mapping with role/content` / `history[{turn_index}]: missing or non-string \`{key}\``.
  `execution.py:26`: `from evalspec.room import gather_facts, merge_facts, render_history`.
  `execution.py:315`: `prompt = render_history(eval_case.history, today) + substitute_prompt(eval_case.prompt, today)`.
  `discovery.py:61-63`: rename the `seed` property to `history` reading `self.eval.get("history", [])`. Update the `EvalCase.eval` field comment at `discovery.py:26` to `{slug, prompt, assertions, history?}`.
- [ ] **Step 8: Update `execution` + `README` tested block, run to confirm pass.**
  In `tests/test_execution.py` `test_seed_block_prepended_to_graded_prompt`: change `"seed": [...]` to `"history": [{"role": "user", "content": "scope my plan"}, {"role": "assistant", "content": "which part?"}]` (assertions on the `<transcript>` output are unchanged). In `tests/test_discovery.py` `test_discover_eval_cases_seed_and_fixtures`: change the frontmatter to `history:\n  - role: user\n    content: hi`, assert `case.history == [{"role": "user", "content": "hi"}]` (the `prompt.md`/`fixtures/` shape and `case.fixtures_dir` assertion stay — Task 2 rewrites this file). In `README.md`, change the tested eval block's frontmatter from `seed:` / `text:` to `history:` / `content:`.
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_mdformat.py tests/test_room_history.py tests/test_execution.py tests/test_readme_examples.py -q` → all pass.
- [ ] **Step 9: Commit.**
  `git add src/evalspec/schema.py src/evalspec/mdformat.py src/evalspec/room.py src/evalspec/execution.py src/evalspec/discovery.py README.md tests/test_schema.py tests/test_mdformat.py tests/test_room_history.py tests/test_execution.py tests/test_discovery.py && git rm tests/test_room_seed.py`
  `git commit -m "feat(evals): rename seed frontmatter to history[{role,content}]"`

---

## Task 2: Recursive `**/evals/` crawl, filename ids, `(group, eval_id)` identity, `workspace/`, and per-eval `setup.sh`

Replace the `eval_roots`-driven slug-dir discovery with a global crawl for `eval.md`/`*.eval.md`; derive `(group, eval_id)`; rename `fixtures/`→`workspace/`; move every in-repo consumer — including the `setup.sh` locator — to the new `EvalCase` in the same commit so the tree stays green.

This task is large by necessity: the no-compat-shim rule couples `EvalCase` identity to every consumer (discovery, workspace, setup, lint, plugin), so they must change atomically — a `setup.sh` locator or caller left on the old `skill`-keyed lookup after `EvalCase.skill` becomes an alias for `group` would search a nonexistent `skills/<group>/` dir. Mid-task steps are red outside their own `-k` slice (e.g. after the schema `slug`→`id` change but before the parse/discovery rewrite); the authoritative gate is the full `make test`/`make lint` run at Step 24, immediately before the single commit in Step 25.

**Files:**
- Modify `src/evalspec/schema.py` (add `is_kebab`; `slug`→`id` in `_validate_evals_v1` at `schema.py:185-197`)
- Modify `src/evalspec/mdformat.py` (`parse_eval_md` at `mdformat.py:185-223`; remove `NON_EVAL_MD` at `mdformat.py:41-44` and `load_suite_dir` at `mdformat.py:299-317`)
- Modify `src/evalspec/discovery.py` (`EvalCase` at `discovery.py:21-69`; `discover_eval_cases` at `discovery.py:229-258`; add `_evals_dirs`/`_eval_files`)
- Modify `src/evalspec/room.py` (`seed_room` param at `room.py:33`)
- Modify `src/evalspec/lint.py` (`lint_repo` at `lint.py:69-85`; `run` at `lint.py:88-99`)
- Modify `src/evalspec/cases.py` (`seeded_workdir` at `cases.py:111-117`)
- Modify `src/evalspec/plugin.py` (eval discovery at `plugin.py:658-659`)
- Modify `src/evalspec/sandbox.py` (`run_setup_sh` at `sandbox.py:251-279`; `SandboxSession.__init__`/`__aenter__` at `sandbox.py:318-379`; `arm_session` at `sandbox.py:410-443`)
- Modify `src/evalspec/execution.py` (`_run_arm_turns` params at `execution.py:208-247`; `run_eval_arm` wiring at `execution.py:321-341`)
- Test: `tests/test_discovery.py`, `tests/test_mdformat.py`, `tests/test_execution.py`, `tests/test_lint.py`, `tests/test_plugin.py`, `tests/test_readme_examples.py`, `tests/test_sandbox.py`

**Interfaces:**
- Consumes: `schema.is_kebab`, `schema._validate_history`, `mdformat.parse_eval_md`.
- Produces:
  - `schema.is_kebab(value: str) -> bool`.
  - `mdformat.parse_eval_md(path: Path) -> dict` returning `{"id": str, "prompt": str, "assertions": list[str], "history"?: list[dict]}`; `id` = `path.parent.name` when `path.name == "eval.md"`, else the `*.eval.md` stem; raises `MdFormatError` on any other filename.
  - `discovery.EvalCase(group: str, eval_dir: Path, eval_file: Path, eval: dict)` with properties: `eval_id -> str` (`eval["id"]`), `skill -> str` (alias of `group`), `param_id -> str` (`f"{group}-{eval_id}"`), `prompt -> str`, `assertions -> list[str]`, `history -> list[dict]`, `workspace_dir -> Path | None` (`eval_dir / "workspace"` when it is a dir).
  - `discovery.discover_eval_cases(repo_root: Path) -> list[EvalCase]` — crawls `**/evals/<group>/`, raises `schema.SchemaError` on a duplicate `(group, eval_id)` or a non-kebab `group`/`eval_id`, returns cases sorted by `(group, eval_id)`.
  - `room.seed_room(workspace_dir: Path | None, workdir: Path, today: str | None = None) -> dict` (param renamed; behavior unchanged).
  - `sandbox.run_setup_sh(sandbox, agent, *, setup_reldir: str, arm: str, model: str, eval_set: str = "", arm_env: dict | None = None) -> None` — runs `PROJECT_MOUNT/<setup_reldir>/setup.sh` if it exists (absent ⇒ no-op; nonzero exit ⇒ `RuntimeError`).
  - `sandbox.arm_session(*, …, setup_reldir: str | None = None, …)` and `SandboxSession(setup_reldir=…)` — `setup_reldir=None` skips setup.
  - `execution.run_eval_arm` computes `setup_reldir = str(eval_case.eval_dir.relative_to(project))` when `project` is not None, else `None`, and threads it through `_run_arm_turns(setup_reldir=…)`.

- [ ] **Step 1: Write failing `schema.is_kebab` test.**
  In `tests/test_schema.py` add:
  ```python
  def test_is_kebab() -> None:
      """Verify is kebab."""
      assert v.is_kebab("write-spec") is True
      assert v.is_kebab("Write Spec") is False
      assert v.is_kebab("") is False
  ```
- [ ] **Step 2: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_schema.py -k is_kebab -q` → `AttributeError: module 'evalspec.schema' has no attribute 'is_kebab'`.
- [ ] **Step 3: Add `is_kebab` and `slug`→`id` rename in `schema.py`.**
  After `_KEBAB_HINT` (`schema.py:48`) add:
  ```python
  def is_kebab(value: str) -> bool:
      """Return whether a string is a kebab-case identifier."""
      return bool(_ID_KEBAB.match(value))
  ```
  In `_validate_evals_v1` change `allowed_eval = {"slug", "history", "prompt", "assertions"}` to `{"id", "history", "prompt", "assertions"}`; replace the `slug` block at `schema.py:192-197` with an `id` block (same logic, key `"id"`, messages `path.id`, example `'"id": "happy-path"'`, dedupe message `duplicate id \`{eval_id}\``).
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_schema.py -k is_kebab -q` → pass. Update the remaining `test_schema.py` cases (`_doc` items) from `"slug"` to `"id"` and the match strings (`duplicate` still matches; `not kebab-case` still matches).
- [ ] **Step 4: Write failing mdformat id-derivation tests.**
  In `tests/test_mdformat.py` add real helpers + tests (and delete `test_load_suite_dir_*` and the two `test_discover_rejects_legacy_flat_*` are in discovery, not here):
  ```python
  def _write_eval_file(tmp_path: object, group: object, filename: object, body: object) -> object:
      """Write one evals/<group>/<filename>."""
      group_dir = tmp_path / group
      group_dir.mkdir(parents=True, exist_ok=True)
      (group_dir / filename).write_text(textwrap.dedent(body), encoding="utf-8")
      return group_dir / filename


  def test_parse_eval_md_id_from_folder_for_eval_md(tmp_path: object) -> None:
      """Verify parse eval md id from folder for eval.md."""
      path = _write_eval_file(
          tmp_path,
          "summarize-transcript",
          "eval.md",
          "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n",
      )

      ev = mdformat.parse_eval_md(path)

      assert ev["id"] == "summarize-transcript"


  def test_parse_eval_md_id_from_stem_for_dot_eval_md(tmp_path: object) -> None:
      """Verify parse eval md id from stem for *.eval.md."""
      path = _write_eval_file(
          tmp_path,
          "to-spec-activation",
          "write-spec.eval.md",
          "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n",
      )

      ev = mdformat.parse_eval_md(path)

      assert ev["id"] == "write-spec"


  def test_parse_eval_md_rejects_non_eval_filename(tmp_path: object) -> None:
      """Verify parse eval md rejects non-eval filename."""
      path = _write_eval_file(
          tmp_path, "g", "prompt.md", "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n"
      )

      with pytest.raises(mdformat.MdFormatError, match="eval.md"):
          mdformat.parse_eval_md(path)
  ```
  Update `_write_slug` and the existing parse tests: replace `_write_slug(tmp_path, "single-article", …)` with `_write_eval_file(tmp_path, "single-article", "eval.md", …)` and assert `ev["id"]` instead of `ev["slug"]`. Delete `test_load_suite_dir_assembles`, `test_load_suite_dir_rejects_dir_without_prompt`, `test_load_suite_dir_skips_dunder_tooling_dirs`, `test_load_suite_dir_ignores_per_eval_fixtures_dir`.
- [ ] **Step 5: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_mdformat.py -k "id_from or non_eval" -q`
  Expect: `KeyError: 'id'` / `AttributeError` (parse still emits `slug`, no filename guard).
- [ ] **Step 6: Rewrite `parse_eval_md` id derivation and delete `load_suite_dir`/`NON_EVAL_MD` in `mdformat.py`.**
  Replace `mdformat.py:185-189` with:
  ```python
  def parse_eval_md(path: Path) -> dict:
      """Parse one `eval.md` / `*.eval.md` file into schema input.

      The eval id is the parent folder name for `eval.md`, or the file stem for
      `<stem>.eval.md`. Any other filename is a hard error.
      """
      if path.name == "eval.md":
          eval_id = path.parent.name
      elif path.name.endswith(".eval.md"):
          eval_id = path.name[: -len(".eval.md")]
      else:
          raise MdFormatError(f"{path}: eval files must be named `eval.md` or `*.eval.md`")
      fm, body_lines = _split_frontmatter(path.read_text(encoding="utf-8"), path)
      _check_fm_keys(fm, _EVAL_FM, path)
      result: dict = {"id": eval_id}
      if "history" in fm:
          schema._validate_history(fm["history"], f"{path}: history")
          result["history"] = fm["history"]
  ```
  (The `## Prompt` / `## Assertions` parsing below `mdformat.py:197-223` is unchanged; the final `result["prompt"]`/`result["assertions"]` lines stay.)
  Delete `NON_EVAL_MD` (`mdformat.py:41-44`) and `load_suite_dir` (`mdformat.py:299-317`). Update the module docstring's `evals/<slug>/prompt.md` references (final wording in Task 3; here a minimal edit so lint passes — see Task 3 for the full docstring rewrite).
- [ ] **Step 7: Run to confirm pass.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_mdformat.py -q` → pass.
- [ ] **Step 8: Write failing discovery tests for the crawl.**
  Replace the discovery-body helpers and eval-discovery tests in `tests/test_discovery.py` with real ones (keep the `resolve_repo_root`, trigger, `pyproject_table`, and `resolve_environment_config` sections unchanged):
  ```python
  def _write_eval(
      tmp_path: object, evals_parent: object, group: object, filename: object = "eval.md"
  ) -> object:
      """Write one <evals_parent>/evals/<group>/<filename>."""
      group_dir = tmp_path / evals_parent / "evals" / group
      group_dir.mkdir(parents=True, exist_ok=True)
      (group_dir / filename).write_text(
          "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n", encoding="utf-8"
      )
      return group_dir


  def test_discover_finds_eval_md_and_dot_eval_md(tmp_path: object) -> None:
      """Verify discover finds eval.md and *.eval.md at any depth."""
      _write_eval(tmp_path, "skills/ingest", "summarize-transcript", "eval.md")
      _write_eval(tmp_path, "docs/probes", "to-spec-activation", "write-spec.eval.md")

      cases = discover_eval_cases(tmp_path)

      by_id = {case.param_id: case for case in cases}
      assert set(by_id) == {
          "summarize-transcript-summarize-transcript",
          "to-spec-activation-write-spec",
      }
      assert by_id["to-spec-activation-write-spec"].eval_id == "write-spec"
      assert by_id["summarize-transcript-summarize-transcript"].skill == "summarize-transcript"


  def test_discover_prunes_tmp_git_pycache_and_dot_dirs(tmp_path: object) -> None:
      """Verify discover prunes tmp/.git/__pycache__/dot dirs."""
      _write_eval(tmp_path, "tmp/evals-mirror", "hidden-a")
      _write_eval(tmp_path, ".git/x", "hidden-b")
      _write_eval(tmp_path, "src/__pycache__", "hidden-c")
      _write_eval(tmp_path, ".claude/skills/loc", "hidden-d")
      _write_eval(tmp_path, "skills/real", "kept")

      cases = discover_eval_cases(tmp_path)

      assert [case.param_id for case in cases] == ["kept-kept"]


  def test_discover_shared_workspace_for_sibling_evals(tmp_path: object) -> None:
      """Verify sibling *.eval.md files share one workspace/."""
      group_dir = _write_eval(tmp_path, "s", "suite", "one.eval.md")
      (group_dir / "two.eval.md").write_text(
          "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n"
      )
      (group_dir / "workspace").mkdir()
      (group_dir / "workspace" / "seed.md").write_text("body")

      cases = {case.eval_id: case for case in discover_eval_cases(tmp_path)}

      assert cases["one"].workspace_dir == group_dir / "workspace"
      assert cases["two"].workspace_dir == group_dir / "workspace"


  def test_discover_history_and_workspace(tmp_path: object) -> None:
      """Verify discover reads history and locates workspace/."""
      group_dir = tmp_path / "skills" / "archive" / "evals" / "clobber"
      group_dir.mkdir(parents=True)
      (group_dir / "eval.md").write_text(
          "---\nhistory:\n  - role: user\n    content: hi\n---\n\n"
          "## Prompt\n\nArchive ./x.\n\n## Assertions\n\n- [ ] it refused\n"
      )
      (group_dir / "workspace").mkdir()
      (group_dir / "workspace" / "x.md").write_text("body")

      [case] = discover_eval_cases(tmp_path)

      assert case.history == [{"role": "user", "content": "hi"}]
      assert case.workspace_dir == group_dir / "workspace"


  def test_discover_raises_on_duplicate_group_eval_pair(tmp_path: object) -> None:
      """Verify discover raises on duplicate (group, eval_id)."""
      _write_eval(tmp_path, "a", "happy", "eval.md")
      _write_eval(tmp_path, "b", "happy", "eval.md")

      with pytest.raises(schema.SchemaError, match="happy"):
          discover_eval_cases(tmp_path)


  def test_discover_no_evals_dir_is_empty(tmp_path: object) -> None:
      """Verify discover with no evals dir is empty."""
      assert discover_eval_cases(tmp_path) == []


  def test_discover_group_dir_without_eval_files_is_skipped(tmp_path: object) -> None:
      """Verify a group dir with no eval file is skipped, not raised."""
      (tmp_path / "evals" / "binder").mkdir(parents=True)
      (tmp_path / "evals" / "binder" / "corpus.yaml").write_text("x: 1")
      _write_eval(tmp_path, "skills/real", "kept")

      assert [case.param_id for case in discover_eval_cases(tmp_path)] == ["kept-kept"]
  ```
  Delete the now-obsolete eval-discovery tests: `test_discover_eval_cases_self_contained`, `test_discover_eval_cases_seed_and_fixtures`, `test_skill_with_only_triggers_still_discovers_no_eval_cases` (rewrite: assert `discover_eval_cases(tmp_path) == []` and one trigger case), `test_discover_rejects_legacy_flat_eval_file*`, `test_discover_eval_cases_one_per_slug`, `test_discover_eval_cases_sorted_by_skill`, `test_discover_eval_cases_no_skills_root`, `test_discover_eval_cases_skips_skill_without_evals`, `test_discover_eval_cases_raises_on_bad_schema` (rewrite to write `eval.md`), `test_discover_eval_cases_finds_both_roots`, `test_discover_eval_cases_local_only`, `test_discover_eval_cases_skips_local_skill_without_evals`, `test_discover_raises_on_duplicate_name_across_roots`, `test_output_and_trigger_evals_coexist` (rewrite: output eval as `evals/myskill/eval.md`, still one trigger case). Keep all trigger-discovery tests unchanged.
- [ ] **Step 9: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_discovery.py -k "discover_finds or prunes or duplicate_group or shared_workspace" -q`
  Expect: `TypeError`/`AttributeError` (old `EvalCase` shape, no `_evals_dirs`, `workspace_dir` missing).
- [ ] **Step 10: Rewrite `EvalCase` + `discover_eval_cases` in `discovery.py`.**
  Replace `discovery.py:21-69` (`EvalCase`) with:
  ```python
  @dataclass
  class EvalCase:
      """One discovered Markdown output eval, keyed on (group, eval_id)."""

      group: str  # the evals/<group>/ folder name — the artifact-path slot Phase 3 keeps
      eval_dir: Path  # evals/<group>/ — holds the eval file(s), workspace/, setup.sh
      eval_file: Path  # eval.md or <stem>.eval.md
      eval: dict  # {id, prompt, assertions, history?} from parse_eval_md

      @property
      def skill(self: object) -> str:
          """The group name — kept in the `<skill>` artifact-path slot this phase."""
          return self.group

      @property
      def eval_id(self: object) -> str:
          """Return the stable eval identifier used in reports and artifact paths."""
          return self.eval["id"]

      @property
      def param_id(self: object) -> str:
          """Return the pytest parameter id for this case."""
          return f"{self.group}-{self.eval_id}"

      @property
      def prompt(self: object) -> str:
          """Return the prompt text for this discovered case."""
          return self.eval["prompt"]

      @property
      def assertions(self: object) -> list[str]:
          """Return assertion text for this discovered case."""
          return self.eval["assertions"]

      @property
      def history(self: object) -> list[dict]:
          """Return prior-context turns for this discovered case."""
          return self.eval.get("history", [])

      @property
      def workspace_dir(self: object) -> Path | None:
          """Return the per-eval workspace directory when present."""
          workspace_dir = self.eval_dir / "workspace"
          return workspace_dir if workspace_dir.is_dir() else None
  ```
  Replace `discover_eval_cases` (`discovery.py:229-258`) and add the crawl helpers (place `_PRUNE_DIRS` near `_DEFAULT_EVAL_ROOTS`):
  ```python
  _PRUNE_DIRS = frozenset({"tmp", ".git", "__pycache__"})


  def _evals_dirs(repo_root: Path) -> list[Path]:
      """Every directory named `evals` under `repo_root`, scratch/mirror subtrees pruned.

      Prunes `tmp/` (the artifact root), `.git`, `__pycache__`, and every dot-prefixed
      dir before descending — the fixed deny-list that lets the crawl replace the old
      explicit `eval_roots`.
      """
      found: list[Path] = []
      for dirpath, dirnames, _files in os.walk(repo_root):
          dirnames[:] = sorted(
              name
              for name in dirnames
              if name not in _PRUNE_DIRS and not name.startswith(".")
          )
          if Path(dirpath).name == "evals":
              found.append(Path(dirpath))
      return sorted(found)


  def _eval_files(group_dir: Path) -> list[Path]:
      """The `eval.md` and sorted `*.eval.md` files directly under one group dir."""
      files: list[Path] = []
      canonical = group_dir / "eval.md"
      if canonical.is_file():
          files.append(canonical)
      files.extend(
          sorted(
              path
              for path in group_dir.iterdir()
              if path.is_file() and path.name.endswith(".eval.md")
          )
      )
      return files


  def discover_eval_cases(repo_root: Path) -> list[EvalCase]:
      """Discover Markdown output evals by crawling every `evals/<group>/` folder.

      Raises `schema.SchemaError` on a non-kebab `group`/`eval_id` or a duplicate
      `(group, eval_id)` pair, naming both source files.
      """
      seen: dict[tuple[str, str], Path] = {}
      cases: list[EvalCase] = []
      for evals_dir in _evals_dirs(repo_root):
          for group_dir in sorted(path for path in evals_dir.iterdir() if path.is_dir()):
              group = group_dir.name
              if group.startswith(".") or group.startswith("__"):
                  continue
              for eval_file in _eval_files(group_dir):
                  parsed = mdformat.parse_eval_md(eval_file)
                  eval_id = parsed["id"]
                  if not schema.is_kebab(group):
                      raise schema.SchemaError(
                          f"{eval_file}: group `{group}` is not kebab-case ({schema._KEBAB_HINT})"
                      )
                  if not schema.is_kebab(eval_id):
                      raise schema.SchemaError(
                          f"{eval_file}: eval id `{eval_id}` is not kebab-case "
                          f"({schema._KEBAB_HINT})"
                      )
                  key = (group, eval_id)
                  if key in seen:
                      raise schema.SchemaError(
                          f"duplicate eval {group}/{eval_id}: {seen[key]} and {eval_file}"
                      )
                  seen[key] = eval_file
                  cases.append(
                      EvalCase(group=group, eval_dir=group_dir, eval_file=eval_file, eval=parsed)
                  )
      return sorted(cases, key=lambda case: (case.group, case.eval_id))
  ```
  (`_skill_dirs` and `discover_trigger_cases` stay untouched — triggers keep the old path.)
- [ ] **Step 11: Run to confirm pass.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_discovery.py -q` → pass.
- [ ] **Step 12: Rename `seed_room` param and confirm `test_room` green.**
  `room.py:33`: `def seed_room(workspace_dir: Path | None, workdir: Path, today: str | None = None) -> dict:` and rename the internal `fixture_dir` references (`room.py:36-38`) to `workspace_dir`. Update the docstring first line to "Create the clean-room workdir from the eval's workspace and record baseline facts." `tests/test_room.py` calls `seed_room(fixture, vault)` positionally — no test change needed.
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_room.py -q` → pass.
- [ ] **Step 13: Move `cases.py`, `lint.py`, `plugin.py` to the new surface.**
  `cases.py:116`: `pre_run_shas = seed_room(eval_case.workspace_dir, workdir, today)`.
  `lint.py:69-85`: drop the `eval_roots` param and read `case.eval_file`:
  ```python
  def lint_repo(repo_root: Path) -> list[Finding]:
      """Lint discovered eval assertions for unjudgeable wording."""
      findings: list[Finding] = []
      for case in discovery.discover_eval_cases(repo_root):
          for text in case.assertions:
              for rule, message in lint_assertion(text):
                  findings.append(Finding(case.eval_file, case.eval_id, text, rule, message))
      return findings
  ```
  `lint.py:88`: `def run(repo_root: Path) -> int:` and `findings = lint_repo(repo_root)`.
  `plugin.py:657-659`: drop `eval_roots` from the eval branch:
  ```python
          repo_root = resolve_repo_root(metafunc.config)
          cases = discover_eval_cases(repo_root)
  ```
  (Leave `plugin.py:677-678` — the trigger branch still resolves `eval_roots`.)
- [ ] **Step 14: Update `test_lint.py`, `test_plugin.py`, `test_readme_examples.py`, `test_execution.py`.**
  `tests/test_lint.py` `_write_eval`: write `evals/<skill>/<slug>/eval.md` and assert `findings[0].file == slug_dir / "eval.md"`:
  ```python
  def _write_eval(
      tmp_path: object, assertions: object, *, skill: object = "demo", slug: object = "a"
  ) -> object:
      """Write eval."""
      group_dir = tmp_path / "skills" / skill / "evals" / slug
      group_dir.mkdir(parents=True, exist_ok=True)
      body = "".join(f"- [ ] {a}\n" for a in assertions)
      (group_dir / "eval.md").write_text(f"---\n{{}}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n{body}")
      return group_dir
  ```
  `tests/test_plugin.py` `_make_project`: write `evals/alpha/eval.md` and `evals/beta/eval.md` (not `prompt.md`); the node-id assertions (`myskill-alpha-baseline` etc.) are unchanged because `group`="alpha"/"beta" → wait: with `evals/alpha/eval.md`, group=`alpha`, eval_id=`alpha`, so `param_id`=`alpha-alpha`. Update `_make_project` to write `evals/myskill/alpha.eval.md` and `evals/myskill/beta.eval.md` so `group`=`myskill`, `eval_id`=`alpha`/`beta`, preserving `test_eval[myskill-alpha-baseline]`:
  ```python
  def _make_project(pytester, skill="myskill", arms_toml=ARMS_TOML) -> None:
      (pytester.path / "pyproject.toml").write_text(arms_toml)
      evals = pytester.path / "skills" / skill / "evals" / skill
      evals.mkdir(parents=True)
      (evals / "alpha.eval.md").write_text(ALPHA_MD)
      (evals / "beta.eval.md").write_text(BETA_MD)
      pytester.makepyfile(test_cases=DUMMY_CASES)
  ```
  In `test_malformed_schema_fails_collection` write `evals/myskill/bad.eval.md` with the unknown `id` frontmatter key.
  `tests/test_readme_examples.py`: write the extracted block to `eval_dir / "eval.md"` (not `prompt.md`) and `parse_eval_md(eval_dir / "eval.md")`; keep `eval_dir` named e.g. `demo` so `id`=`demo`.
  `tests/test_execution.py` `_case` (line 56-58): build the new `EvalCase`:
  ```python
  def _case(tmp_path: object, eval_obj: object, skill: object = "myskill") -> object:
      """Build the case test fixture — eval_obj is {id, prompt, assertions, history?}."""
      eval_dir = tmp_path / "skills" / skill / "evals" / eval_obj["id"]
      eval_dir.mkdir(parents=True, exist_ok=True)
      return EvalCase(
          group=skill, eval_dir=eval_dir, eval_file=eval_dir / "eval.md", eval=eval_obj
      )
  ```
  Across `test_execution.py` change every `eval_obj` literal's `"slug"` key to `"id"` (e.g. `{"id": "alpha", "prompt": …, "assertions": […]}`) and `"seed"`→`"history"`/`"text"`→`"content"` (already done for the one seed test in Task 1 — verify the `id` rename here). Artifact-path assertions (`workspace.arm_dir(tmp_path, "myskill", "alpha", …)`) are unchanged because `group`="myskill".
- [ ] **Step 15: Write failing `run_setup_sh` tests.**
  In `tests/test_sandbox.py` replace `test_run_setup_sh_uses_skill_cwd_and_env` / `test_run_setup_sh_passes_set_and_arm_env` / `test_run_setup_sh_nonzero_exit_raises` and the two `_LocalShellSandbox` tests with:
  ```python
  def test_run_setup_sh_runs_reldir_script_and_env() -> None:
      """Verify run setup sh runs the eval-dir script with cell env."""
      fake_sandbox, agent = _SetupShellSandbox(), _SetupAgent()

      asyncio.run(
          sandbox.run_setup_sh(
              fake_sandbox,
              agent,
              setup_reldir="skills/ingest/evals/single-article",
              arm="trial",
              model="opus",
          )
      )

      call = fake_sandbox.calls[-1]
      assert call["cwd"] == sandbox.PROJECT_MOUNT
      assert f"{sandbox.PROJECT_MOUNT}/skills/ingest/evals/single-article/setup.sh" in call["script"]
      assert "bash ./setup.sh" in call["script"]
      assert call["env"]["EVALSPEC_ARM"] == "trial"
      assert call["env"]["EVALSPEC_MODEL"] == "opus"


  def test_run_setup_sh_passes_set_and_arm_env() -> None:
      """Verify run setup sh passes set and arm env."""
      fake_sandbox, agent = _SetupShellSandbox(), _SetupAgent()

      asyncio.run(
          sandbox.run_setup_sh(
              fake_sandbox,
              agent,
              setup_reldir="skills/ingest/evals/x",
              arm="trial",
              model="opus",
              eval_set="popular-harnesses",
              arm_env={"ANTHROPIC_BASE_URL": "https://o"},
          )
      )

      env = fake_sandbox.calls[-1]["env"]
      assert env["EVALSPEC_SET"] == "popular-harnesses"
      assert env["ANTHROPIC_BASE_URL"] == "https://o"


  def test_run_setup_sh_nonzero_exit_raises() -> None:
      """Verify run setup sh nonzero exit raises."""
      fake_sandbox, agent = _SetupShellSandbox(exit_code=2, stderr="boom"), _SetupAgent()

      with pytest.raises(RuntimeError, match="setup.sh"):
          asyncio.run(
              sandbox.run_setup_sh(
                  fake_sandbox, agent, setup_reldir="skills/ingest/evals/x", arm="trial", model="opus"
              )
          )


  def test_run_setup_sh_absent_file_is_noop(tmp_path: object) -> None:
      """Verify run setup sh absent file is noop."""
      # A real /bin/sh under a project mount with no <reldir>/setup.sh → clean exit 0.
      (tmp_path / "evals" / "x").mkdir(parents=True)
      fake_sandbox = _LocalShellSandbox(tmp_path)

      asyncio.run(
          sandbox.run_setup_sh(
              fake_sandbox, _SetupAgent(), setup_reldir="evals/x", arm="trial", model="opus"
          )
      )


  def test_run_setup_sh_present_but_failing_propagates(tmp_path: object) -> None:
      """Verify run setup sh present but failing propagates."""
      eval_dir = tmp_path / "evals" / "x"
      eval_dir.mkdir(parents=True)
      (eval_dir / "setup.sh").write_text("exit 2\n")
      fake_sandbox = _LocalShellSandbox(tmp_path)

      with pytest.raises(RuntimeError, match="setup.sh"):
          asyncio.run(
              sandbox.run_setup_sh(
                  fake_sandbox, _SetupAgent(), setup_reldir="evals/x", arm="trial", model="opus"
              )
          )
  ```
  `_LocalShellSandbox` runs the script under a temp cwd; its `PROJECT_MOUNT` in the script must resolve to `tmp_path`. Update `_LocalShellSandbox.shell` to substitute the mount: since the script hardcodes `/project`, run it with cwd `tmp_path` and pre-seed `PROJECT_MOUNT`→`tmp_path` by having `run_setup_sh` build the path from `PROJECT_MOUNT`. Simplest: in these two real-shell tests set the script's mount by monkeypatching `sandbox.PROJECT_MOUNT` to `str(tmp_path)` via `monkeypatch.setattr(sandbox, "PROJECT_MOUNT", str(tmp_path))` and add `monkeypatch` to the signatures.
- [ ] **Step 16: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_sandbox.py -k run_setup_sh -q`
  Expect: `TypeError: run_setup_sh() got an unexpected keyword argument 'setup_reldir'`.
- [ ] **Step 17: Rewrite `run_setup_sh` in `sandbox.py`.**
  Replace `sandbox.py:251-279` with:
  ```python
  async def run_setup_sh(
      sandbox: object,
      agent: object,
      *,
      setup_reldir: str,
      arm: str,
      model: str,
      eval_set: str = "",
      arm_env: dict | None = None,
  ) -> None:
      """Run an eval's own setup.sh inside the arm sandbox when present.

      Locates the script at `PROJECT_MOUNT/<setup_reldir>/setup.sh` (the eval folder's
      path relative to the repo root). Missing ⇒ no-op; present ⇒ runs under bash from
      the eval dir and fails loudly on a nonzero exit. The `EVALSPEC_*` cell env reaches
      the script.
      """
      env = {**agent.cell_env(arm=arm, model=model, eval_set=eval_set), **(arm_env or {})}
      eval_dir = shlex.quote(f"{PROJECT_MOUNT}/{setup_reldir}")
      script = (
          "set -e\n"
          f"if [ -f {eval_dir}/setup.sh ]; then cd {eval_dir}; bash ./setup.sh; fi"
      )
      res = await sandbox.shell(script, env=env, cwd=PROJECT_MOUNT)
      if res.exit_code != 0:
          raise RuntimeError(
              f"setup.sh failed for `{setup_reldir}` arm `{arm}` "
              f"(exit {res.exit_code}): {res.stderr_text[-2000:]}"
          )
  ```
- [ ] **Step 18: Run to confirm pass.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_sandbox.py -k run_setup_sh -q` → pass.
- [ ] **Step 19: Write failing `arm_session` tests for `setup_reldir`.**
  In `tests/test_sandbox.py`, update `test_arm_session_runs_setup_sh_when_skill_set` → `..._when_reldir_set` (pass `setup_reldir="skills/ingest/evals/x"` to `arm_session`, assert the recorded `run_setup_sh` call carried that reldir) and `test_arm_session_skips_setup_sh_when_no_skill` → assert `setup_reldir=None` skips setup. Change every `arm_session(... skill=...)` call in this file to `setup_reldir=...`.
- [ ] **Step 20: Run to confirm fail.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_sandbox.py -k arm_session -q`
  Expect: `TypeError: __init__() got an unexpected keyword argument 'setup_reldir'`.
- [ ] **Step 21: Rewrite `SandboxSession`/`arm_session` in `sandbox.py`.**
  In `SandboxSession.__init__` (`sandbox.py:318-352`) replace the `skill: str | None = None` param with `setup_reldir: str | None = None`, store `self._setup_reldir = setup_reldir`, and drop the `self._skill` line. In `__aenter__` (`sandbox.py:363-377`) replace the `if self._skill is not None:` block:
  ```python
          # Install before the artifact baseline so only later agent-authored files surface.
          if self._setup_reldir is not None:
              try:
                  await run_setup_sh(
                      self._sandbox,
                      self._agent,
                      setup_reldir=self._setup_reldir,
                      arm=self._arm,
                      model=self._model,
                      eval_set=self._eval_set,
                      arm_env=self._arm_env,
                  )
              except BaseException:
                  await _stop_quietly(self._sandbox)
                  raise
  ```
  In `arm_session` (`sandbox.py:410-443`) replace the `skill=` param and pass-through with `setup_reldir=`.
- [ ] **Step 22: Run to confirm pass.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_sandbox.py -q` → pass.
- [ ] **Step 23: Thread `setup_reldir` through `execution.py`.**
  In `run_eval_arm` after `arm_name = arm.name` (`execution.py:303`) add:
  ```python
      # setup.sh lives in the eval folder, located by its path relative to the mount.
      setup_reldir = str(eval_case.eval_dir.relative_to(project)) if project is not None else None
  ```
  In the `_run_arm_turns(...)` call (`execution.py:322-341`) replace `skill=eval_case.skill,` with `setup_reldir=setup_reldir,` (keep `detect_skill=detect_skill,` — dispatch detection still keys on the group). In `_run_arm_turns` signature (`execution.py:208-226`) rename the `skill` param to `setup_reldir` and, in the `session_factory(...)` call (`execution.py:232-247`), replace `skill=skill,` with `setup_reldir=setup_reldir,`. The `_turn_transcript(..., skill=skill)` call at `execution.py:274` must keep the group name — change it to `skill=detect_skill` (both equal `eval_case.skill`; pass the value `_run_arm_turns` still has, so also keep a `skill` param OR pass `detect_skill`). Concretely: keep `_run_arm_turns` receiving `detect_skill` (already a param) and use it for `_turn_transcript(skill=detect_skill)`.
- [ ] **Step 24: Update `test_execution.py` factory-kwarg assertions and run the full unit suite to confirm pass.**
  In `tests/test_execution.py` any assertion on `factory_kwargs["skill"]` becomes `factory_kwargs["setup_reldir"]` (e.g. equal to `str((tmp_path / "skills" / "myskill" / "evals" / "alpha"))` relative to `tmp_path` = `"skills/myskill/evals/alpha"`). `fake_session_factory` accepts `**kwargs`, so no factory change needed.
  `make test` → green (this is the authoritative gate for the whole task: discovery, workspace, setup, lint, and plugin consumers all land together). `make lint` → green (fixes any docstring/line-length nits from the edits).
- [ ] **Step 25: Commit.**
  `git add src/evalspec/schema.py src/evalspec/mdformat.py src/evalspec/discovery.py src/evalspec/room.py src/evalspec/lint.py src/evalspec/cases.py src/evalspec/plugin.py src/evalspec/sandbox.py src/evalspec/execution.py tests/test_schema.py tests/test_mdformat.py tests/test_discovery.py tests/test_lint.py tests/test_plugin.py tests/test_readme_examples.py tests/test_execution.py tests/test_sandbox.py`
  `git commit -m "feat(evals): crawl **/evals for eval.md, key cases on (group, eval_id), workspace/, and per-eval setup.sh"`

---

## Task 3: Documentation — schema.md, module docstrings, README, quickstart

Rewrite every doc surface that describes the old `skills/<skill>/evals/<slug>/prompt.md` / `seed:` / `fixtures/` shape to the crawl-based layout. No code changes.

**Files:**
- Modify `docs/schema.md`
- Modify `src/evalspec/discovery.py` (module docstring `discovery.py:1-8`), `src/evalspec/mdformat.py` (module docstring `mdformat.py:1-19`), `src/evalspec/room.py` (module docstring `room.py:1-9`), `src/evalspec/sandbox.py` (`run_setup_sh` docstring — done in Task 2; here the module-level intent comment at `sandbox.py:293-294`), `src/evalspec/__init__.py` (docstring `__init__.py:1-8`)
- Modify `README.md`, `docs/quickstart.md`
- Test: `tests/test_readme_examples.py` (already writes `eval.md` from Task 2 — re-run to confirm the reworded README block still parses)

**Interfaces:** none (documentation).

- [ ] **Step 1: Rewrite `docs/schema.md`.**
  Replace the layout block (`docs/schema.md:5-10`) with the crawl layout:
  ```
  **/evals/<group>/eval.md          # id = folder name
  **/evals/<group>/<stem>.eval.md   # id = file stem (siblings share the folder)
  **/evals/<group>/workspace/       # optional starting files, copied into /workspace
  **/evals/<group>/setup.sh         # optional per-eval sandbox setup
  skills/<skill>/evals/trigger-evals.md  # evalspec-trigger/v1 (unchanged this phase)
  ```
  Rewrite the surrounding prose: discovery is a recursive `**/evals/` crawl (pruning `tmp/`, `.git`, `__pycache__`, dot-dirs), decoupled from `eval_roots`; identity is `(group, eval_id)` where `eval.md`→`group`=`eval_id`=folder name and `<stem>.eval.md`→`group`=folder, `eval_id`=stem; the full test id is `<group>-<eval_id>-<arm>` and artifact paths read `<group>/eval-<eval_id>/`; a duplicate `(group, eval_id)` fails loudly at collection naming both files. Call out explicitly that the dot-dir prune excludes `.claude/`, so any output evals under `.claude/skills/**/evals/` are no longer discovered (trigger discovery there is unaffected). Rename the "Output evals — the `evals/<slug>/prompt.md` format" section to the `eval.md` / `*.eval.md` format; change the frontmatter table row and the example to `history:` / `content:`; rename the `fixtures/` paragraph (`docs/schema.md:67`) to `workspace/`; rename the "### Seed" section to "### History" (`{role, content}`). Update "Validation rules" (`docs/schema.md:164-171`): the only output-eval frontmatter key is `history`; a history turn needs non-empty `role` and `content`; both `group` and `eval_id` must be kebab-case. Note per-eval `setup.sh` replaces the suite-level script.
- [ ] **Step 2: Rewrite module docstrings.**
  `discovery.py:1-8`: describe the recursive `**/evals/` crawl (deny-list pruning), `eval.md`/`*.eval.md` collection, `(group, eval_id)` identity + duplicate guard, and that trigger discovery still uses the skill-root path.
  `mdformat.py:1-19`: `eval.md` / `*.eval.md` (id from folder/stem), `history:` frontmatter, `## Prompt` + `## Assertions`; drop the `evals/<slug>/prompt.md` and `seed:` wording.
  `room.py:1-9`: `seed_room` stages the eval's `workspace/` (was `fixtures/`); `setup.sh` is per-eval inside the sandbox.
  `sandbox.py:293-294` comment: the project mounts read-only so a per-eval `setup.sh` can install the suite-specific skill.
  `__init__.py:1-8`: discovers output evals via the `**/evals/<group>/eval.md` crawl.
- [ ] **Step 3: Rewrite README + quickstart examples.**
  `README.md:32,56,89`: eval layout is `**/evals/<group>/eval.md` (or `<stem>.eval.md`), `workspace/` holds starting files, `history:` is the frontmatter context key, per-eval `setup.sh`. Keep the fenced `` ```markdown `` eval block (the one with `## Assertions`) valid — it already uses `history:` from Task 1.
  `docs/quickstart.md:87-89,193`: `skills/hello/evals/greets-by-name/prompt.md` → `skills/hello/evals/greets-by-name/eval.md`; `fixtures/` → `workspace/`; the `schema.md` bullet's `seed:` → `history:`.
- [ ] **Step 4: Verify docs + tested example.**
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_readme_examples.py -q` → pass.
  `make lint` → green (docstring line lengths within the ruff limit).
- [ ] **Step 5: Commit.**
  `git add docs/schema.md docs/quickstart.md README.md src/evalspec/discovery.py src/evalspec/mdformat.py src/evalspec/room.py src/evalspec/sandbox.py src/evalspec/__init__.py`
  `git commit -m "docs: describe the eval.md crawl, history, workspace/, and per-eval setup.sh"`

---

## Final verification (run after Task 3)

- [ ] `make test` — full offline suite green.
- [ ] `make lint` — package + docs pass.
- [ ] `grep -rEn "prompt\.md|\bseed\b|fixtures_dir|render_seed|load_suite_dir|NON_EVAL_MD" src/evalspec` — returns nothing in the eval-discovery path (only trigger/historical references survive, and only if legitimately unrelated).
- [ ] `grep -rEn "eval_case\.skill" src/evalspec/execution.py` — confirms `.skill` (the group alias) still feeds `workspace.arm_dir`, `grading["skill"]`, and `detect_skill`, so report/matrix keys are unchanged.
