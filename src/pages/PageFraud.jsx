import { useMemo } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import PageHeader from "../components/PageHeader.jsx";
import DataState from "../components/DataState.jsx";
import { api, useApi, formatWhen } from "../utils/api.js";

// Fraud cases are rows in fraud.fraud_cases (opened from the Fraud Triage agent's
// proposals or by an officer); the counts come from api.overview. Nothing on this
// page is a constant: with no cases it is empty.
const SEVERITY_BADGE = { critical: "badge-red", high: "badge-red", medium: "badge-amber", low: "badge-blue" };
const STATUS_BADGE = { escalated: "badge-red", confirmed: "badge-red", cleared: "badge-green", dismissed: "badge-gray" };

function Bars({ rows, empty, color = "var(--red)", unit = "" }) {
  const max = Math.max(1, ...rows.map(r => r.n));
  if (!rows.length) return <div style={{ fontSize: 12, color: "var(--text3)", padding: "10px 0" }}>{empty}</div>;
  return rows.map(r => (
    <div key={r.label} style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: "var(--text2)", textTransform: "capitalize" }}>{r.label}</span>
        <span style={{ fontFamily: "var(--font-mono)", color }}>{r.n}{unit}</span>
      </div>
      <div className="progress-bar"><div className="progress-fill" style={{ width: `${(r.n / max) * 100}%`, background: color }} /></div>
    </div>
  ));
}

export default function PageFraud({ role, setPage, openApp }) {
  const cases = useApi(() => api.fraudCases(), []);
  const ov = useApi(() => api.overview(), []);
  const list = cases.data?.cases ?? [];
  const f = ov.data?.fraud;
  const docs = ov.data?.documents;

  const byBranch = useMemo(() => {
    const m = {};
    list.forEach(c => { const b = c.branch || "—"; m[b] = (m[b] || 0) + 1; });
    return Object.entries(m).map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n);
  }, [list]);
  const severe = list.filter(c => ["high", "critical"].includes(c.severity)).length;

  const open = (no) => { if (!no) return; openApp?.(no); setPage("app-detail"); };

  return (
    <div>
      <PageHeader title="Fraud Detection" sub="Fraud cases and document-forensics findings across all applications" />

      <div className="grid4" style={{ marginBottom: 16 }}>
        {[
          { label: "Fraud Cases", value: f?.cases, color: "var(--red)", sub: "Open and under review" },
          { label: "High Severity", value: severe, color: "var(--red)", sub: "High or critical" },
          { label: "Documents Flagged", value: docs?.tamper_flagged, color: "var(--purple)", sub: `${docs?.tamper_high ?? 0} rated HIGH by forensics` },
          { label: "Documents To Review", value: docs?.needs_review, color: "var(--amber)", sub: `of ${docs?.documents ?? 0} stored` },
        ].map(s => (
          <div key={s.label} className="stat-card">
            <div className="stat-label">{s.label}</div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 28, fontWeight: 700, color: s.color }}>{s.value ?? "—"}</div>
            <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>{s.sub}</div>
          </div>
        ))}
      </div>

      <div className="grid3" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>Cases by Severity</div>
          <Bars rows={(f?.by_severity ?? []).map(r => ({ label: r.severity, n: r.n }))} empty="No fraud cases" />
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>Cases by Category</div>
          <Bars rows={(f?.by_category ?? []).map(r => ({ label: r.category, n: r.n }))} empty="No fraud cases" color="var(--amber)" />
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>Top Fraud Signals</div>
          <Bars rows={(f?.top_signals ?? []).map(r => ({ label: r.signal_name, n: r.n }))} empty="No signals recorded" color="var(--purple)" />
        </div>
      </div>

      <div className="grid2" style={{ marginBottom: 16 }}>
        <div className="card" style={{ gridColumn: role === "head" ? "span 1" : "span 2" }}>
          <div className="card-title" style={{ marginBottom: 14 }}>Fraud Cases</div>
          <DataState
            loading={cases.loading} error={cases.error} onRetry={cases.reload}
            empty={list.length === 0} label="fraud cases"
            emptyIcon={Icons.fraud} emptyTitle="No fraud cases" emptySub="Cases appear here when an application is flagged."
          >
            <div className="table-wrap">
              <table>
                <thead><tr><th>App ID</th><th>Applicant</th><th>Category</th><th>Score</th><th>Officer</th><th>Branch</th><th>Status</th><th>Opened</th><th>Action</th></tr></thead>
                <tbody>
                  {list.map(c => (
                    <tr key={c.case_id}>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)" }}>{c.application_no || "—"}</td>
                      <td className="bold" style={{ fontSize: 12 }}>{c.customer_name}</td>
                      <td><span className={`badge ${SEVERITY_BADGE[c.severity] || "badge-gray"}`}>{c.fraud_category || c.severity}</span></td>
                      <td style={{ fontFamily: "var(--font-mono)", color: c.score > 70 ? "var(--red)" : "var(--amber)", fontWeight: 600 }}>{c.score ?? "—"}</td>
                      <td style={{ fontSize: 11, color: "var(--text3)" }}>{c.officer_name || "Unassigned"}</td>
                      <td style={{ fontSize: 11, color: "var(--text3)" }}>{c.branch || "—"}</td>
                      <td><span className={`badge ${STATUS_BADGE[c.status] || "badge-amber"}`}>{c.status}</span></td>
                      <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{formatWhen(c.created_at)}</td>
                      <td><button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 8px" }} onClick={() => open(c.application_no)}>Review</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </DataState>
        </div>

        {role === "head" && (
          <div>
            <div className="card" style={{ marginBottom: 16 }}>
              <div className="card-title" style={{ marginBottom: 14 }}>Fraud by Branch</div>
              <Bars rows={byBranch} empty="No fraud cases" unit=" cases" />
            </div>
            <div className="card">
              <div className="card-title" style={{ marginBottom: 8 }}>Rings &amp; Linked Identities</div>
              <div style={{ fontSize: 12, color: "var(--text3)", lineHeight: 1.6, marginBottom: 12 }}>
                The Fraud Triage agent compares every application for a shared PAN, Aadhaar, bank account, file or face and
                proposes rings for you to open. It never files a case by itself.
              </div>
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setPage("agents")}>
                <Icon d={Icons.fraud} size={13} /> Open Fraud Triage
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
