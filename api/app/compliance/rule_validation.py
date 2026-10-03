"""What a compliance rule must look like before it can go live.

A live rule is evaluated against every application, and a 'mandatory' one can block an approval — so a
rule typed into the form by accident (a real case: id "on", name "on", the form's default condition,
no regulation, mandatory) must be refused, not stored. Checked on manual creation and when an
extracted candidate is approved. Seeded rules are code-reviewed and are not re-validated here.
"""
from __future__ import annotations

import re

from .rules_engine import _COMPARATORS

RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")      # KYC_003, DL_KFS_001, EXT_0002
RULE_TYPES = {"mandatory", "internal_policy", "advisory"}
OPS = set(_COMPARATORS) | {"exists", "not_exists"}
# the sections policy_context.build_context produces; a condition on anything else can never be evaluated
ROOTS = ("customer.", "loan_terms.", "risk.", "documents.", "application.")
_PATH_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")


def _known(path: str) -> bool:
    return path == "loan_type" or path.startswith(ROOTS)


def _condition_problems(node, where: str = "condition") -> list[str]:
    if not isinstance(node, dict) or not node:
        return [f"{where} must be a JSON object"]
    for group in ("all", "any"):
        if group in node:
            kids = node[group]
            if not isinstance(kids, list) or not kids:
                return [f"{where}.{group} must be a non-empty list"]
            return [p for i, k in enumerate(kids) for p in _condition_problems(k, f"{where}.{group}[{i}]")]
    if "not" in node:
        return _condition_problems(node["not"], f"{where}.not")
    out = []
    has_field, has_expr = "field" in node, "expr" in node
    if has_field == has_expr:
        out.append(f"{where} needs exactly one of 'field' or 'expr'")
    op = node.get("op")
    if op not in OPS:
        out.append(f"{where}: unknown operator {op!r} (use one of {', '.join(sorted(OPS))})")
    elif op not in ("exists", "not_exists") and "value" not in node:
        out.append(f"{where}: operator '{op}' needs a 'value'")
    if has_field and not (isinstance(node["field"], str) and _known(node["field"])):
        out.append(f"{where}: field {node.get('field')!r} is not an application field "
                   f"(expected customer.*, loan_terms.*, risk.*, documents.*, application.* or loan_type)")
    if has_expr:
        paths = _PATH_RE.findall(str(node["expr"]))
        if not paths:
            out.append(f"{where}: the expression references no application field")
        out += [f"{where}: {p!r} in the expression is not an application field" for p in paths if not _known(p)]
    return out


def rule_problems(rule: dict) -> list[str]:
    """Reasons this rule may not go live (empty list = acceptable)."""
    out = []
    rid = str(rule.get("rule_id") or "").strip()
    name = str(rule.get("rule_name") or "").strip()
    if not RULE_ID_RE.match(rid) or not 4 <= len(rid) <= 40:
        out.append("Rule ID must look like KYC_003 or DL_KFS_001: capital letters and digits in parts "
                   "joined by underscores (4-40 characters)")
    if len(name) < 10 or len(name.split()) < 2 or name.lower() == rid.lower():
        out.append("Rule name must say what the rule enforces (at least two words, 10+ characters)")
    if rule.get("rule_type") not in RULE_TYPES:
        out.append("Severity must be mandatory, internal_policy or advisory")
    if rule.get("rule_type") == "mandatory" and len(str(rule.get("regulation") or "").strip()) < 5:
        out.append("A mandatory rule can block approvals, so it must cite the regulation or policy it enforces")
    out += _condition_problems(rule.get("condition"))
    if rule.get("applies_when") is not None:
        out += _condition_problems(rule["applies_when"], "applies_when")
    return out
