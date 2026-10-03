"""Which agents call an LLM, on which lane, with what data and needs.

lane        vision = the call carries a document image; text = text only
data_class  pii_image | pii_text | public — decides what the privacy guard
            requires before a HOSTED provider may be used
needs       capabilities the chosen model must have (validated on PUT /settings/llm)
priority    LLMGate queue priority (lower = served first) — interactive
            upload-path work outranks background narration
max_wait_s  longest this agent will queue behind other LLM calls before
            degrading to its deterministic fallback
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMUse:
    agent_id: str
    label: str
    lane: str
    data_class: str
    needs: tuple = field(default_factory=tuple)
    priority: int = 3
    max_wait_s: float = 120.0
    purpose: str = ""


LLM_AGENTS: dict[str, LLMUse] = {u.agent_id: u for u in [
    LLMUse("extraction", "Extraction", "vision", "pii_image", ("vision", "json"), 0, 180,
           "reads every field of an uploaded document"),
    LLMUse("authenticity", "Authenticity", "vision", "pii_image", ("vision", "json"), 1, 120,
           "judges whether an income document looks fabricated (capped, grounded)"),
    LLMUse("tamper_explainer", "Tamper Explainer", "vision", "pii_image", ("json",), 2, 60,
           "restates tampering findings in plain language (translation only)"),
    LLMUse("regulatory_qa", "Regulatory Q&A", "text", "public", ("json",), 1, 90,
           "writes a cited answer from retrieved RBI passages"),
    LLMUse("rule_authoring", "Rule Authoring", "text", "public", ("json",), 3, 300,
           "drafts rule candidates from regulatory text (human approves)"),
    LLMUse("credit_memo", "Credit Memo", "text", "pii_text", ("json",), 4, 300,
           "writes the credit memo from the evidence bundle"),
    LLMUse("underwriting_supervisor", "Underwriting Supervisor", "text", "pii_text", ("json",), 4, 300,
           "explains the recommendation; may only tighten the rule baseline"),
    LLMUse("income_analysis", "Income Analysis", "text", "pii_text", ("json",), 4, 300,
           "optional narration of income triangulation"),
    LLMUse("fraud_patterns", "Fraud Patterns", "text", "pii_text", ("json",), 4, 300,
           "optional narration of statement pattern flags"),
    LLMUse("fraud_triage", "Fraud Triage", "text", "pii_text", ("json",), 4, 300,
           "optional narration of case/ring proposals"),
    LLMUse("process_mining_analyst", "Process-Mining Analyst", "text", "public", ("json",), 4, 300,
           "narrates conformance and bottleneck findings"),
]}

HOSTED_PROVIDERS = {"anthropic"}
PROVIDERS = ("ollama", "anthropic", "mock")
