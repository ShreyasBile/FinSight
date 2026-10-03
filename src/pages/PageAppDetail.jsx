import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import FraudBadge from "../components/FraudBadge.jsx";
import DataState from "../components/DataState.jsx";
import ComplianceCheckPanel from "../components/ComplianceCheckPanel.jsx";
import { api, useApi, formatINR, formatWhen, formatDate, formatSla, FRAUD_LABEL } from "../utils/api.js";
import { agentsApi } from "../utils/agents.js";
import AgentActivityPanel from "../components/agents/AgentActivityPanel.jsx";
import CaseAssessmentCard from "../components/agents/CaseAssessmentCard.jsx";
import { CreditMemoPanel, RiskAnalysisPanel } from "../components/agents/UnderwritingPanels.jsx";
import DecisionPanel from "../components/DecisionPanel.jsx";
import DocumentViewer from "../components/DocumentViewer.jsx";

const DOC_STATUS_BADGE = {
  verified: "badge-green", processing: "badge-blue", pending: "badge-blue",
  rejected: "badge-red", resubmitted: "badge-amber", expired: "badge-red",
};

function maskAadhaar(a) {
  if (!a) return null;
  const s = String(a).replace(/\s/g, "");
  return s.length >= 4 ? `XXXX-XXXX-${s.slice(-4)}` : "XXXX";
}

// Payload the live RBI compliance engine evaluates. Built from the real
// application + customer rows; fields the schema doesn't hold yet are sent as
// documented defaults so it is clear what is measured vs assumed.
// Policy Check agent input: only what the application record actually holds.
// Everything else (KYC, disclosures, tamper/fraud/face scores) is established
// by the agents from evidence — never hardcoded here.
function policyExtra(app) {
  const c = app.customer || {};
  return {
    loan_type: app.loan_product,
    monthly_income: c.annual_income ? Math.round(c.annual_income / 12) : undefined,
    credit_score: c.credit_score,
    loan_terms: { loan_amount: app.loan_amount, tenure_months: app.tenure_months, interest_rate: app.interest_rate },
  };
}

export default function PageAppDetail({ role, setPage, applicationNo, session }) {
  const { data: app, loading, error, reload } = useApi(
    () => api.application(applicationNo), [applicationNo], { skip: !applicationNo }
  );

  if (!applicationNo) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
        <Icon d={Icons.eye} size={38} style={{ color: "var(--text3)", marginBottom: 12 }} />
        <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>
          No application selected
        </div>
        <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 18 }}>
          Open a case from the applications list to see its full detail.
        </div>
        <button className="btn btn-primary" onClick={() => setPage && setPage("applications")}>
          Go to Applications
        </button>
      </div>
    );
  }

  return (
    // a refresh after a decision keeps the page (and the panel's confirmation) on screen;
    // only a different application shows the loading state
    <DataState loading={loading && app?.application_no !== applicationNo} error={error} onRetry={reload} empty={!app}
               label="this application" emptyTitle="Application not found">
      {app && <Detail app={app} role={role} setPage={setPage} session={session} reload={reload} />}
    </DataState>
  );
}

