import { useEffect, useState } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import ComplianceCheckPanel from "../components/ComplianceCheckPanel.jsx";
import DataState from "../components/DataState.jsx";
import { api, useApi, formatINR, formatDate } from "../utils/api.js";
import { agentsApi } from "../utils/agents.js";
import FraudTriagePanel from "../components/agents/FraudTriagePanel.jsx";
import FraudAssessmentView from "../components/agents/FraudAssessmentView.jsx";

// Head-only: what the agents found about this applicant across applications
// (duplicate identities, face/liveness, ring proposals). Evidence, not verdicts.
function RiskSignals({ applicationNo }) {
  const [res, setRes] = useState(undefined);
  useEffect(() => {
    let alive = true;
    agentsApi.appResults(applicationNo).then(r => alive && setRes(r)).catch(() => alive && setRes(null));
    return () => { alive = false; };
  }, [applicationNo]);
  const a = res?.assessment;
  return (
    <div>
      {res === null && (
        <div className="card" style={{ marginBottom: 16, fontSize: 12, color: "var(--text3)" }}>
          The agents have not assessed this application yet — run the fraud assessment from the application's Fraud Report tab.
        </div>
      )}
      {a?.fraud && <div className="card" style={{ marginBottom: 16 }}><FraudAssessmentView fraud={a.fraud} /></div>}
      {a?.dedupe && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title" style={{ marginBottom: 8 }}>Duplicate identities across applications</div>
          {a.dedupe.hits?.length ? a.dedupe.hits.map((h, i) => (
            <div key={i} style={{ fontSize: 12, padding: "4px 0" }}>
              <span className={`badge ${h.score >= 0.95 ? "badge-red" : "badge-amber"}`}>{h.kind.replaceAll("_", " ")}</span>{" "}
              <span style={{ color: "var(--text3)" }}>{(h.keys || []).join(", ") || "face"}{h.face_similarity != null ? ` · face ${h.face_similarity}` : ""}</span>
            </div>
          )) : <div style={{ fontSize: 12, color: "var(--text2)" }}>No other application shares this applicant's PAN, Aadhaar, account, file or face.</div>}
        </div>
      )}
      {(res?.face || res?.liveness) && (
        <div className="card" style={{ marginBottom: 16, fontSize: 12, color: "var(--text2)" }}>
          <div className="card-title" style={{ marginBottom: 8 }}>Face &amp; liveness</div>
          {res.face && <div>Face match across documents: <b>{res.face.overall}</b> — {res.face.summary}</div>}
          {res.liveness && <div style={{ marginTop: 4 }}>Selfie liveness: <b>{String(res.liveness.verdict).replaceAll("_", " ")}</b>
            {res.liveness.live_score != null ? ` (live score ${res.liveness.live_score})` : ""}</div>}
        </div>
      )}
      <FraudTriagePanel filterApplication={applicationNo} />
    </div>
  );
}

function maskAadhaar(a) {
  if (!a) return "—";
  const s = String(a).replace(/\s/g, "");
  return s.length >= 4 ? `XXXX-XXXX-${s.slice(-4)}` : "XXXX";
}

function policyExtra(app, c) {
  return {
    loan_type: app.loan_product,
    monthly_income: c?.annual_income ? Math.round(c.annual_income / 12) : undefined,
    credit_score: c?.credit_score,
    loan_terms: { loan_amount: app.loan_amount, tenure_months: app.tenure_months, interest_rate: app.interest_rate },
  };
}

