"""The 29 FinSight agents — one per feature, derived from the nine diagrams in
docs/finsight-architecture.html.

Each AgentSpec is metadata only (what the agent is, what it wraps, how it is
traced). The implementation class registers itself with @implements(id); the
/agents API lists every spec and whether it is implemented.

kind    D = deterministic: the LLM can never influence its output
        H = hybrid: deterministic core + a fenced LLM step
        L = LLM-central with deterministic guards (grounding gate, floors)
process which process-mining case notion its event belongs to
side_effects  declared writes; CI asserts no agent can approve rules, set an
        application status, file a fraud case, or send anything externally
"""
from __future__ import annotations

from dataclasses import dataclass, field

FORBIDDEN_SIDE_EFFECTS = {"approves_rule", "rejects_rule", "sets_application_status",
                          "files_fraud_case", "sends_external", "accepts_offer"}


@dataclass(frozen=True)
class AgentSpec:
    id: str
    number: int
    name: str
    crew: str
    kind: str
    role: str
    goal: str
    wraps: str
    activity_code: str | None
    component_code: str | None
    process: str = "document"
    llm: bool = False
    side_effects: tuple = field(default_factory=tuple)
    phase: int = 2
    surface: str = ""
    version: str = "1.0"
    resource_type: str = "rule_engine"


