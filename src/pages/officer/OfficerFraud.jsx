import { useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, formatDate } from "../../utils/api.js";
import FraudTriagePanel from "../../components/agents/FraudTriagePanel.jsx";

const SEVERITY_BADGE = {
  critical: "badge-red", high: "badge-red", medium: "badge-amber", low: "badge-blue",
};
const STATUS_BADGE = {
  open: "badge-amber", investigating: "badge-purple", escalated: "badge-red",
  confirmed: "badge-red", dismissed: "badge-green",
};
const STATUS_LABEL = {
  open: "Open", investigating: "Under Review", escalated: "Escalated",
  confirmed: "Confirmed", dismissed: "Cleared",
};

export default function OfficerFraud({ setPage, session, openApp }) {
  const officerId = session?.user_id;

  // Cases assigned to this officer. The signals under each case are the real
  // rows fraud.fraud_signals holds — the module that produced them, its compute
  // time and model version included — rather than a static list of examples.
  const { data, loading, error, reload } = useApi(
    () => api.fraudCases(officerId), [officerId], { skip: !officerId }
  );

  const cases = data?.cases ?? [];

  const stats = useMemo(() => ({
    total: cases.length,
    high: cases.filter(c => ["high", "critical"].includes(c.severity)).length,
    investigating: cases.filter(c => ["open", "investigating", "escalated"].includes(c.status)).length,
    breached: cases.filter(c => c.sla_breached).length,
  }), [cases]);

  const open = (c) => {
    if (!c.application_no) return;
    openApp?.(c.application_no);
    setPage("app-detail");
  };

  return (
    <div>
      <PageHeader title="Fraud Detection" sub="Cases raised against applications in your queue" />

      <FraudTriagePanel openApp={(no) => { openApp?.(no); setPage("app-detail"); }} />

      <div className="grid4" style={{ gap: 12, marginBottom: 16 }}>
        {[
          { label: "Open Cases",   value: stats.total,         color: "var(--blue)" },
          { label: "High Severity", value: stats.high,         color: "var(--red)" },
          { label: "Under Investigation", value: stats.investigating, color: "var(--amber)" },
          { label: "SLA Breached", value: stats.breached,      color: "var(--red)" },
        ].map(s => (
          <div key={s.label} className="stat-card">
            <div className="stat-label">{s.label}</div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 26, fontWeight: 700, color: s.color }}>
              {s.value}
            </div>
          </div>
        ))}
      </div>

      <DataState
        loading={loading} error={error} onRetry={reload}
        empty={cases.length === 0}
        label="fraud cases"
        emptyIcon={Icons.fraud}
        emptyTitle="No fraud cases on your queue"
        emptySub="Cases appear here when a signal fires on one of your applications."
      >
        <>
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-title" style={{ marginBottom: 14 }}>Cases</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Application</th><th>Applicant</th><th>Category</th><th>Score</th>
                    <th>Severity</th><th>Evidence</th><th>Branch</th><th>Raised</th>
                    <th>Status</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map(c => (
                    <tr key={c.case_id}>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)", fontWeight: 600 }}>
                        {c.application_no || "—"}
                      </td>
                      <td className="bold">{c.customer_name || "—"}</td>
                      <td style={{ fontSize: 12 }}>{c.fraud_category}</td>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: 12,
                                   color: c.score >= 70 ? "var(--red)" : c.score >= 50 ? "var(--amber)" : "var(--text2)",
                                   fontWeight: 600 }}>
                        {c.score}
                      </td>
                      <td>
                        <span className={`badge ${SEVERITY_BADGE[c.severity] || "badge-gray"}`}
                              style={{ fontSize: 10, textTransform: "capitalize" }}>
                          {c.severity}
                        </span>
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text2)", maxWidth: 300 }}>{c.description}</td>
                      <td style={{ fontSize: 11, color: "var(--text3)" }}>{c.branch || "—"}</td>
                      <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                        {formatDate(c.created_at)}
                      </td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[c.status] || "badge-gray"}`} style={{ fontSize: 10 }}>
                          {STATUS_LABEL[c.status] || c.status}
                        </span>
                        {!!c.sla_breached &&
                          <span className="badge badge-red" style={{ fontSize: 9, marginLeft: 4 }}>SLA</span>}
                      </td>
                      <td>
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 8px" }}
                                onClick={() => open(c)} disabled={!c.application_no}>
                          <Icon d={Icons.eye} size={11} /> Open
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Detection signals behind each case, straight from fraud.fraud_signals. */}
          <div className="card">
            <div className="card-title" style={{ marginBottom: 4 }}>Detection Signals</div>
            <div className="card-sub" style={{ marginBottom: 14 }}>
              What each case was raised on, and which module produced it
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {cases.filter(c => c.detection_method).map(c => (
                <div key={c.case_id} style={{
                  border: "1px solid var(--border)", borderRadius: 8, padding: "12px 14px",
                  background: "var(--surface2)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text1)" }}>
                      {c.fraud_category}
                    </span>
                    <span className={`badge ${SEVERITY_BADGE[c.severity] || "badge-gray"}`}
                          style={{ fontSize: 9, textTransform: "capitalize" }}>{c.severity}</span>
                    <span style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                      {c.application_no}
                    </span>
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text3)",
                                   fontFamily: "var(--font-mono)" }}>
                      {c.detection_method}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.5 }}>
                    {c.description}
                  </div>
                  {c.time_to_detect_sec !== null && c.time_to_detect_sec !== undefined && (
                    <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 8,
                                  fontFamily: "var(--font-mono)" }}>
                      detected {Math.round(c.time_to_detect_sec / 60)} min after first signal
                      {c.time_to_resolve_sec != null &&
                        ` · resolved in ${Math.round(c.time_to_resolve_sec / 3600)}h`}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </>
      </DataState>
    </div>
  );
}
