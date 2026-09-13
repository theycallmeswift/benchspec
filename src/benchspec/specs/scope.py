"""Scope clauses: the `- if:` / `- unless:` expression that gates an assertion per arm.

A clause decides whether one assertion is graded in one arm without touching the
assertion's prose. Its expression is a small typed language, parsed here into a frozen
AST and evaluated against the arm's variables. Substitution happens at the AST level —
a `{VAR}` node reads its value at evaluation time, so a value containing spaces or the
word `and` compares as a plain string and can never re-tokenize.

Grammar (precedence low to high):

    expr       := and_expr ("or" and_expr)*
    and_expr   := not_expr ("and" not_expr)*
    not_expr   := "not" not_expr | comparison
    comparison := primary (("==" | "!=" | "<" | ">" | "<=" | ">=") primary)?
    primary    := INT | STRING | "true" | "false" | "{" NAME "}" | "(" expr ")"

    INT    := -?[0-9]+
    STRING := "..." | '...'      (no escapes)
    NAME   := [A-Z][A-Z0-9_]*    (always substitutes a string)

Typing is strict and fails loud: `==` / `!=` compare one type against itself; the
ordering operators take ints only; `and` / `or` / `not` take booleans only; the whole
expression must be a boolean. A bare word is a parse error, never an implicit string.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from benchspec.config.arms import Arm, expand_env
from benchspec.specs import schema

if TYPE_CHECKING:
    from benchspec.specs.discovery import EvalCase

Value = int | str | bool


class ScopeError(schema.SchemaError):
    """Signal a clause that cannot be parsed or evaluated."""

    pass


@dataclass(frozen=True)
class Literal:
    """A typed constant: an int, a quoted string, or `true` / `false`."""

    value: Value


@dataclass(frozen=True)
class Variable:
    """A `{NAME}` reference, substituted as a string at evaluation."""

    name: str


@dataclass(frozen=True)
class Compare:
    """A binary comparison between two operands."""

    operator: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Not:
    """Boolean negation."""

    operand: Expr


@dataclass(frozen=True)
class And:
    """Boolean conjunction."""

    left: Expr
    right: Expr


@dataclass(frozen=True)
class Or:
    """Boolean disjunction."""

    left: Expr
    right: Expr


Expr = Literal | Variable | Compare | Not | And | Or

_EQUALITY = frozenset({"==", "!="})
_KEYWORDS = frozenset({"and", "or", "not", "true", "false"})

_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<int>-?\d+)
  | (?P<string>"[^"]*"|'[^']*')
  | (?P<variable>\{(?P<name>[A-Z][A-Z0-9_]*)\})
  | (?P<bad_variable>\{[^}\s]*\}?)
  | (?P<operator>==|!=|<=|>=|<|>)
  | (?P<open>\()
  | (?P<close>\))
  | (?P<word>[^\s(){}"'=!<>]+)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class _Token:
    """One lexeme: its kind (a literal, variable, operator, paren, keyword, or `end`) and text."""

    kind: str
    text: str


def _lexeme(match: re.Match[str]) -> str:
    """The name of the `_TOKEN` alternative `match` took: its first participating named group."""
    return next(name for name, text in match.groupdict().items() if text is not None)


def _tokenize(expr: str) -> list[_Token]:
    """Split an expression into tokens, rejecting bare words and stray characters."""
    tokens: list[_Token] = []
    position = 0
    while position < len(expr):
        match = _TOKEN.match(expr, position)
        if match is None:
            raise ScopeError(f"unexpected character {expr[position]!r} at position {position}")

        position = match.end()
        kind = _lexeme(match)
        if kind == "space":
            continue

        if kind == "variable":
            tokens.append(_Token("variable", match.group("name")))
        elif kind == "bad_variable":
            raise ScopeError(
                f"{match.group(0)} is not a variable reference "
                "(expected `{NAME}` with NAME matching [A-Z][A-Z0-9_]*)"
            )
        elif kind == "word":
            word = match.group(0)
            if word not in _KEYWORDS:
                raise ScopeError(f"bare word `{word}` — quote it to compare a string")
            tokens.append(_Token(word, word))
        else:
            tokens.append(_Token(kind, match.group(0)))

    tokens.append(_Token("end", ""))
    return tokens


class _Parser:
    """A recursive-descent parser over a token list; one instance parses one expression."""

    def __init__(self, tokens: list[_Token]) -> None:
        """Start at the first token."""
        self._tokens = tokens
        self._position = 0

    def _peek(self) -> _Token:
        """The current token, without consuming it."""
        return self._tokens[self._position]

    def _advance(self) -> _Token:
        """Consume and return the current token."""
        token = self._tokens[self._position]
        self._position += 1

        return token

    def _expect(self, kind: str) -> _Token:
        """Consume a token of `kind` or fail naming what was found instead."""
        token = self._peek()
        if token.kind != kind:
            raise ScopeError(f"expected `{kind}`, got {_describe(token)}")

        return self._advance()

    def parse(self) -> Expr:
        """Parse the whole token stream; trailing tokens are an error."""
        expr = self._or()
        if self._peek().kind != "end":
            raise ScopeError(f"unexpected {_describe(self._peek())} after a complete expression")

        return expr

    def _or(self) -> Expr:
        """Parse `and_expr ("or" and_expr)*`."""
        expr = self._and()
        while self._peek().kind == "or":
            self._advance()
            expr = Or(expr, self._and())

        return expr

    def _and(self) -> Expr:
        """Parse `not_expr ("and" not_expr)*`."""
        expr = self._not()
        while self._peek().kind == "and":
            self._advance()
            expr = And(expr, self._not())

        return expr

    def _not(self) -> Expr:
        """Parse `"not" not_expr | comparison`."""
        if self._peek().kind == "not":
            self._advance()
            return Not(self._not())

        return self._comparison()

    def _comparison(self) -> Expr:
        """Parse `primary (operator primary)?`."""
        left = self._primary()
        if self._peek().kind != "operator":
            return left

        operator = self._advance().text
        return Compare(operator, left, self._primary())

    def _primary(self) -> Expr:
        """Parse a literal, a variable, or a parenthesized expression."""
        token = self._peek()
        if token.kind == "int":
            self._advance()
            return Literal(int(token.text))
        if token.kind == "string":
            self._advance()
            return Literal(token.text[1:-1])
        if token.kind in ("true", "false"):
            self._advance()
            return Literal(token.kind == "true")
        if token.kind == "variable":
            self._advance()
            return Variable(token.text)
        if token.kind == "open":
            self._advance()
            expr = self._or()
            self._expect("close")
            return expr
        raise ScopeError(f"expected a value, got {_describe(token)}")


def _describe(token: _Token) -> str:
    """Name a token for an error message."""
    return "end of expression" if token.kind == "end" else f"`{token.text}`"


def parse(expr: str) -> Expr:
    """Parse a clause expression into its AST.

    Args:
        expr: The raw expression text after `if:` / `unless:`.

    Returns:
        The expression tree.

    Raises:
        ScopeError: on any syntax error, including a bare unquoted word.
    """
    if not expr.strip():
        raise ScopeError("empty expression")

    return _Parser(_tokenize(expr)).parse()


def _walk(expr: Expr) -> Iterator[Expr]:
    """Yield every node of the tree, pre-order."""
    yield expr

    if isinstance(expr, Compare | And | Or):
        yield from _walk(expr.left)
        yield from _walk(expr.right)
    elif isinstance(expr, Not):
        yield from _walk(expr.operand)


def names(expr: Expr) -> set[str]:
    """Every `{VAR}` name the expression references."""
    return {node.name for node in _walk(expr) if isinstance(node, Variable)}


def _kind(value: Value) -> str:
    """The clause-language type name of a value; `bool` is checked before its `int` base."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    return "str"