_SPECS = [
    # ── Intake ───────────────────────────────────────────────────────────────
    AgentSpec("preprocessing", 1, "Preprocessing", "intake", "D",
              "Document image technician", "Flatten, orient and repair each photo so fields can be read",
              "app.preprocess.preprocess_image", "DOC_PREPROCESS", "preprocess",
              surface="POST /extract", resource_type="system"),
    AgentSpec("extraction", 2, "Extraction", "intake", "L",
              "Document reader", "Read every requested field of the document into JSON, inventing nothing",
              "app.providers.*.extract (via LLM gateway)", "DOC_EXTRACT", "ocr_extract",
              llm=True, surface="POST /extract", resource_type="ml_model"),
    AgentSpec("validation", 3, "Validation", "intake", "D",
              "Field auditor", "Check every extracted field's format and checksum",
              "app.guardrails.validate", "DOC_GUARDRAILS", "guardrails", surface="POST /extract"),
    AgentSpec("doc_identity", 4, "Document Identity", "intake", "D",
              "Intake gatekeeper", "Confirm the upload is the document that was asked for",
              "app.docrules.check_document_identity", "DOC_IDENTITY_CHECK", "doc_identity",
              phase=3, surface="POST /extract (identity field)"),
    # ── Privacy ──────────────────────────────────────────────────────────────
    AgentSpec("pii_masking", 5, "PII Masking", "privacy", "D",
              "Privacy officer", "Redact PAN and Aadhaar numbers, keeping the last four characters",
              "app.mask.mask_document", "DOC_MASK", "pii_mask", surface="POST /document/mask",
              resource_type="system"),
    # ── Identity & fraud evidence ────────────────────────────────────────────
    AgentSpec("face_verification", 6, "Face Verification", "fraud_evidence", "D",
              "Identity verifier", "Decide whether the same person appears on every document",
              "app.face.service.verify", "FACE_VERIFY", "face_match", process="loan_application",
              surface="POST /face/verify", resource_type="ml_model"),
    AgentSpec("tampering_forensics", 7, "Tampering Forensics", "fraud_evidence", "D",
              "Document forensics examiner", "Run the nine-signal cascade and fuse it into one honest tier",
              "app.tampering.service.analyze", "DOC_TAMPER_SCAN", "tampering",
              surface="POST /document/integrity", resource_type="ml_model"),
    AgentSpec("authenticity", 8, "Authenticity", "fraud_evidence", "H",
              "Fabrication reviewer", "Spot documents fabricated from scratch, citing only what is printed",
              "app.tampering.pipeline.run_authenticity", "DOC_AUTHENTICITY", "authenticity",
              llm=True, phase=3, surface="inside /document/integrity", resource_type="ai_agent"),
    AgentSpec("tamper_explainer", 9, "Tamper Explainer", "fraud_evidence", "H",
              "Plain-language explainer", "Restate already-found issues simply, never adding or judging",
              "app.tampering.explain.explain", "DOC_TAMPER_EXPLAIN", "tamper_explain",
              llm=True, surface="POST /tampering/explain", resource_type="ai_agent"),
    AgentSpec("tamper_locator", 10, "Tamper Locator", "fraud_evidence", "D",
              "Evidence locator", "Box on the page each figure a finding cites",
              "app.tampering.locate.locate", "DOC_TAMPER_LOCATE", "tamper_locate",
              surface="POST /tampering/locate", resource_type="system"),
    AgentSpec("cross_verification", 11, "Cross-Document Verification", "fraud_evidence", "D",
              "Consistency checker", "Compare names, dates of birth and gender across documents (Indian-aware)",
              "app.docrules.cross_verify_documents", "CROSS_VERIFY", "cross_verify",
              process="loan_application", phase=3, surface="POST /agents/cases/{id}/assess"),
    AgentSpec("dedupe", 12, "Dedupe", "fraud_evidence", "D",
              "Duplicate-identity investigator", "Find the same file, PAN, Aadhaar or face in other applications",
              "app.agents.tools.dedupe", "DEDUPE_CHECK", "dedupe", process="loan_application",
              side_effects=("writes_identity_index",), phase=3, surface="assess"),
    AgentSpec("fraud_fusion", 13, "Fraud Fusion", "fraud_evidence", "D",
              "Fraud analyst", "Fuse every evidence component into one advisory fraud score",
              "app.agents.tools.fraud_fusion", "FRAUD_FUSION", "fraud_fusion", process="loan_application",
              side_effects=("writes_fraud_assessment",), phase=3, surface="assess · Fraud tab"),
    AgentSpec("fraud_patterns", 14, "Fraud Patterns", "fraud_evidence", "H",
              "Statement analyst", "Flag cash cycling, sudden deposits, income mismatch and GST inflation",
              "app.agents.tools.fraud_patterns", "FRAUD_PATTERN_SCAN", "fraud_pattern",
              process="loan_application", llm=True, phase=5, surface="assess · Fraud tab"),
    AgentSpec("fraud_triage", 15, "Fraud Triage", "fraud_evidence", "H",
              "Fraud triage lead", "Propose (never file) fraud cases and ring links for a human",
              "app.agents.tools.fraud_triage", "FRAUD_TRIAGE_AI", "fraud_triage", process="fraud_case",
              llm=True, phase=5, surface="Officer Fraud · AI proposals", resource_type="ai_agent"),
    # ── Compliance ───────────────────────────────────────────────────────────
    AgentSpec("kb_ingestion", 16, "KB Ingestion", "compliance", "D",
              "Regulatory librarian", "Chunk and index regulatory text for retrieval",
              "app.compliance.db.insert_document", "KB_INDEX", "kb_ingest", process="rule_authoring",
              side_effects=("writes_knowledge_base",), phase=5, surface="POST /compliance/kb/documents",
              resource_type="system"),
    AgentSpec("rule_authoring", 17, "Rule Authoring", "compliance", "H",
              "Compliance analyst (maker)", "Draft rule candidates from regulation for a human checker",
              "app.compliance.extraction.extract_candidates", "RULE_EXTRACT", "rule_extract",
              process="rule_authoring", llm=True, side_effects=("writes_candidates",), phase=4,
              surface="POST /compliance/kb/documents/{id}/extract", resource_type="ai_agent"),
    AgentSpec("regulatory_qa", 18, "Regulatory Q&A", "compliance", "L",
              "Regulatory advisor", "Answer from retrieved RBI passages only, with citations",
              "app.compliance.retrieval.BM25Index + LLM synthesis", "REG_QA_ANSWER", "reg_qa",
              process="agent_ops", llm=True, phase=4, surface="POST /compliance/rag/query",
              resource_type="ai_agent"),
    AgentSpec("policy_check", 19, "Policy Check", "compliance", "D",
              "Compliance checker", "Evaluate every active RBI/internal rule against real evidence",
              "app.compliance.rules_engine.evaluate_application", "COMPLIANCE_CHECK", "compliance",
              process="loan_application", phase=3, surface="POST /compliance/policy/check"),
    # ── Underwriting ─────────────────────────────────────────────────────────
    AgentSpec("income_analysis", 20, "Income Analysis", "underwriting", "H",
              "Income analyst", "Triangulate income across payslip, ITR/Form 16, bank credits and GST",
              "app.agents.tools.income", "INCOME_ANALYSIS", "income_analysis", process="loan_application",
              llm=True, phase=4, surface="underwrite · Risk tab"),
    AgentSpec("risk_scoring", 21, "Risk Scoring", "underwriting", "D",
              "Credit risk modeller", "Score nine risk dimensions and grade the application",
              "app.agents.vendor.risk_engine", "RISK_SCORE", "risk_model", process="loan_application",
              phase=4, surface="Risk Analysis tab", resource_type="ml_model"),
    AgentSpec("credit_memo", 22, "Credit Memo", "underwriting", "L",
              "Credit memo writer", "Write the credit memo from evidence, citing only real figures",
              "LLM writer (grounded)", "CREDIT_MEMO_GENERATE", "credit_memo", process="loan_application",
              llm=True, side_effects=("writes_report_draft",), phase=4, surface="Credit Memo tab",
              resource_type="ai_agent"),
    AgentSpec("underwriting_supervisor", 23, "Underwriting Supervisor", "underwriting", "L",
              "Senior underwriter", "Recommend a next step; may tighten but never loosen the rule baseline",
              "rule baseline + LLM (tighten-only)", "UW_RECOMMEND", "uw_supervisor",
              process="loan_application", llm=True, phase=4, surface="Overview / Credit Memo",
              resource_type="ai_agent"),
    AgentSpec("offer_generation", 24, "Offer Generation", "underwriting", "D",
              "Pricing analyst", "Draft a risk-priced offer with the EMI (a human sends it)",
              "app.agents.vendor.risk_engine.pricing_engine.get_rate", "OFFER_GENERATE", "offer_gen",
              process="loan_application", side_effects=("writes_offer_draft",), phase=5, surface="Overview"),
    AgentSpec("workflow_router", 25, "Workflow Router", "underwriting", "D",
              "Workflow coordinator", "Recommend the next lifecycle step for the product",
              "app.agents.tools.workflow", "WORKFLOW_ROUTE", "workflow_router", process="loan_application",
              phase=5, surface="Application Detail · next steps"),
    # ── Operations ───────────────────────────────────────────────────────────
    AgentSpec("notifier", 26, "Notifier", "ops", "D",
              "Communications clerk", "Draft notification rows from templates (nothing is sent externally)",
              "app.agents.tools.notifier", "NOTIFY_SENT", "notifier", process="loan_application",
              side_effects=("writes_notifications",), phase=5, surface="notification pages",
              resource_type="system"),
    AgentSpec("audit_storage", 27, "Audit / Storage", "ops", "D",
              "Records keeper", "Persist the application to the audit store with a masked officer view",
              "app.storage.store_application", "APP_STORE", "storage", process="loan_application",
              side_effects=("writes_storage", "writes_application_record"), surface="POST /application/store",
              resource_type="system"),
    AgentSpec("process_mining_analyst", 28, "Process-Mining Analyst", "ops", "H",
              "Process analyst", "Report conformance, variants and bottlenecks from the event log",
              "app.agents.tools.process_mining", "PM_ANALYZE", "pm_analyst", process="agent_ops",
              llm=True, phase=5, surface="Agent Activity · Process Mining", resource_type="ai_agent"),
    AgentSpec("liveness", 29, "Liveness", "fraud_evidence", "D",
              "Liveness checker", "Confirm the selfie is a live person and matches the ID photo",
              "app.agents.tools.liveness", "LIVENESS_CHECK", "liveness", process="loan_application",
              phase=6, surface="wizard selfie step", resource_type="ml_model"),
]

REGISTRY: dict[str, AgentSpec] = {s.id: s for s in _SPECS}
IMPLEMENTATIONS: dict[str, type] = {}


def implements(agent_id: str):
    """Class decorator: register an implementation for a spec."""
    def deco(cls):
        if agent_id not in REGISTRY:
            raise KeyError(f"no AgentSpec for '{agent_id}'")
        cls.spec = REGISTRY[agent_id]
        IMPLEMENTATIONS[agent_id] = cls
        return cls
    return deco


def get_agent(agent_id: str):
    from . import impl  # noqa: F401 - importing registers every implementation
    cls = IMPLEMENTATIONS.get(agent_id)
    if cls is None:
        raise KeyError(f"agent '{agent_id}' is not implemented")
    return cls()


def is_enabled(agent_id: str) -> bool:
    from ..config import settings
    if not settings.agents_enabled:
        return False
    disabled = {a.strip() for a in settings.agents_disabled.split(",") if a.strip()}
    return agent_id not in disabled
