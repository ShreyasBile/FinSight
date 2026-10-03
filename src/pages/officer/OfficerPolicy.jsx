import { useState, useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import RegulatoryChat from "../../components/RegulatoryChat.jsx";
import ComplianceCheckPanel from "../../components/ComplianceCheckPanel.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, formatDate, formatINR } from "../../utils/api.js";

const PRIORITY_BADGE = { urgent: "badge-red", high: "badge-red", medium: "badge-amber", low: "badge-gray" };
const STATUS_BADGE = { open: "badge-amber", in_progress: "badge-blue", resolved: "badge-green", closed: "badge-gray" };
const STATUS_LABEL = { open: "Open", in_progress: "In Progress", resolved: "Resolved", closed: "Closed" };

/**
 * Builds the payload the RBI compliance engine evaluates, from the real
 * application row. This replaces a hardcoded per-application checklist that
 * listed made-up rule outcomes — the results below are now produced by the
 * actual rules engine against actual values.
 *
 * Consent and the Key Facts Statement are NOT sent from here: the server reads
 * the consent recorded at submission and, from the offer stage on, the KFS built
 * with the offer (api/app/agents/tools/kfs.py). Before an offer exists the
 * loan-terms rules are "not applicable" rather than "needs info".
 */
function policyExtra(app) {
  return {
    loan_type: app.loan_product,
    credit_score: app.credit_score,
    loan_terms: { loan_amount: app.loan_amount, tenure_months: app.tenure_months, interest_rate: app.interest_rate },
  };
}

export default function OfficerPolicy({ session }) {
  const [showForm, setShowForm] = useState(false);
  const [selectedApp, setSelectedApp] = useState("");
  const officerId = session?.user_id;

  const { data: appData } = useApi(
    () => api.applications({ officer_id: officerId }), [officerId], { skip: !officerId }
  );
  const { data: ticketData, loading, error, reload } = useApi(
    () => api.policyTickets(officerId), [officerId], { skip: !officerId }
  );

  const apps = appData?.applications ?? [];
  const tickets = ticketData?.tickets ?? [];
  const openCount = tickets.filter(t => t.status === "open").length;

  const chosen = useMemo(
    () => apps.find(a => a.application_no === selectedApp) || null,
    [apps, selectedApp]
  );

  return (
    <div>
      <PageHeader title="Policy Engine" sub="RAG-powered policy queries · Read-only access">
        <button className="btn btn-primary" onClick={() => setShowForm(true)}>
          <Icon d={Icons.alert} size={13} />Raise Policy Ticket
        </button>
      </PageHeader>

      <div style={{ padding: "8px 14px", background: "var(--blue-tint)", borderRadius: 8,
                    border: "1px solid var(--blue-tint-strong)", marginBottom: 16,
                    fontSize: 12, color: "var(--text2)" }}>
        <Icon d={Icons.info} size={15} style={{ color: "var(--blue)", flexShrink: 0 }} /> You can
        query policies but cannot modify rule configurations. All policy changes must be approved
        by your Branch Manager.
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--amber-tint-strong)" }}>
          <div className="card-title" style={{ color: "var(--amber)", marginBottom: 14 }}>
            <Icon d={Icons.scrollText} size={16} style={{ color: "var(--amber)" }} /> New Policy Issue Ticket
          </div>
          <div className="form-row">
            <div className="form-group"><label className="form-label">Linked Application</label>
              <select className="form-input">
                <option value="">— Select —</option>
                {apps.map(a => (
                  <option key={a.application_no}>{a.application_no} — {a.customer_name}</option>
                ))}
              </select>
            </div>
            <div className="form-group"><label className="form-label">Policy Area</label>
              <select className="form-input">
                <option>KYC / Document Policy</option><option>Fraud Policy</option>
                <option>Credit Risk</option><option>Fair Practices</option><option>Other</option>
              </select>
            </div>
          </div>
          <div className="form-group"><label className="form-label">Issue Description</label>
            <textarea className="form-input" rows={4}
              placeholder="Describe the policy ambiguity or conflict you encountered while processing an application. Include the specific policy section if known..." />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary">Submit Ticket</button>
            <button className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="grid2" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>AI Policy Query</div>
          <RegulatoryChat height={520} />
        </div>

        <div>
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-title" style={{ marginBottom: 4 }}>Policy Compliance Check</div>
            <div className="card-sub" style={{ marginBottom: 12 }}>
              Runs the selected application against every active RBI rule
            </div>
            <div className="form-group">
              <label className="form-label">Select Application</label>
              <select className="form-input" value={selectedApp}
                      onChange={e => setSelectedApp(e.target.value)}>
                <option value="">— Choose an application —</option>
                {apps.map(a => (
                  <option key={a.application_no} value={a.application_no}>
                    {a.application_no} — {a.customer_name} ({formatINR(a.loan_amount)})
                  </option>
                ))}
              </select>
            </div>
            {chosen && (
              // key forces a fresh run whenever the selection changes; the panel
              // evaluates its payload once on mount.
              <ComplianceCheckPanel key={chosen.application_no}
                                    applicationNo={chosen.application_no} extra={policyExtra(chosen)} />
            )}
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-title" style={{ marginBottom: 14 }}>
              My Tickets
              <span className="badge badge-amber" style={{ marginLeft: 6 }}>{openCount} Open</span>
            </div>
            <DataState
              loading={loading} error={error} onRetry={reload}
              empty={tickets.length === 0}
              label="policy tickets"
              emptyIcon={Icons.scrollText}
              emptyTitle="No tickets raised"
              emptySub="Use 'Raise Policy Ticket' to flag any policy issues"
            >
              <div>
                {tickets.map(t => (
                  <div key={t.ticket_no} style={{ padding: "12px 0", borderBottom: "1px solid var(--border)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between",
                                  alignItems: "center", marginBottom: 6 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--blue)" }}>
                          {t.ticket_no}
                        </span>
                        <span className={`badge ${PRIORITY_BADGE[t.priority] || "badge-gray"}`}
                              style={{ fontSize: 9, textTransform: "capitalize" }}>{t.priority}</span>
                      </div>
                      <span className={`badge ${STATUS_BADGE[t.status] || "badge-gray"}`}>
                        {STATUS_LABEL[t.status] || t.status}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.5, marginBottom: 4 }}>
                      {t.issue}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                      {formatDate(t.created_at)}{t.policy_ref ? ` · ${t.policy_ref}` : ""}
                      {t.application_no ? ` · ${t.application_no}` : ""}
                    </div>
                    {t.status === "resolved" && t.resolution && (
                      <div style={{ marginTop: 6, fontSize: 11, color: "var(--green)",
                                    display: "flex", alignItems: "flex-start", gap: 4 }}>
                        <Icon d={Icons.check} size={12} style={{ color: "var(--green)", flexShrink: 0, marginTop: 2 }} />
                        <span>{t.resolution}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </DataState>
          </div>
        </div>
      </div>
    </div>
  );
}