def _compare(operator: str, left: Value, right: Value) -> bool:
    """Apply one comparison operator, refusing cross-type and non-int ordering operands."""
    left_kind, right_kind = _kind(left), _kind(right)
    if left_kind != right_kind:
        raise ScopeError(
            f"`{operator}` compares {left_kind} {left!r} against {right_kind} {right!r}; "
            "both sides must be the same type"
        )

    if operator in _EQUALITY:
        return (left == right) if operator == "==" else (left != right)

    if left_kind != "int" or not (isinstance(left, int) and isinstance(right, int)):
        raise ScopeError(f"`{operator}` orders ints only, got {left_kind} {left!r}")

    return _ordering(operator, left, right)


def _ordering(operator: str, left: int, right: int) -> bool:
    """Apply one ordering operator to two ints."""
    if operator == "<":
        return left < right
    if operator == ">":
        return left > right
    if operator == "<=":
        return left <= right
    return left >= right


def _boolean(value: Value, *, where: str) -> bool:
    """Require a boolean operand; there is no truthiness in the clause language."""
    if not isinstance(value, bool):
        raise ScopeError(f"`{where}` takes booleans only, got {_kind(value)} {value!r}")

    return value


def _unknown_variables(unknown: Iterable[str], variables: Mapping[str, str]) -> ScopeError:
    """The error for `{NAME}` references `variables` lacks, listing the names it has."""
    missing = ", ".join(f"{{{name}}}" for name in sorted(unknown))
    available = ", ".join(sorted(variables)) or "none"

    return ScopeError(f"unknown variable {missing}; available: {available}")


