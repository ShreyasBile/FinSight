import PageHeader from "../../components/PageHeader.jsx";
import ProgressBar from "../../components/ProgressBar.jsx";
import RiskBadge from "../../components/RiskBadge.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, formatINR, formatWhen } from "../../utils/api.js";
import { agentsApi } from "../../utils/agents.js";

// Everything on this page is read from the database (api.overview for the counts,
// api.applications for the queues) or from the running services (system health).
// With no applications the numbers are 0 and the lists show an empty state.
const HEAD_LIMIT = 500000;
const SEVERITY_BADGE = { critical: "badge-red", high: "badge-red", medium: "badge-amber", low: "badge-blue" };

const Empty = ({ children }) => (
  <div style={{ fontSize: 12, color: "var(--text3)", padding: "14px 0", textAlign: "center" }}>{children}</div>
);
const Row = ({ label, children }) => (
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12 }}>
    <span style={{ color: "var(--text3)" }}>{label}</span>{children}
  </div>
);
const Status = ({ ok, on = "Running", off = "Not reachable" }) => (
  <span className={`badge ${ok ? "badge-green" : "badge-red"}`} style={{ fontSize: 10 }}>{ok ? on : off}</span>
);

export default function HeadDashboard({ setPage, session, openApp }) {
  const ov = useApi(() => api.overview(), []);
  const apps = useApi(() => api.applications({ include_closed: false }), []);
  const cases = useApi(() => api.fraudCases(), []);
  const tickets = useApi(() => api.policyTickets(), []);
  const agents = useApi(() => agentsApi.health(), []);
  const db = useApi(() => api.health(), []);

  const open = (no) => { openApp?.(no); setPage("app-detail"); };

  const t = ov.data?.totals;
  const list = apps.data?.applications ?? [];
  // First what is actually waiting for the Head (referred by an officer, or a large loan to
  // sanction), then open cases worth a look (high risk or above the officer's limit).
  const needsHead = a => a.status_code === "underwritingReview" ||
    (a.status_code === "accepted" && a.loan_amount > HEAD_LIMIT);
  const awaiting = list.filter(needsHead);
  const watch = list
    .filter(a => !needsHead(a) && ((a.risk_score ?? 0) >= 60 || a.loan_amount > HEAD_LIMIT))
    .sort((a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0) || (b.loan_amount ?? 0) - (a.loan_amount ?? 0));
  const highRiskQueue = [...awaiting, ...watch].slice(0, 6);
  const escalated = list.map(a => {
    const why = [];
    if ((a.risk_score ?? 0) >= 70) why.push(`Risk score ${a.risk_score}`);
    if (["high", "critical"].includes(a.fraud_level)) why.push(`Fraud alert${a.fraud_category ? ` — ${a.fraud_category}` : ""}`);
    if (a.sla_minutes_left !== null && a.sla_minutes_left < 0) why.push("SLA breached");
    return { a, why };
  }).filter(x => x.why.length);
  const ticketList = tickets.data?.tickets ?? [];
  const caseList = cases.data?.cases ?? [];
  const stages = ov.data?.by_status ?? [];
  const maxStage = Math.max(1, ...stages.map(s => s.n));
  const officers = ov.data?.officers ?? [];

  return (
    <div>
      <PageHeader title="Dashboard" sub={`Full platform overview${session ? ` · Head Manager: ${session.full_name}` : ""}`}>
        <span className="badge badge-blue" style={{ padding: "6px 12px", fontSize: 12 }}>Head Manager</span>
      </PageHeader>

      <DataState loading={ov.loading} error={ov.error} onRetry={ov.reload} label="the dashboard">
        <div className="stats-grid">
          {[
            { label: "Applications", val: t?.applications, sub: `${t?.active ?? 0} active · ${t?.new_applications ?? 0} new`, color: "var(--blue)" },
            { label: "Requested Amount", val: formatINR(t?.requested_amount ?? 0), sub: `${formatINR(t?.approved_amount ?? 0)} approved`, color: "var(--blue)" },
            { label: "Average Risk Score", val: t?.avg_risk == null ? "—" : Math.round(t.avg_risk),
              sub: `${t?.high_risk ?? 0} high risk · ${(t?.applications ?? 0) - (t?.scored ?? 0)} not scored yet`, color: "var(--amber)" },
            { label: "Fraud Cases", val: ov.data?.fraud.cases, sub: `${t?.sla_breached ?? 0} past SLA · ${ov.data?.documents.tamper_flagged ?? 0} documents flagged`, color: "var(--red)" },
          ].map(s => (
            <div key={s.label} className="stat-card">
              <div className="stat-label">{s.label}</div>
              <div className="stat-value" style={{ color: s.color, fontSize: 24 }}>{s.val ?? "—"}</div>
              <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>{s.sub}</div>
            </div>
          ))}
        </div>

        <div className="grid4" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title" style={{ marginBottom: 12 }}>System Health</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              <Row label="API"><Status ok={!!agents.data} /></Row>
              <Row label="Database"><Status ok={!!db.data?.ok} /></Row>
              <Row label="Local model server"><Status ok={!!agents.data?.ollama_reachable} /></Row>
              <Row label="Agents ready">
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text1)" }}>
                  {agents.data ? `${agents.data.implemented} / ${agents.data.registered}` : "—"}
                </span>
              </Row>
              <Row label="Event log (SQL)"><Status ok={!!agents.data?.sql_sink?.enabled && !agents.data?.sql_sink?.breaker_open} on="Writing" off="Off" /></Row>
              <Row label="Documents stored">
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text1)" }}>{ov.data?.documents.documents ?? 0}</span>
              </Row>
            </div>
            <button className="btn btn-ghost" style={{ fontSize: 11, marginTop: 12 }} onClick={() => setPage("agents")}>Open Agent Activity</button>
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 12 }}>Applications by Stage</div>
            {stages.length === 0 ? <Empty>No applications yet</Empty> : stages.map(s => (
              <div key={s.status_code} style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
                  <span style={{ color: "var(--text2)" }}>{s.label}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text1)" }}>{s.n}</span>
                </div>
                <ProgressBar value={s.n} max={maxStage} color="var(--blue)" />
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 12 }}>Fraud Cases</div>
            {caseList.length === 0 ? <Empty>No fraud cases</Empty> : caseList.slice(0, 4).map(c => (
              <div key={c.case_id} style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: "1px solid var(--border)", alignItems: "center" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 2 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--red)", fontWeight: 600 }}>{c.application_no}</span>
                    <span className={`badge ${SEVERITY_BADGE[c.severity] || "badge-gray"}`} style={{ fontSize: 9 }}>{c.severity}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text2)" }}>{c.fraud_category || c.description}</div>
                </div>
                <button className="btn btn-ghost" style={{ fontSize: 10, padding: "4px 10px", flexShrink: 0 }} onClick={() => open(c.application_no)}>Open</button>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 12 }}>Officer Workload</div>
            {officers.length === 0 ? <Empty>No officers</Empty> : officers.map(o => (
              <div key={o.user_id} style={{ display: "flex", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--border)", alignItems: "center" }}>
                <div style={{ width: 28, height: 28, borderRadius: "50%", background: "var(--surface2)", display: "flex", alignItems: "center",
                              justifyContent: "center", fontSize: 11, fontWeight: 600, color: "var(--text1)", flexShrink: 0 }}>
                  {o.full_name.split(" ").map(w => w[0]).join("")}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text1)" }}>{o.full_name}</div>
                  <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                    {o.assigned} assigned · {o.pending} in review{o.sla_breached ? ` · ${o.sla_breached} late` : ""}
                  </div>
                </div>
                <ProgressBar value={o.assigned - o.open_cases} max={Math.max(o.assigned, 1)} color="var(--blue)" />
              </div>
            ))}
          </div>
        </div>

        <div className="grid2" style={{ marginBottom: 16 }}>
          <div className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div className="card-title" style={{ marginBottom: 0 }}>Approval Queue</div>
              {awaiting.length > 0 && <span className="badge badge-amber">{awaiting.length} awaiting you</span>}
            </div>
            {highRiskQueue.length === 0 ? <Empty>Nothing needs your approval right now</Empty> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>App ID</th><th>Applicant</th><th>Amount</th><th>Risk</th><th>Status</th><th>Action</th></tr></thead>
                  <tbody>
                    {highRiskQueue.map(a => (
                      <tr key={a.application_no}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)" }}>{a.application_no}</td>
                        <td className="bold" style={{ fontSize: 12 }}>{a.customer_name}</td>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{formatINR(a.loan_amount)}</td>
                        <td>{a.risk_score === null ? <span style={{ fontSize: 11, color: "var(--text3)" }}>Not scored</span> : <RiskBadge score={a.risk_score} />}</td>
                        <td style={{ fontSize: 11, color: needsHead(a) ? "var(--amber)" : "var(--text3)" }}>
                          {needsHead(a) ? (a.status_code === "accepted" ? "To sanction" : "Referred to you") : a.status_label}
                        </td>
                        <td><button className={`btn ${needsHead(a) ? "btn-primary" : "btn-ghost"}`} style={{ fontSize: 10, padding: "4px 12px" }}
                                    onClick={() => open(a.application_no)}>{needsHead(a) ? "Decide" : "Review"}</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Policy Exception Queue</div>
            {ticketList.length === 0 ? <Empty>No policy tickets raised</Empty> : ticketList.slice(0, 5).map(p => (
              <div key={p.ticket_no} style={{ display: "flex", gap: 10, padding: "10px 0", borderBottom: "1px solid var(--border)", alignItems: "center" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 2 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--amber)" }}>{p.ticket_no}</span>
                    <span className="badge badge-amber" style={{ fontSize: 9 }}>{p.status}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text2)" }}>{p.issue}</div>
                  <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 2 }}>Raised by {p.raised_by_name} · {formatWhen(p.created_at)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card" style={{ borderColor: escalated.length ? "var(--red-tint-strong)" : undefined }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <div className="card-title" style={{ color: escalated.length ? "var(--red)" : undefined, marginBottom: 0 }}>Escalated Applications</div>
            {escalated.length > 0 && <span className="badge badge-red">{escalated.length} Active</span>}
          </div>
          {escalated.length === 0 ? <Empty>No application is escalated — none is high-risk, flagged for fraud or past its SLA</Empty> : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {escalated.map(({ a, why }) => (
                <div key={a.application_no} style={{ display: "flex", gap: 14, padding: "12px 14px", background: "var(--red-tint)", borderRadius: 8,
                                                     border: "1px solid var(--red-tint-strong)", alignItems: "center" }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                      <span style={{ fontSize: 13, color: "var(--text1)", fontWeight: 600 }}>{a.application_no}</span>
                      <span style={{ fontSize: 11, color: "var(--text3)" }}>— {a.customer_name}</span>
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text2)" }}>{why.join(" · ")}</div>
                  </div>
                  <button className="btn btn-ghost" style={{ fontSize: 12, padding: "6px 14px", flexShrink: 0 }} onClick={() => open(a.application_no)}>Review</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </DataState>
    </div>
  );
}
