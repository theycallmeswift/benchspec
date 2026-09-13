"""The scope-clause expression language: parsing, typing, evaluation, and arm variables."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from benchspec.config.arms import Arm
from benchspec.specs import mdformat, scope
from benchspec.specs.discovery import EvalCase


def _holds(expr: str, **variables: str) -> bool:
    """Parse and evaluate `expr` against the given variables."""
    return scope.evaluate(scope.parse(expr), variables)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("1 == 1", True, id="int-equal"),
        pytest.param("-3 < 2", True, id="negative-int"),
        pytest.param('"en-GB" == "en-GB"', True, id="double-quoted-string"),
        pytest.param("'en-GB' != \"en-US\"", True, id="single-quoted-string"),
        pytest.param("true", True, id="true"),
        pytest.param("false", False, id="false"),
        pytest.param("true == false", False, id="bool-equality"),
    ],
)
def test_typed_literals_evaluate(expr: str, expected: bool) -> None:
    """Verify ints, both string quotings, and the two booleans are literals of their own type."""
    assert _holds(expr) is expected


def test_not_binds_tighter_than_and_which_binds_tighter_than_or() -> None:
    """Verify `not` > `and` > `or`: `not a and b or c` reads `((not a) and b) or c`."""
    assert _holds("not true and false or true") is True
    assert _holds("not true and false or false") is False
    assert _holds("false or true and false") is False


def test_parentheses_override_precedence() -> None:
    """Verify parentheses group an `or` under an `and` and a `not` over a comparison."""
    assert _holds("false and (false or true)") is False
    assert _holds("not (1 == 1)") is False


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("1 < 2", True, id="lt"),
        pytest.param("2 > 2", False, id="gt"),
        pytest.param("2 <= 2", True, id="le"),
        pytest.param("3 >= 4", False, id="ge"),
    ],
)
def test_ordering_on_ints(expr: str, expected: bool) -> None:
    """Verify the four ordering operators compare ints."""
    assert _holds(expr) is expected


def test_bare_word_is_a_parse_error() -> None:
    """Verify an unquoted word is rejected at parse time, never read as a string."""
    with pytest.raises(scope.ScopeError, match="bare word `codex`"):
        scope.parse("{BENCHSPEC_HARNESS} == codex")


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("{BENCHSPEC_ARM}", id="string-result"),
        pytest.param("1", id="int-result"),
    ],
)
def test_non_boolean_expression_is_rejected(expr: str) -> None:
    """Verify a string or int result is an error: the language has no truthiness."""
    with pytest.raises(scope.ScopeError, match="not a boolean"):
        _holds(expr, BENCHSPEC_ARM="trial")


def test_cross_type_comparison_is_rejected() -> None:
    """Verify a `{VAR}` (always a string) compared against an int raises, never `False`."""
    with pytest.raises(scope.ScopeError, match="both sides must be the same type"):
        _holds("{COUNT} == 1", COUNT="1")


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param('"a" < "b"', id="str"),
        pytest.param("true < false", id="bool"),
    ],
)
def test_ordering_on_str_or_bool_is_rejected(expr: str) -> None:
    """Verify `<` and friends take ints only."""
    with pytest.raises(scope.ScopeError, match="orders ints only"):
        _holds(expr)


def test_logical_operators_take_booleans_only() -> None:
    """Verify `and` over a string operand raises rather than coercing it."""
    with pytest.raises(scope.ScopeError, match="`and` takes booleans only"):
        _holds('{BENCHSPEC_ARM} and true', BENCHSPEC_ARM="trial")


def test_variable_value_with_spaces_and_keywords_compares_as_a_plain_string() -> None:
    """Verify substitution is at the AST level: a value is never re-tokenized."""
    value = 'x and "y" or not (z)'

    assert _holds('{NAME} == \'x and "y" or not (z)\'', NAME=value) is True
    assert _holds('{NAME} == "x"', NAME=value) is False


def test_unknown_variable_names_it_and_lists_available_names() -> None:
    """Verify an unknown `{VAR}` raises naming it and every available name, sorted."""
    with pytest.raises(scope.ScopeError) as exc_info:
        _holds("{NOPE} == 'x'", ZED="1", ALPHA="2")

    assert "{NOPE}" in str(exc_info.value)
    assert "available: ALPHA, ZED" in str(exc_info.value)


def test_names_collects_every_reference() -> None:
    """Verify `names` walks the whole tree."""
    expr = scope.parse('not ({A} == "1") and ({B} != {C} or true)')

    assert scope.names(expr) == {"A", "B", "C"}


def test_applies_inverts_unless() -> None:
    """Verify `unless: X` is `if: not (X)`."""
    variables = {"BENCHSPEC_HARNESS": "codex"}
    clause = {"key": "unless", "expr": '{BENCHSPEC_HARNESS} == "codex"'}

    assert scope.applies(clause, variables) is False
    assert scope.applies({**clause, "key": "if"}, variables) is True


def test_applies_error_names_the_clause() -> None:
    """Verify a failure through `applies` carries the clause text for the author."""
    with pytest.raises(scope.ScopeError, match=r"`if: \{NOPE\} == 1`"):
        scope.applies({"key": "if", "expr": "{NOPE} == 1"}, {})


def test_clause_text_renders_key_and_expression() -> None:
    """Verify `clause_text` is the `key: expr` form used in grading.json and lint output."""
    assert scope.clause_text({"key": "unless", "expr": "true"}) == "unless: true"


def test_arm_variables_carry_the_cell_env_and_arm_env() -> None:
    """Verify the five BENCHSPEC_* names plus the arm's env, env layered last."""
    arm = Arm("trial", "codex", "gpt", env={"GREETING_LOCALE": "en-GB"})

    variables = scope.arm_variables(arm, baseline="baseline", eval_set="smoke")

    assert variables == {
        "BENCHSPEC_ARM": "trial",
        "BENCHSPEC_MODEL": "gpt",
        "BENCHSPEC_HARNESS": "codex",
        "BENCHSPEC_SET": "smoke",
        "BENCHSPEC_BASELINE": "baseline",
        "GREETING_LOCALE": "en-GB",
    }