def _evaluate(expr: Expr, variables: Mapping[str, str]) -> Value:
    """Evaluate a subtree to its typed value."""
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Variable):
        if expr.name not in variables:
            raise _unknown_variables({expr.name}, variables)

        return variables[expr.name]
    if isinstance(expr, Compare):
        return _compare(
            expr.operator, _evaluate(expr.left, variables), _evaluate(expr.right, variables)
        )
    if isinstance(expr, Not):
        return not _boolean(_evaluate(expr.operand, variables), where="not")

    where = "and" if isinstance(expr, And) else "or"
    # Both sides are evaluated so a type error on the right surfaces on every arm.
    left = _boolean(_evaluate(expr.left, variables), where=where)
    right = _boolean(_evaluate(expr.right, variables), where=where)

    return (left and right) if isinstance(expr, And) else (left or right)


def evaluate(expr: Expr, variables: Mapping[str, str]) -> bool:
    """Evaluate an expression to a boolean under `variables`.

    Args:
        expr: A parsed expression.
        variables: `{NAME}` values; every value is a string.

    Returns:
        The expression's boolean value.

    Raises:
        ScopeError: an unknown variable (naming the available ones), a cross-type
            comparison, an ordering on a non-int, a non-boolean logical operand, or a
            non-boolean result.
    """
    result = _evaluate(expr, variables)
    if not isinstance(result, bool):
        raise ScopeError(f"expression is {_kind(result)} {result!r}, not a boolean")

    return result


def clause_text(clause: Mapping[str, str]) -> str:
    """Render a parsed clause as `if: <expr>` / `unless: <expr>`."""
    return f"{clause['key']}: {clause['expr']}"


def applies(clause: Mapping[str, str], variables: Mapping[str, str]) -> bool:
    """Whether an assertion carrying `clause` is graded under `variables`.

    `unless: X` is `if: not (X)`. Every failure is re-raised with the clause text so an
    author can find the line.

    Raises:
        ScopeError: any parse or evaluation failure, naming the clause.
    """
    try:
        holds = evaluate(parse(clause["expr"]), variables)
    except ScopeError as error:
        raise ScopeError(f"`{clause_text(clause)}`: {error}") from error

    return holds if clause["key"] == "if" else not holds


def arm_variables(arm: Arm, *, baseline: str | None, eval_set: str) -> dict[str, str]:
    """The variables a clause sees for `arm`: the cell env plus the arm's own `env`.

    The arm's `env` is layered raw — a `$VAR` reference is expanded only when a clause
    reads it (see `applicable`), so an unreferenced secret is never read at collection.

    Args:
        arm: The arm being resolved.
        baseline: The set's baseline arm name, or None when the set has none.
        eval_set: The explicitly selected set name; empty on a default-set run.

    Returns:
        The variable mapping, `BENCHSPEC_*` first, arm `env` on top.
    """
    return {
        "BENCHSPEC_ARM": arm.name,
        "BENCHSPEC_MODEL": arm.model,
        "BENCHSPEC_HARNESS": arm.harness,
        "BENCHSPEC_SET": eval_set,
        "BENCHSPEC_BASELINE": baseline or "",
        **arm.env,
    }


def _resolve(
    referenced: set[str], variables: Mapping[str, str], environ: Mapping[str, str]
) -> dict[str, str]:
    """Expand exactly the referenced variables, failing on an unknown name."""
    unknown = referenced - set(variables)
    if unknown:
        raise _unknown_variables(unknown, variables)

    return expand_env({name: variables[name] for name in referenced}, environ)


def applicable(case: EvalCase, arm: Arm, *, baseline: str | None, eval_set: str) -> list[bool]:
    """One flag per assertion of `case`: True where it is graded in `arm`.

    A line without a clause is always graded. Resolution happens before any sandbox
    boots, so a failure here costs nothing.

    Args:
        case: The discovered eval.
        arm: The arm being resolved.
        baseline: The set's baseline arm name, or None.
        eval_set: The explicitly selected set name; empty on a default-set run.

    Returns:
        Flags aligned with `case.assertions`.

    Raises:
        ScopeError: any parse, unknown-name, expansion, or type failure, naming the eval
            file, the clause, and the arm.
    """
    variables = arm_variables(arm, baseline=baseline, eval_set=eval_set)

    flags: list[bool] = []
    for clause in case.clauses:
        if clause is None:
            flags.append(True)
            continue

        try:
            expr = parse(clause["expr"])
            resolved = _resolve(names(expr), variables, os.environ)
            holds = evaluate(expr, resolved)
        except schema.SchemaError as error:
            raise ScopeError(
                f"{case.eval_file}: `{clause_text(clause)}` cannot be resolved for arm "
                f"`{arm.name}`: {error}"
            ) from error

        flags.append(holds if clause["key"] == "if" else not holds)

    return flags
