import { useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { agentsApi } from "../../utils/agents.js";
import FraudAssessmentView from "./FraudAssessmentView.jsx";

// Runs the case-level agents on everything the document agents already found:
// Cross-Document Verification, Dedupe, (Fraud Patterns), Fraud Fusion and an
// evidence-backed Policy Check. No re-upload, no LLM on this path.
const LEVEL_BADGE = { ok: "badge-green", warn: "badge-amber", error: "badge-red" };
const POLICY_BADGE = { PASS: "badge-green", PASS_WITH_WARNINGS: "badge-amber", FAIL: "badge-red", NEEDS_INFO: "badge-gray" };

export default function CaseAssessmentCard({ caseId, applicationNo, role, actorId, loanType, formData = {}, initial = null, onResult }) {
  const [res, setRes] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setBusy(true); setError(null);
    const body = {
      loan_type: loanType,
      monthly_income: formData.monthlyIncome ? Number(formData.monthlyIncome) : undefined,
      existing_emi: formData.existingEmi ? Number(formData.existingEmi) : undefined,
    };
    try {
      const r = applicationNo ? await agentsApi.appAssessment(applicationNo, body, { role, actorId }) : await agentsApi.assess(caseId, body);
      setRes(r);
      onResult?.(r);
    } catch (e) {
      setError(e.status === 404 ? "Upload and read at least one document first." : e.message);
    } finally {
      setBusy(false);
    }
  };

  const policy = res?.policy;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Icon d={Icons.fraud} size={14} /> Fraud &amp; Policy Assessment
          </div>
          <div className="card-sub">Cross-document checks · duplicate identities · fused fraud score · RBI policy on real evidence</div>
        </div>
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={run}>
          <Icon d={Icons.zap} size={12} /> {busy ? "Assessing…" : res ? "Re-run assessment" : "Run assessment"}
        </button>
      </div>
      {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 10 }}>{error}</div>}
      {res && (
        <div style={{ marginTop: 14 }}>
          {res.route === "refer" && (
            <div style={{ fontSize: 12, padding: "8px 10px", borderRadius: 6, background: "var(--amber-tint)", color: "var(--text1)", marginBottom: 12 }}>
              Recommended: refer for a closer human review (fraud risk HIGH or a mandatory rule failed). Nothing has been rejected.
            </div>
          )}
          <FraudAssessmentView fraud={res.fraud} />

          <div className="grid2" style={{ marginTop: 14 }}>
            <div>
              <div className="section-head" style={{ fontSize: 12 }}>Cross-document checks</div>
              {(res.cross_checks || []).length === 0 && <div style={{ fontSize: 12, color: "var(--text3)" }}>Fewer than two documents to compare.</div>}
              {(res.cross_checks || []).map((c, i) => (
                <div key={i} style={{ fontSize: 12, padding: "5px 0", borderBottom: "1px solid var(--border)" }}>
                  <span className={`badge ${LEVEL_BADGE[c.level]}`} style={{ marginRight: 6 }}>{c.level}</span>{c.title}
                  {c.detail && <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 2 }}>{c.detail}</div>}
                </div>
              ))}
              <div className="section-head" style={{ fontSize: 12, marginTop: 12 }}>Duplicate identities</div>
              {res.dedupe?.hits?.length ? res.dedupe.hits.map((h, i) => (
                <div key={i} style={{ fontSize: 12, padding: "4px 0" }}>
                  <span className={`badge ${h.score >= 0.95 ? "badge-red" : "badge-amber"}`}>{h.kind.replaceAll("_", " ")}</span>{" "}
                  <span style={{ fontSize: 11, color: "var(--text3)" }}>{h.keys.join(", ") || "face"}{h.face_similarity != null ? ` · face ${h.face_similarity}` : ""}</span>
                </div>
              )) : <div style={{ fontSize: 12, color: "var(--text3)" }}>No matches in other applications ({(res.dedupe?.keys_checked || []).join(", ")}).</div>}
            </div>
            <div>
              <div className="section-head" style={{ fontSize: 12 }}>RBI / policy check</div>
              {policy && (
                <>
                  <div style={{ marginBottom: 6 }}>
                    <span className={`badge ${POLICY_BADGE[policy.overall_status] || "badge-gray"}`}>{policy.overall_status}</span>
                    {policy.summary && (
                      <span style={{ fontSize: 11, color: "var(--text3)", marginLeft: 6 }}>
                        {policy.summary.passed} pass · {policy.summary.failed} fail · {policy.summary.inconclusive} need info
                      </span>
                    )}
                  </div>
                  {(policy.checks || []).filter((c) => c.status !== "PASS" && c.status !== "NOT_APPLICABLE").slice(0, 8).map((c) => (
                    <div key={c.rule_id} style={{ fontSize: 11.5, padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                      <span className={`badge ${c.status === "FAIL" ? "badge-red" : c.status === "WARNING" ? "badge-amber" : "badge-gray"}`} style={{ fontSize: 9, marginRight: 6 }}>{c.status}</span>
                      {c.rule_name}
                      {c.evidence && <div style={{ fontSize: 10.5, color: "var(--text3)" }}>{c.evidence}</div>}
                    </div>
                  ))}
                  <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 6 }}>
                    Built from real evidence: fields nobody verified stay out, so they show as "need info" instead of a false pass.
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
