# Python style

Apply this guide after [development.md](development.md). `make lint` checks it
with Ruff and ty.

- **Typed throughout.** Parse untyped input (JSON, YAML, HTTP bodies) at the
  boundary into dataclasses, or narrow it with `isinstance`; never let `Any`
  travel inward.
- **Dataclasses for data, `Protocol` for seams,** so a test fake satisfies a
  seam structurally.
- **Google-style docstrings** on every module, class, and function. Test
  functions skip them; the name states the behavior.
- **`pathlib` for paths,** and `encoding="utf-8"` on every text read and write.
- **Keyword-only arguments** (`*,`) once a function takes more than two
  parameters of the same type.
- **Errors are exception classes named for the domain,** with messages that
  name the file or field at fault.
- **Command-line output goes through a logger,** never `print`.
- **uv owns the environment.** Add dependencies with `uv add`; never
  `pip install` into `.venv`.

```python
@pytest.mark.parametrize(
    ("bank", "ledger", "unmatched_amounts"),
    [
        ([100, 200], [200, 100], []),
        ([300, 100], [100], [300]),
        ([100, 100], [100], [100]),
    ],
)
def test_reports_bank_amounts_with_no_ledger_match(
    bank: list[int], ledger: list[int], unmatched_amounts: list[int]
) -> None:
    assert unmatched(bank, ledger) == unmatched_amounts
```

## In this repository

- **Python 3.11 through 3.14.** CI runs the tests on each; write nothing
  newer than 3.11 syntax.
- **`from __future__ import annotations` is the first import,** right after
  the module docstring, in every file.
- **Imports are absolute** (`from benchspec.sandbox import provision`), never
  relative, and never wildcard.
- **Indent multiline strings with the code around them** and strip the indent
  with `textwrap.dedent`. Start the literal with `"""\` so the first line
  isn't blank. Docstrings are exempt.
- **An optional dependency gets a guarded import at the top.** Import the
  module, fall back to `None`, and check `is None` where it's used, so every
  call stays type-checked. Tests stub the module attribute, not `sys.modules`.
- **houserules checks what Ruff can't,** such as section-header comments and
  single-letter names; its rules are in `.houserules`.

```python
try:
    import microsandbox
    import microsandbox.errors
except ImportError:
    microsandbox = None


async def restore(snapshot: str, name: str) -> Sandbox:
    """Restore a sandbox from a snapshot."""
    if microsandbox is None:
        raise SandboxError("microsandbox is not installed")

    return await microsandbox.Sandbox.restore(snapshot, name=name)
```