// Agent results for this application (case file), and the on-demand
// Underwriting run (income → risk → supervisor + memo → offer → next step).
function useAgentResults(app, onChange, who) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const appNo = app.application_no;
  const refresh = useCallback(() => agentsApi.appResults(appNo).then(setRes).catch(() => {}), [appNo]);
  useEffect(() => {
    let alive = true;
    agentsApi.appResults(appNo).then(r => alive && setRes(r)).catch(() => alive && setRes(null));
    return () => { alive = false; };
  }, [appNo]);
  const underwrite = useCallback(async () => {
    setBusy(true); setError(null);
    const c = app.customer || {};
    try {
      const r = await agentsApi.appUnderwrite(appNo, {
        loan_amount: app.loan_amount || undefined, tenure_months: app.tenure_months || undefined,
        monthly_income: c.annual_income ? Math.round(c.annual_income / 12) : undefined,
        credit_score: c.credit_score || undefined, status: app.status_code || undefined,
      }, who);
      setRes(prev => ({ ...(prev || {}), case_id: r.case_id, memo: r.memo, offer: r.offer, workflow: r.workflow,
                        underwriting: { income: r.income, risk: r.risk, recommendation: r.recommendation, run_id: r.run_id } }));
      onChange?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [app, appNo, onChange, who]);
  return { res, busy, error, underwrite, refresh };
}

function Detail({ app, role, setPage, session, reload }) {
  const [tab, setTab] = useState("Overview");
  const [viewing, setViewing] = useState(null);
  const tabs = ["Overview", "Documents", "Risk Analysis", "Fraud Report",
                "Policy Check", "Credit Memo", "Agent Activity", "Activity Log"];
  const who = useMemo(() => ({ role, actorId: session?.user_id }), [role, session?.user_id]);
  const agents = useAgentResults(app, reload, who);
  const assessment = agents.res?.assessment;

  const c = app.customer || {};
  const docs = app.documents || [];
  const fraudCases = app.fraud_cases || [];
  const timeline = app.timeline || [];
  const offer = (app.offers || [])[0] || null;
  const sla = formatSla(app.sla_minutes_left);

  const monthlyIncome = c.annual_income ? Math.round(c.annual_income / 12) : null;
  const docVerified = docs.filter(d => d.verification_status === "verified").length;
  const docFlagged = docs.filter(d => d.tampering_risk_tier && d.tampering_risk_tier !== "LOW").length;

  const notAvailable = (title, note) => (
    <div className="card" style={{ textAlign: "center", padding: "36px 24px" }}>
      <Icon d={Icons.cpu} size={30} style={{ color: "var(--text3)", marginBottom: 12 }} />
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 12, color: "var(--text3)", lineHeight: 1.6, maxWidth: 440, margin: "0 auto" }}>
        {note}
      </div>
    </div>
  );

  return (
    <div className="detail-grid">
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <Icon d={Icons.chevronR} size={14} style={{ color: "var(--text3)", cursor: "pointer",
                 transform: "rotate(180deg)" }} onClick={() => setPage && setPage("applications")} />
          <span style={{ fontSize: 12, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>Applications</span>
          <Icon d={Icons.chevronR} size={12} style={{ color: "var(--text3)" }} />
          <span style={{ fontSize: 12, color: "var(--blue)", fontFamily: "var(--font-mono)" }}>{app.application_no}</span>
        </div>

        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
            <div>
              <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text1)", marginBottom: 4 }}>
                {app.customer_name} — {app.product_name}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--blue)", fontFamily: "var(--font-mono)" }}>
                  {app.application_no}
                </span>
                <StatusBadge status={app.status_label} />
                {app.risk_score != null && <RiskBadge score={app.risk_score} />}
                <FraudBadge level={FRAUD_LABEL[app.fraud_level] || "None"} />
                <span className="badge badge-gray" style={{ fontFamily: "var(--font-mono)" }}>
                  {formatINR(app.loan_amount)}
                </span>
              </div>
            </div>
            {role === "officer" && (
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={agents.busy}
                        onClick={() => { setTab("Credit Memo"); agents.underwrite(); }}>
                  <Icon d={Icons.policy} size={12} />{agents.busy ? "Agents working…" : "Generate Credit Memo"}
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="tabs">
          {tabs.map(t => <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</div>)}
        </div>

        {tab === "Overview" && (
          <div>
            <div className="grid3" style={{ marginBottom: 16 }}>
              {[
                { label: "Risk Score", value: app.risk_score != null ? `${app.risk_score} / 100` : "Not scored",
                  color: app.risk_score >= 70 ? "var(--red)" : app.risk_score >= 40 ? "var(--amber)" : "var(--green)",
                  sub: app.risk_level ? `${app.risk_level} risk` : "—" },
                { label: "Fraud Level", value: (FRAUD_LABEL[app.fraud_level] || "None"),
                  color: ["high", "critical"].includes(app.fraud_level) ? "var(--red)" :
                         app.fraud_level === "medium" ? "var(--amber)" :
                         app.fraud_level === "unassessed" ? "var(--text3)" : "var(--green)",
                  sub: app.fraud_category || (app.fraud_level === "unassessed"
                    ? "Run it on the Fraud Report tab" : "No active case") },
                { label: "Credit Score", value: c.credit_score ?? "—", color: "var(--blue)", sub: "CIBIL" },
                { label: "SLA", value: sla.text, color: sla.overdue ? "var(--red)" : "var(--text1)",
                  sub: app.status_label },
                { label: "Documents", value: `${docVerified} / ${docs.length}`, color: "var(--blue)", sub: "verified" },
                { label: "Rework", value: app.rework_count ?? 0, color: app.rework_count ? "var(--amber)" : "var(--green)",
                  sub: `${app.handover_count ?? 0} handovers` },
              ].map(m => (
                <div key={m.label} className="stat-card">
                  <div className="stat-label">{m.label}</div>
                  <div style={{ fontSize: 22, fontWeight: 700, color: m.color, marginBottom: 2 }}>{m.value}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)" }}>{m.sub}</div>
                </div>
              ))}
            </div>
            <div className="grid2">
              <div className="card">
                <div className="card-title" style={{ marginBottom: 14 }}>Applicant Profile</div>
                {[
                  ["Full Name", app.customer_name],
                  ["Date of Birth", formatDate(c.date_of_birth)],
                  ["PAN", c.pan || "—"],
                  ["Aadhaar", maskAadhaar(c.aadhaar) || "—"],
                  ["Occupation", c.occupation || "—"],
                  ["Mobile", c.mobile || "—"],
                  ["Email", c.email || "—"],
                  ["Address", [c.address_line1, c.city, c.state, c.pincode].filter(Boolean).join(", ") || "—"],
                ].map(([k, v]) => (
                  <div key={k} style={{ display: "flex", gap: 16, padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ fontSize: 12, color: "var(--text3)", width: 130, flexShrink: 0 }}>{k}</span>
                    <span style={{ fontSize: 12, color: "var(--text1)" }}>{v}</span>
                  </div>
                ))}
              </div>
              <div className="card">
                <div className="card-title" style={{ marginBottom: 14 }}>Loan & Financials</div>
                {[
                  ["Loan Amount", formatINR(app.loan_amount)],
                  ["Tenure", app.tenure_months ? `${app.tenure_months} months` : "—"],
                  ["Interest Rate", app.interest_rate != null ? `${app.interest_rate}%` : "—"],
                  ["Annual Income", formatINR(c.annual_income)],
                  ["Est. Monthly Income", formatINR(monthlyIncome)],
                  ["Credit Score (CIBIL)", c.credit_score ?? "—"],
                  ["Prior Applications", c.application_count ?? "—"],
                  ["Branch", app.branch_code || "—"],
                ].map(([k, v]) => (
                  <div key={k} style={{ display: "flex", gap: 16, padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ fontSize: 12, color: "var(--text3)", width: 160, flexShrink: 0 }}>{k}</span>
                    <span style={{ fontSize: 12, color: "var(--text1)" }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === "Documents" && (
          docs.length === 0
            ? notAvailable("No documents on file",
                "This application has no uploaded documents recorded in the database yet.")
            : (
              <div className="grid2">
                {docs.map(d => (
                  <div key={d.document_id} className="card card-sm">
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                      <Icon d={Icons.fileText} size={16}
                            style={{ color: d.verification_status === "verified" ? "var(--green)" : "var(--amber)", flexShrink: 0 }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 13, color: "var(--text1)", fontWeight: 500 }}>{d.doc_label}</div>
                        <div style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                          {d.file_name} · {formatDate(d.uploaded_at)}
                        </div>
                      </div>
                      <span className={`badge ${DOC_STATUS_BADGE[d.verification_status] || "badge-gray"}`}>
                        {d.verification_status}
                      </span>
                      <button className="btn btn-ghost" style={{ fontSize: 11, padding: "3px 10px" }}
                              onClick={() => setViewing(d)} title={role === "officer" ? "Opens the masked copy" : "Opens the original"}>
                        <Icon d={Icons.eye} size={11} />View
                      </button>
                    </div>
                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 11, color: "var(--text3)" }}>
                      {d.ocr_confidence != null &&
                        <span>OCR {Math.round(d.ocr_confidence * 100)}%</span>}
                      {d.tampering_risk_tier &&
                        <span style={{ color: d.tampering_risk_tier !== "LOW" ? "var(--amber)" : "var(--text3)" }}>
                          Tamper: {d.tampering_risk_tier}
                        </span>}
                      {d.attempt_no > 1 && <span style={{ color: "var(--amber)" }}>Attempt {d.attempt_no}</span>}
                      {!!d.needs_review && <span style={{ color: "var(--amber)" }}>Needs review</span>}
                    </div>
                  </div>
                ))}
              </div>
            )
        )}

        {tab === "Risk Analysis" && (
          <RiskAnalysisPanel uw={agents.res?.underwriting} busy={agents.busy} error={agents.error}
                             onRun={agents.underwrite} />
        )}

        {tab === "Fraud Report" && role !== "user" && (
          <CaseAssessmentCard key={app.application_no} applicationNo={app.application_no} role={role} actorId={session?.user_id}
                              loanType={app.loan_product} onResult={() => { agents.refresh(); reload?.(); }}
                              initial={assessment ? { ...assessment,
                                policy: assessment.policy_status ? { overall_status: assessment.policy_status } : undefined } : null} />
        )}

        {tab === "Fraud Report" && (
          fraudCases.length === 0
            ? (
              <div className="alert-banner alert-green">
                <Icon d={Icons.fraud} size={14} style={{ color: "var(--green)", flexShrink: 0 }} />
                <span style={{ fontSize: 12, color: "var(--text2)" }}>
                  No fraud case has been raised against this application.
                </span>
              </div>
            )
            : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {fraudCases.map(fc => {
                  let signals = [];
                  try { signals = fc.signals ? JSON.parse(fc.signals) : []; } catch { signals = []; }
                  return (
                    <div key={fc.case_id} className="card">
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text1)" }}>{fc.fraud_category}</span>
                        <span className={`badge ${["high", "critical"].includes(fc.severity) ? "badge-red" :
                                          fc.severity === "medium" ? "badge-amber" : "badge-blue"}`}
                              style={{ textTransform: "capitalize" }}>{fc.severity}</span>
                        <span className="badge badge-gray" style={{ textTransform: "capitalize" }}>{fc.status}</span>
                        {fc.ai_confidence != null &&
                          <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                            {Math.round(fc.ai_confidence * 100)}% confidence · {fc.detection_method}
                          </span>}
                      </div>
                      <p style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.6, marginBottom: signals.length ? 10 : 0 }}>
                        {fc.description}
                      </p>
                      {signals.map((s, i) => (
                        <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 8px",
                                              background: "var(--surface2)", borderRadius: 6, marginBottom: 4 }}>
                          <Icon d={Icons.alert} size={12} style={{ color: "var(--amber)", flexShrink: 0 }} />
                          <span style={{ fontSize: 11, color: "var(--text2)", flex: 1 }}>{s.signal_name}</span>
                          {s.component_code &&
                            <span style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{s.component_code}</span>}
                          {s.signal_score != null &&
                            <span className="badge badge-amber" style={{ fontSize: 9 }}>{Math.round(s.signal_score * 100)}</span>}
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            )
        )}

        {tab === "Policy Check" && (
          <ComplianceCheckPanel key={app.application_no} applicationNo={app.application_no} extra={policyExtra(app)} />
        )}

        {tab === "Credit Memo" && (
          <CreditMemoPanel uw={agents.res?.underwriting} memo={agents.res?.memo} offer={agents.res?.offer}
                           workflow={agents.res?.workflow} busy={agents.busy} error={agents.error}
                           onRun={agents.underwrite} role={role}
                           applicationNo={app.application_no} actorId={session?.user_id} />
        )}

        {tab === "Agent Activity" && (
          agents.res?.case_id
            ? <AgentActivityPanel caseId={agents.res.case_id} role={role} title="Agent activity for this application" />
            : notAvailable("No agent runs yet",
                "Run the fraud assessment or generate the credit memo — every agent step for this application then appears here with its timing, model and evidence.")
        )}

        {tab === "Activity Log" && (
          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Application Timeline</div>
            {timeline.length === 0
              ? <div style={{ fontSize: 12, color: "var(--text3)" }}>No activity recorded yet.</div>
              : (
                <div className="timeline">
                  {timeline.map((tl, i) => (
                    <div key={tl.event_seq} className="tl-item">
                      <div className="tl-dot" style={{ background: "var(--blue-tint-strong)", border: "1px solid var(--blue-tint-strong)" }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: "var(--blue)" }}>{i + 1}</span>
                      </div>
                      <div className="tl-content">
                        <div className="tl-time">
                          {formatWhen(tl.created_at)} · <span style={{ color: "var(--blue)" }}>{tl.to_label}</span>
                          {!!tl.is_rework &&
                            <span className="badge badge-amber" style={{ marginLeft: 6, fontSize: 9 }}>rework</span>}
                        </div>
                        <div className="tl-text">
                          <strong>{tl.actor || (tl.is_automated ? "System" : "—")}</strong>
                          {tl.from_label ? ` — moved from ${tl.from_label}` : " — application created"}
                        </div>
                        {tl.change_reason && tl.change_reason !== "Application created" && (
                          <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 3, fontStyle: "italic" }}>
                            “{tl.change_reason}”
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
          </div>
        )}
      </div>

      <div className="page-aside">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10, fontSize: 12 }}>Case Summary</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {[
              { label: "Applied", value: formatDate(app.applied_at) },
              { label: "SLA", value: sla.text, color: sla.overdue ? "var(--red)" : "var(--green)" },
              { label: "Assignee", value: app.officer_name || "Unassigned" },
              ...(app.head_name ? [{ label: "Head Manager", value: app.head_name }] : []),
              { label: "Last Activity", value: formatWhen(app.last_activity_at) },
              ...(app.wait_label && !app.is_terminal ? [{ label: "Waiting On", value: app.wait_label }] : []),
              { label: "Next Action", value: role === "user" ? app.borrower_next_action : app.officer_next_action },
            ].map(s => (
              <div key={s.label} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 11, color: "var(--text3)" }}>{s.label}</span>
                <span style={{ fontSize: 11, color: s.color || "var(--text1)", fontWeight: 500, textAlign: "right", maxWidth: 140 }}>{s.value}</span>
              </div>
            ))}
          </div>
        </div>

        {(role === "officer" || role === "head") && (
          <DecisionPanel applicationNo={app.application_no} session={session}
                         refreshKey={`${app.status_code}|${app.fraud_level}|${app.risk_score}|${app.current_wait_reason}`}
                         onDone={() => { reload?.(); agents.refresh(); }} />
        )}

        {app.document_request && (
          <div className="card" style={{ borderColor: "var(--amber)" }}>
            <div className="card-title" style={{ marginBottom: 6, fontSize: 12 }}>Waiting for the borrower</div>
            <div style={{ fontSize: 11, color: "var(--text2)", lineHeight: 1.5 }}>
              Documents requested{app.document_request.requested_by ? ` by ${app.document_request.requested_by}` : ""}
              {" "}{formatWhen(app.document_request.requested_at)}: “{app.document_request.remarks}”
            </div>
          </div>
        )}

        {offer && (
          <div className="card">
            <div className="card-title" style={{ marginBottom: 6, fontSize: 12 }}>Current Offer</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--green)", fontFamily: "var(--font-mono)" }}>
              {formatINR(offer.offered_amount)}
            </div>
            <div style={{ fontSize: 11, color: "var(--text3)" }}>
              {offer.interest_rate}% · {offer.tenure_months} months · EMI {formatINR(offer.emi_amount)}
            </div>
            <span className={`badge ${offer.status === "accepted" ? "badge-green" : offer.status === "pending" ? "badge-blue" : "badge-gray"}`}
                  style={{ marginTop: 6, textTransform: "capitalize" }}>{offer.status === "pending" ? "sent · awaiting borrower" : offer.status}</span>
            {offer.approved_by && (
              <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 6 }}>Approved by {offer.approved_by}</div>
            )}
          </div>
        )}

        {(app.disbursements || []).map(d => (
          <div key={d.reference_number} className="card">
            <div className="card-title" style={{ marginBottom: 6, fontSize: 12 }}>Disbursement</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--green)", fontFamily: "var(--font-mono)" }}>
              {formatINR(d.disbursed_amount)}
            </div>
            <div style={{ fontSize: 11, color: "var(--text3)" }}>
              {d.disbursement_method} · ref {d.reference_number} · {formatDate(d.disbursed_at)}
            </div>
          </div>
        ))}
      </div>
      <DocumentViewer doc={viewing} session={session} onClose={() => setViewing(null)} />
    </div>
  );
}