def test_baseline_is_empty_without_a_baseline() -> None:
    """Verify a set with no baseline exports `BENCHSPEC_BASELINE` as the empty string."""
    arm = Arm("trial", "claude-code", "sonnet")

    variables = scope.arm_variables(arm, baseline=None, eval_set="")

    assert variables["BENCHSPEC_BASELINE"] == ""


def _eval_case(tmp_path: Path, checklist: str) -> EvalCase:
    """Write `skills/demo/evals/alpha/eval.md` with `checklist` and parse it for real."""
    eval_dir = tmp_path / "skills" / "demo" / "evals" / "alpha"
    eval_dir.mkdir(parents=True)
    eval_file = eval_dir / "eval.md"
    eval_file.write_text(
        dedent("""\
            ---
            ---

            ## Prompt

            Do it.

            ## Assertions

        """)
        + checklist
    )
    return EvalCase(
        group="demo", eval_dir=eval_dir, eval_file=eval_file, eval=mdformat.parse_eval_md(eval_file)
    )


def test_applicable_is_true_for_unclaused_lines_and_decides_claused_ones(tmp_path: Path) -> None:
    """Verify one flag per assertion: unclaused lines always grade, claused ones per arm."""
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./out.md exists
            - [ ] Skill `hello` invoked
              - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
        """),
    )

    baseline_flags = scope.applicable(
        eval_case, Arm("baseline", "claude-code", "sonnet"), baseline="baseline", eval_set=""
    )
    trial_flags = scope.applicable(
        eval_case, Arm("trial", "claude-code", "sonnet"), baseline="baseline", eval_set=""
    )

    assert baseline_flags == [True, False]
    assert trial_flags == [True, True]


def test_applicable_unknown_name_names_the_eval_clause_and_arm(tmp_path: Path) -> None:
    """Verify the collection-time error carries everything an author needs to find it."""
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./out.md exists
              - if: {NOPE} == "x"
        """),
    )

    with pytest.raises(scope.ScopeError) as exc_info:
        scope.applicable(
            eval_case, Arm("trial", "claude-code", "sonnet"), baseline=None, eval_set=""
        )

    message = str(exc_info.value)
    assert str(eval_case.eval_file) in message
    assert '`if: {NOPE} == "x"`' in message
    assert "arm `trial`" in message
    assert "{NOPE}" in message
    assert "BENCHSPEC_BASELINE" in message  # the available names are listed


def test_arm_env_reference_expands_only_when_a_clause_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an unreferenced `$VAR` secret is never read; a referenced one is expanded."""
    monkeypatch.delenv("UNSET_SECRET", raising=False)
    monkeypatch.setenv("LOCALE_FROM_HOST", "en-GB")
    arm = Arm(
        "trial",
        "claude-code",
        "sonnet",
        env={"SECRET": "$UNSET_SECRET", "GREETING_LOCALE": "$LOCALE_FROM_HOST"},
    )
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./out.md exists
              - if: {GREETING_LOCALE} == "en-GB"
        """),
    )

    flags = scope.applicable(eval_case, arm, baseline=None, eval_set="")

    assert flags == [True]


def test_arm_env_reference_that_is_unset_fails_when_a_clause_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a referenced `$VAR` that is unset on the host is a ScopeError naming the arm."""
    monkeypatch.delenv("UNSET_SECRET", raising=False)
    arm = Arm("trial", "claude-code", "sonnet", env={"SECRET": "$UNSET_SECRET"})
    eval_case = _eval_case(
        tmp_path,
        dedent("""\
            - [ ] ./out.md exists
              - if: {SECRET} == "x"
        """),
    )

    with pytest.raises(scope.ScopeError, match="UNSET_SECRET") as exc_info:
        scope.applicable(eval_case, arm, baseline=None, eval_set="")

    assert "arm `trial`" in str(exc_info.value)
