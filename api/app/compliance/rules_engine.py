"""
rules_engine.py
---------------
Declarative, auditable compliance-rule evaluation.

A rule's logic is expressed as a JSON *condition tree* — never as a Python
string that gets eval()'d. The tree is made of:

  Group nodes:
    {"all":  [ ...nodes... ]}      -> AND
    {"any":  [ ...nodes... ]}      -> OR
    {"not":  <node>}               -> negation

  Leaf nodes (a single comparison):
    {"field": "customer.kyc_status", "op": "eq",  "value": "complete"}
    {"expr":  "(customer.existing_emi + customer.proposed_emi)"
              " / customer.monthly_household_income", "op": "lte", "value": 0.5}

  "field" does a safe dotted lookup; "expr" runs the safe arithmetic
  evaluator (safe_eval.eval_arithmetic). Exactly one of the two is present.

Each leaf returns a tri-state: True (pass), False (fail), or None
(inconclusive — a referenced field was missing / not evaluable). Groups
combine tri-states with three-valued logic so "we could not tell" never
silently becomes a pass or a fail.

A Rule couples a condition tree with metadata: severity (rule_type), the
governing regulation it enforces (citation + source), an optional
applies_when guard (so e.g. microfinance-only rules don't fire on personal
loans), required_fields, and human-readable remediation guidance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .safe_eval import eval_arithmetic, resolve_field, UnsafeExpressionError

# Tri-state result of evaluating a condition.
PASS = True
FAIL = False
UNKNOWN = None

_COMPARATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "contains": lambda a, b: b in a,
}


def _eval_leaf(node: Mapping[str, Any], context: Mapping[str, Any]) -> Optional[bool]:
    op = node.get("op")

    # Existence checks don't need a comparison value.
    if op in ("exists", "not_exists"):
        present = resolve_field(context, node["field"]) is not None
        return present if op == "exists" else not present

    if op not in _COMPARATORS:
        return UNKNOWN

    # Left-hand operand: either a resolved field or a computed expression.
    if "expr" in node:
        try:
            left: Any = eval_arithmetic(node["expr"], context)
        except UnsafeExpressionError:
            return UNKNOWN
    else:
        left = resolve_field(context, node["field"])
        if left is None:
            return UNKNOWN

    right = node.get("value")

    try:
        return bool(_COMPARATORS[op](left, right))
    except TypeError:
        # e.g. comparing None/str with a number -> can't tell.
        return UNKNOWN


def evaluate_condition(node: Mapping[str, Any], context: Mapping[str, Any]) -> Optional[bool]:
    """Evaluate a condition tree with three-valued (Kleene) logic."""
    if "all" in node:
        results = [evaluate_condition(c, context) for c in node["all"]]
        if any(r is FAIL for r in results):
            return FAIL
        if any(r is UNKNOWN for r in results):
            return UNKNOWN
        return PASS
    if "any" in node:
        results = [evaluate_condition(c, context) for c in node["any"]]
        if any(r is PASS for r in results):
            return PASS
        if any(r is UNKNOWN for r in results):
            return UNKNOWN
        return FAIL
    if "not" in node:
        inner = evaluate_condition(node["not"], context)
        return UNKNOWN if inner is UNKNOWN else (not inner)
    # Otherwise it's a leaf.
    return _eval_leaf(node, context)


@dataclass
class Rule:
    rule_id: str
    rule_name: str
    category: str
    rule_type: str  # "mandatory" | "internal_policy" | "advisory"
    condition: dict
    regulation: str = ""          # e.g. "RBI (Digital Lending) Directions, 2025"
    regulation_ref: str = ""      # e.g. "Para 5 — Disbursement & repayment"
    remediation: str = ""
    required_fields: list = field(default_factory=list)
    applies_when: Optional[dict] = None
    is_active: bool = True


@dataclass
class CheckResult:
    rule_id: str
    rule_name: str
    category: str
    rule_type: str
    status: str          # PASS | FAIL | WARNING | INCONCLUSIVE | NOT_APPLICABLE
    evidence: str
    regulation: str
    regulation_ref: str
    remediation: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _missing_required(rule: Rule, context: Mapping[str, Any]) -> list[str]:
    return [f for f in rule.required_fields if resolve_field(context, f) is None]


def evaluate_rule(rule: Rule, context: Mapping[str, Any]) -> CheckResult:
    """Evaluate a single rule against a loan-application context."""
    # 1. Applicability guard.
    if rule.applies_when is not None:
        applicable = evaluate_condition(rule.applies_when, context)
        if applicable is FAIL:
            return CheckResult(
                rule.rule_id, rule.rule_name, rule.category, rule.rule_type,
                "NOT_APPLICABLE", "Rule does not apply to this application type.",
                rule.regulation, rule.regulation_ref, "",
            )
        # If applicability itself is UNKNOWN we still try to evaluate; the
        # required-fields / condition logic below will surface any gaps.

    # 2. Required fields present?
    missing = _missing_required(rule, context)
    if missing:
        return CheckResult(
            rule.rule_id, rule.rule_name, rule.category, rule.rule_type,
            "INCONCLUSIVE", f"Missing required field(s): {', '.join(missing)}",
            rule.regulation, rule.regulation_ref, rule.remediation,
        )

    # 3. Main condition.
    outcome = evaluate_condition(rule.condition, context)

    if outcome is PASS:
        status, evidence = "PASS", f"Satisfied: {rule.rule_name}"
    elif outcome is FAIL:
        # Severity maps a failed condition to a status.
        status = {
            "mandatory": "FAIL",
            "internal_policy": "WARNING",
            "advisory": "WARNING",
        }.get(rule.rule_type, "WARNING")
        evidence = f"Not satisfied: {rule.rule_name}"
    else:
        status, evidence = "INCONCLUSIVE", "Condition could not be evaluated with the data provided."

    return CheckResult(
        rule.rule_id, rule.rule_name, rule.category, rule.rule_type,
        status, evidence, rule.regulation, rule.regulation_ref, rule.remediation,
    )


def evaluate_application(rules: list[Rule], context: Mapping[str, Any]) -> dict:
    """Run every active rule and roll up an overall decision."""
    checks = [
        evaluate_rule(r, context) for r in rules if r.is_active
    ]

    # Only rules that actually applied count toward the decision.
    graded = [c for c in checks if c.status != "NOT_APPLICABLE"]

    mandatory_fail = any(
        c.status == "FAIL" for c in graded
    )
    mandatory_inconclusive = any(
        c.status == "INCONCLUSIVE" and c.rule_type == "mandatory" for c in graded
    )
    has_warning = any(c.status == "WARNING" for c in graded)

    if mandatory_fail:
        overall = "FAIL"
        recommendation = (
            "Reject or route to senior manual review: one or more mandatory "
            "RBI/regulatory checks failed."
        )
    elif mandatory_inconclusive:
        overall = "NEEDS_INFO"
        recommendation = (
            "Request missing information: a mandatory check could not be "
            "evaluated with the data provided."
        )
    elif has_warning:
        overall = "PASS_WITH_WARNINGS"
        recommendation = (
            "Manual review recommended: passes all mandatory regulatory checks "
            "but one or more internal-policy checks were not met."
        )
    else:
        overall = "PASS"
        recommendation = "All regulatory and policy checks passed."

    passed = sum(1 for c in graded if c.status == "PASS")

    return {
        "overall_status": overall,
        "auto_approval_allowed": overall == "PASS",
        "manual_review_required": overall != "PASS",
        "summary": {
            "total_rules_evaluated": len(graded),
            "passed": passed,
            "failed": sum(1 for c in graded if c.status == "FAIL"),
            "warnings": sum(1 for c in graded if c.status == "WARNING"),
            "inconclusive": sum(1 for c in graded if c.status == "INCONCLUSIVE"),
            "not_applicable": sum(1 for c in checks if c.status == "NOT_APPLICABLE"),
        },
        "checks": [c.as_dict() for c in checks],
        "recommendation": recommendation,
    }
