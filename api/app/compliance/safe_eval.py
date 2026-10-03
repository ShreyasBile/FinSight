"""
safe_eval.py
------------
Safe, whitelist-based arithmetic evaluator used by the rules engine.

The original prototype called Python's built-in eval() directly on rule
strings coming from the database. That is a remote-code-execution hole: a
crafted rule (or a compromised DB row) could run arbitrary code on the
server. This module removes eval() entirely.

Two things live here:

1. resolve_field(context, "customer.monthly_income")
   Safe dotted-path lookup into the (nested) loan-application dict. Missing
   keys return None instead of raising.

2. eval_arithmetic("(customer.existing_emi + customer.proposed_emi) / customer.monthly_income", context)
   Evaluates a *restricted arithmetic* expression over dotted field
   references. Only numbers, the four binary operators (+ - * /), unary
   minus, and parentheses are permitted. Every other AST node type
   (function calls, attribute access, names, comprehensions, subscripts,
   dunder tricks, etc.) is rejected before any evaluation happens.

This keeps compliance rules expressive enough for ratios like FOIR while
being provably unable to execute anything other than arithmetic.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Mapping

# A field token looks like  customer.monthly_income  or  risk.tamper_score
_FIELD_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# AST node types we allow inside an arithmetic expression. Anything not in
# this set causes a hard rejection.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Name,
    ast.Load,
    ast.Constant,
)


class UnsafeExpressionError(ValueError):
    """Raised when an expression contains a disallowed construct."""


def resolve_field(context: Mapping[str, Any], path: str) -> Any:
    """Follow a dotted path into a nested mapping, returning None if any
    step is missing or not a mapping."""
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _to_number(value: Any) -> float:
    """Coerce a resolved field value to a float for arithmetic. Booleans
    count as 1/0; None and non-numeric values raise so the caller can treat
    the expression as inconclusive."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise UnsafeExpressionError(f"Non-numeric operand: {value!r}")


def eval_arithmetic(expr: str, context: Mapping[str, Any]) -> float:
    """Safely evaluate a restricted arithmetic expression.

    Dotted field tokens are resolved from *context* and substituted with
    placeholder identifiers before parsing, so the AST only ever contains
    numbers and operators. Raises UnsafeExpressionError on anything
    disallowed or on missing/non-numeric fields.
    """
    variables: dict[str, float] = {}

    def _sub(match: re.Match) -> str:
        token = match.group(0)
        # Pure numbers are matched by the regex too (e.g. "0"); leave bare
        # numbers alone — but our token regex requires a leading letter/_,
        # so numbers never reach here. Every match is a field path.
        placeholder = f"_v{len(variables)}"
        raw = resolve_field(context, token)
        if raw is None:
            raise UnsafeExpressionError(f"Missing field: {token}")
        variables[placeholder] = _to_number(raw)
        return placeholder

    substituted = _FIELD_TOKEN.sub(_sub, expr)

    try:
        tree = ast.parse(substituted, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Syntax error: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsafeExpressionError(
                f"Disallowed expression element: {type(node).__name__}"
            )
        # Only allow our own placeholder identifiers as names.
        if isinstance(node, ast.Name) and node.id not in variables:
            raise UnsafeExpressionError(f"Unknown identifier: {node.id}")
        # Constants must be plain numbers, never strings/bytes/bools/None.
        # (bool is a subclass of int, so reject it explicitly.)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)
            ):
                raise UnsafeExpressionError(f"Disallowed constant: {node.value!r}")

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return variables[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            return -operand if isinstance(node.op, ast.USub) else +operand
        if isinstance(node, ast.BinOp):
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise UnsafeExpressionError("Division by zero")
                return left / right
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise UnsafeExpressionError("Modulo by zero")
                return left % right
        raise UnsafeExpressionError(f"Unsupported node: {type(node).__name__}")

    return _eval(tree)