export default function PageCustomer360({ role, setPage, applicationNo, session }) {
  const officerId = role === "officer" ? session?.user_id : undefined;

  // With no dedicated customer picker, the page centres on an application:
  // the one the officer opened, else the most recent in their queue (all
  // applications for the head). Everything shown is that customer's real data.
  const { data: list } = useApi(
    () => api.applications(officerId ? { officer_id: officerId } : {}),
    [officerId]
  );
  const appNo = applicationNo || list?.applications?.[0]?.application_no;

  const { data: app, loading, error, reload } = useApi(
    () => api.application(appNo), [appNo], { skip: !appNo }
  );
  const { data: history } = useApi(
    () => api.applications({ customer_id: app?.customer_id }),
    [app?.customer_id], { skip: !app?.customer_id }
  );

  return (
    <DataState loading={loading} error={error} onRetry={reload} empty={!app}
               label="customer profile"
               emptyTitle="No customer to show"
               emptySub="Open an application first — the 360° view centres on that applicant.">
      {app && <Profile app={app} customer={app.customer || {}} history={history?.applications || []}
                       role={role} setPage={setPage} />}
    </DataState>
  );
}

function Profile({ app, customer: c, history, role, setPage }) {
  const [tab, setTab] = useState("Profile");
  const tabs = role === "head"
    ? ["Profile", "Financials", "Documents", "Loan History", "RBI Compliance", "Risk Signals"]
    : ["Profile", "Financials", "Documents", "Loan History", "RBI Compliance"];

  const initials = `${(c.first_name || "?")[0]}${(c.last_name || "")[0] || ""}`.toUpperCase();
  const active = history.filter(h => !h.is_terminal).length;
  const closed = history.filter(h => h.is_terminal).length;
  const monthly = c.annual_income ? Math.round(c.annual_income / 12) : null;
  const docs = app.documents || [];

  const notAvailable = (note) => (
    <div className="card" style={{ textAlign: "center", padding: "32px 24px" }}>
      <Icon d={Icons.cpu} size={28} style={{ color: "var(--text3)", marginBottom: 10 }} />
      <div style={{ fontSize: 12, color: "var(--text3)", lineHeight: 1.6, maxWidth: 440, margin: "0 auto" }}>{note}</div>
    </div>
  );

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Icon d={Icons.chevronR} size={14} style={{ color: "var(--text3)", cursor: "pointer", transform: "rotate(180deg)" }}
              onClick={() => setPage && setPage("applications")} />
        <span style={{ fontSize: 12, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>Applications</span>
        <Icon d={Icons.chevronR} size={12} style={{ color: "var(--text3)" }} />
        <span style={{ fontSize: 12, color: "var(--blue)", fontFamily: "var(--font-mono)" }}>{app.application_no}</span>
      </div>

      <div className="grid2" style={{ marginBottom: 16 }}>
        <div className="card" style={{ display: "flex", gap: 16 }}>
          <div style={{ width: 60, height: 60, borderRadius: 16, background: "linear-gradient(135deg,var(--blue),var(--purple))",
                        display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                        fontSize: 22, fontWeight: 800, color: "white" }}>{initials}</div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text1)", marginBottom: 4 }}>{app.customer_name}</div>
            <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 8 }}>
              {c.first_application_at ? `Customer since ${formatDate(c.first_application_at)}` : "New customer"}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <span className="badge badge-green">Active Customer</span>
              {c.occupation && <span className="badge badge-blue">{c.occupation}</span>}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>Quick Stats</div>
          <div className="grid4" style={{ gap: 10 }}>
            {[["Total Loans", history.length], ["Active", active], ["Closed", closed], ["CIBIL", c.credit_score ?? "—"]].map(([k, v]) => (
              <div key={k} style={{ textAlign: "center" }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: "var(--blue)" }}>{v}</div>
                <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>{k}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="tabs" style={{ marginBottom: 16 }}>
        {tabs.map(t => <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</div>)}
      </div>

      {tab === "Profile" && (
        <div className="grid3">
          <div className="card"><div className="card-title" style={{ marginBottom: 12 }}>Personal Info</div>
            {[["DOB", formatDate(c.date_of_birth)], ["PAN", c.pan || "—"], ["Aadhaar", maskAadhaar(c.aadhaar)],
              ["Phone", c.mobile || "—"], ["Email", c.email || "—"]].map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 8, padding: "5px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 11, color: "var(--text3)", width: 80, flexShrink: 0 }}>{k}</span>
                <span style={{ fontSize: 12, color: "var(--text1)" }}>{v}</span>
              </div>
            ))}
          </div>
          <div className="card"><div className="card-title" style={{ marginBottom: 12 }}>Employment & Income</div>
            {[["Occupation", c.occupation || "—"], ["Annual Income", formatINR(c.annual_income)],
              ["Est. Monthly", formatINR(monthly)], ["Prior Applications", c.application_count ?? "—"]].map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 8, padding: "5px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 11, color: "var(--text3)", width: 120, flexShrink: 0 }}>{k}</span>
                <span style={{ fontSize: 12, color: "var(--text1)" }}>{v}</span>
              </div>
            ))}
          </div>
          <div className="card"><div className="card-title" style={{ marginBottom: 12 }}>Address</div>
            {[["Line 1", c.address_line1 || "—"], ["Line 2", c.address_line2 || "—"], ["City", c.city || "—"],
              ["State", c.state || "—"], ["PIN", c.pincode || "—"]].map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 8, padding: "5px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 11, color: "var(--text3)", width: 60, flexShrink: 0 }}>{k}</span>
                <span style={{ fontSize: 12, color: "var(--text1)" }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "Financials" && (
        <div>
          <div className="grid3" style={{ marginBottom: 16 }}>
            <div className="stat-card">
              <div className="stat-label">CIBIL Score</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: c.credit_score >= 700 ? "var(--green)" : "var(--amber)" }}>
                {c.credit_score ?? "—"}
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Annual Income</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: "var(--blue)" }}>{formatINR(c.annual_income)}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Total Loans</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: "var(--blue)" }}>{history.length}</div>
            </div>
          </div>
          {notAvailable("Detailed banking-behaviour analytics (balances, bounce counts, cash-deposit ratio) come from the bank-statement analysis pipeline, which is not persisted in this build. The figures above are the customer's stored financial profile.")}
        </div>
      )}

      {tab === "Documents" && (
        docs.length === 0
          ? notAvailable(`No documents recorded for ${app.application_no}.`)
          : (
            <div className="card">
              <div className="card-title" style={{ marginBottom: 12 }}>
                Documents · {app.application_no}
              </div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Document</th><th>Uploaded</th><th>Status</th><th>Tamper</th></tr></thead>
                  <tbody>
                    {docs.map(d => (
                      <tr key={d.document_id}>
                        <td style={{ fontSize: 12 }}>{d.doc_label}</td>
                        <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                          {formatDate(d.uploaded_at)}
                        </td>
                        <td><span className={`badge ${d.verification_status === "verified" ? "badge-green" : "badge-amber"}`}>
                          {d.verification_status}</span></td>
                        <td style={{ fontSize: 11, color: d.tampering_risk_tier && d.tampering_risk_tier !== "LOW" ? "var(--amber)" : "var(--text3)" }}>
                          {d.tampering_risk_tier || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
      )}

      {tab === "Loan History" && (
        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>All Applications</div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Application</th><th>Type</th><th>Amount</th><th>Applied</th><th>Status</th></tr></thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.application_no}>
                    <td style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--blue)" }}>{h.application_no}</td>
                    <td style={{ fontSize: 12 }}>{h.product_name}</td>
                    <td style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>{formatINR(h.loan_amount)}</td>
                    <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{formatDate(h.applied_at)}</td>
                    <td><StatusBadge status={h.status_label} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "RBI Compliance" && (
        <ComplianceCheckPanel key={app.application_no} applicationNo={app.application_no} extra={policyExtra(app, c)} />
      )}

      {role === "head" && tab === "Risk Signals" && <RiskSignals applicationNo={app.application_no} />}
    </div>
  );
}
