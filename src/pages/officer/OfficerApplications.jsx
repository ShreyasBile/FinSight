import { useState, useEffect, useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import StatusBadge from "../../components/StatusBadge.jsx";
import RiskBadge from "../../components/RiskBadge.jsx";
import FraudBadge from "../../components/FraudBadge.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, formatINR, formatWhen, formatSla, FRAUD_LABEL } from "../../utils/api.js";

// Filters run over rows the database already returned. The counts in the
// header come from a separate COUNT query (api.officerStats) rather than from
// the loaded page, so they stay right regardless of what is filtered on screen.
const FILTERS = ["All", "High Risk", "Pending Review", "SLA Breached", "More Docs Required"];

const MATCHES = {
  All: () => true,
  "High Risk": a => (a.risk_score ?? 0) >= 70,
  "Pending Review": a => ["underReview", "underwritingReview"].includes(a.status_code),
  "SLA Breached": a => a.sla_minutes_left !== null && a.sla_minutes_left < 0,
  "More Docs Required": a => a.status_code === "documentsProcessing",
};

export default function OfficerApplications({ setPage, session, initialSearch = "", openApp }) {
  const [filter, setFilter] = useState("All");
  const [search, setSearch] = useState(initialSearch);
  useEffect(() => { if (initialSearch) setSearch(initialSearch); }, [initialSearch]);

  const officerId = session?.user_id;

  const { data, loading, error, reload } = useApi(
    () => api.applications({ officer_id: officerId, include_closed: false }),
    [officerId],
    { skip: !officerId }
  );
  const { data: stats } = useApi(
    () => api.officerStats(officerId), [officerId], { skip: !officerId }
  );

  const apps = data?.applications ?? [];

  const filtered = useMemo(() => apps.filter(a => {
    if (!MATCHES[filter](a)) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return (a.customer_name || "").toLowerCase().includes(q)
        || (a.application_no || "").toLowerCase().includes(q);
  }), [apps, filter, search]);

  const q = stats?.queue;

  const open = (a) => { openApp?.(a.application_no); setPage("app-detail"); };

  return (
    <div>
      <PageHeader
        title="Underwriting Queue"
        sub={session ? `${q?.total ?? apps.length} cases assigned to ${session.full_name}` : "Loading…"}
      />

      <div className="grid4" style={{ gap: 12, marginBottom: 16 }}>
        {[
          { label: "Open Cases",    value: apps.length,           color: "var(--blue)" },
          { label: "High Risk",     value: q?.high_risk ?? "—",   color: "var(--red)" },
          { label: "Pending Review", value: q?.pending ?? "—",    color: "var(--amber)" },
          { label: "SLA Breach",    value: q?.sla_breached ?? "—", color: "var(--red)" },
        ].map(s => (
          <div key={s.label} className="stat-card">
            <div className="stat-label">{s.label}</div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 26, fontWeight: 700, color: s.color }}>
              {s.value}
            </div>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", gap: 2, background: "var(--surface2)", borderRadius: 8, padding: 3 }}>
            {FILTERS.map(f => (
              <div key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>{f}</div>
            ))}
          </div>
          <div className="search-bar" style={{ marginLeft: "auto", width: 220 }}>
            <Icon d={Icons.search} size={13} style={{ color: "var(--text3)" }} />
            <input placeholder="Search by name or ID..." value={search}
                   onChange={e => setSearch(e.target.value)} />
          </div>
        </div>
      </div>

      <DataState
        loading={loading} error={error} onRetry={reload}
        empty={filtered.length === 0}
        label="the underwriting queue"
        emptyIcon={Icons.search}
        emptyTitle={apps.length === 0 ? "No cases assigned to you" : "No cases match this filter"}
        emptySub={apps.length === 0
          ? "New applications appear here once they are assigned."
          : "Try a different filter or search term."}
        emptyAction={apps.length > 0 && (
          <button className="btn btn-ghost" onClick={() => { setFilter("All"); setSearch(""); }}>
            Clear filters
          </button>
        )}
      >
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>App ID</th><th>Applicant</th><th>Loan Type</th><th>Amount</th>
                  <th>Risk</th><th>Fraud</th><th>Applied</th><th>SLA Timer</th>
                  <th>Status</th><th>Next Action</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(a => {
                  const sla = formatSla(a.sla_minutes_left);
                  return (
                    <tr key={a.application_no}>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)", fontWeight: 600 }}>
                        {a.application_no}
                      </td>
                      <td className="bold">
                        {a.customer_name}
                        {(a.risk_score ?? 0) >= 70 &&
                          <span className="badge badge-red" style={{ marginLeft: 6, fontSize: 9 }}>High Risk</span>}
                        {["high", "critical"].includes(a.fraud_level) &&
                          <span className="badge badge-amber" style={{ marginLeft: 6, fontSize: 9 }}>Fraud Alert</span>}
                      </td>
                      <td style={{ fontSize: 12 }}>{a.product_name}</td>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text1)" }}>
                        {formatINR(a.loan_amount)}
                      </td>
                      <td><RiskBadge score={a.risk_score} /></td>
                      <td><FraudBadge level={FRAUD_LABEL[a.fraud_level] || "None"} /></td>
                      <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                        {formatWhen(a.applied_at)}
                      </td>
                      <td style={{
                        fontFamily: "var(--font-mono)", fontSize: 11,
                        color: sla.overdue ? "var(--red)" : "var(--text1)",
                        fontWeight: sla.overdue ? 600 : 400,
                      }}>{sla.text}</td>
                      <td><StatusBadge status={a.status_label} /></td>
                      <td style={{ fontSize: 11, color: "var(--text2)" }}>
                        <span className="badge badge-gray">{a.officer_next_action}</span>
                      </td>
                      <td>
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 8px" }}
                                onClick={() => open(a)}>
                          Open Case
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </DataState>
    </div>
  );
}
