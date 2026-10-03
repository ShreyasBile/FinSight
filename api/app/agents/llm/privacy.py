"""Privacy policy for LLM calls.

Local Ollama keeps every document on this machine. A hosted provider may only
be used after a Head Manager has explicitly acknowledged, in Settings, that
applicant data will leave the machine — and never while tests or benchmarks
run (FINSIGHT_FORBID_HOSTED=1), because the repo's storage/ holds real
applicants' documents. The guard fails closed.

For hosted TEXT lanes (memo, supervisor, narrations) the evidence bundle is
pseudonymised first: names become "Applicant", PAN/Aadhaar/account numbers
keep only their last 4 characters, addresses are dropped. Image lanes cannot
be redacted and are listed as such in the Settings privacy notice.
"""
from __future__ import annotations

import re

from ...config import settings
from .catalog import HOSTED_PROVIDERS, LLM_AGENTS


class PrivacyBlocked(PermissionError):
    """A hosted LLM call was refused by policy."""


def check(agent_id: str, provider: str, hosted_acknowledged: bool) -> None:
    if provider not in HOSTED_PROVIDERS:
        return
    if settings.finsight_forbid_hosted:
        raise PrivacyBlocked("hosted LLM calls are forbidden in this environment (FINSIGHT_FORBID_HOSTED)")
    if not hosted_acknowledged:
        raise PrivacyBlocked(
            f"'{agent_id}' is set to a hosted provider ({provider}) but no Head Manager has "
            "acknowledged that applicant data will leave this machine")


def affected_agents(provider: str) -> list[dict]:
    """Agents whose data would leave the machine if `provider` served them."""
    if provider not in HOSTED_PROVIDERS:
        return []
    return [{"agent_id": u.agent_id, "label": u.label, "data": u.data_class,
             "redactable": u.data_class != "pii_image"} for u in LLM_AGENTS.values()]


_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_LONGNUM_RE = re.compile(r"\b\d{9,18}\b")
_NAME_KEYS = ("name", "holder", "father", "insured", "owner", "applicant", "employee_name")
_DROP_KEYS = ("address", "pincode", "dob", "mobile", "email")


def _mask(s: str) -> str:
    s = _PAN_RE.sub(lambda m: "XXXXXX" + m.group(0)[-4:], s)
    s = _AADHAAR_RE.sub(lambda m: "XXXX XXXX " + re.sub(r"\D", "", m.group(0))[-4:], s)
    return _LONGNUM_RE.sub(lambda m: "X" * (len(m.group(0)) - 4) + m.group(0)[-4:], s)


def pseudonymize(obj, names: set[str] | None = None, _key: str = ""):
    """Recursively pseudonymise an evidence bundle before a hosted text call."""
    names = names if names is not None else _collect_names(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(d in kl for d in _DROP_KEYS):
                continue
            if isinstance(v, str) and any(n in kl for n in _NAME_KEYS):
                out[k] = "Applicant"
                continue
            out[k] = pseudonymize(v, names, kl)
        return out
    if isinstance(obj, list):
        return [pseudonymize(v, names, _key) for v in obj]
    if isinstance(obj, str):
        s = obj
        for n in sorted(names, key=len, reverse=True):
            if len(n) > 2:
                s = re.sub(re.escape(n), "Applicant", s, flags=re.IGNORECASE)
        return _mask(s)
    return obj


def _collect_names(obj, acc: set | None = None) -> set:
    acc = acc if acc is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and any(n in str(k).lower() for n in _NAME_KEYS):
                acc.add(v.strip())
                acc.update(t for t in v.split() if len(t) > 2)
            else:
                _collect_names(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_names(v, acc)
    return acc
