import PageHeader from "../components/PageHeader.jsx";
import ProgressBar from "../components/ProgressBar.jsx";
import DataState from "../components/DataState.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import { api, useApi, formatINR } from "../utils/api.js";

// Portfolio risk, computed from the stored risk assessments (api.overview). There is no
// repayment data in FinSight yet, so arrears / NPA figures are deliberately not shown.
const BANDS = [
  { key: "low", label: "Low", color: "var(--green)" },
  { key: "medium", label: "Medium", color: "var(--amber)" },
  { key: "high", label: "High", color: "var(--red)" },
  { key: "very_high", label: "Very High", color: "var(--red)" },
  { key: "unscored", label: "Not scored yet", color: "var(--text3)" },
];
const riskColor = r => (r == null ? "var(--text3)" : r >= 70 ? "var(--red)" : r >= 40 ? "var(--amber)" : "var(--green)");

const Empty = ({ children }) => <div style={{ fontSize: 12, color: "var(--text3)", padding: "10px 0" }}>{children}</div>;

export default function PageRiskDashboard() {
  const ov = useApi(() => api.overview(), []);
  const apps = useApi(() => api.applications({ include_closed: false }), []);
  const d = ov.data;
  const t = d?.totals;
  const bandCount = Object.fromEntries((d?.risk_bands ?? []).map(b => [b.band, b.n]));
  const total = t?.applications || 0;
  const products = d?.by_product ?? [];
  const exposure = products.reduce((s, p) => s + (p.amount || 0), 0);
  const watch = (apps.data?.applications ?? []).filter(a => (a.risk_score ?? 0) >= 70 || ["high", "critical"].includes(a.fraud_level));

  return (
    <div>
      <PageHeader title="Portfolio Risk" sub="Risk across every application in the database — all branches" />
      <DataState loading={ov.loading} error={ov.error} onRetry={ov.reload} label="portfolio risk">
        <div className="grid4" style={{ marginBottom: 16 }}>
          {[
            { label: "Average Risk Score", value: t?.avg_risk == null ? "—" : Math.round(t.avg_risk), color: riskColor(t?.avg_risk), sub: `${t?.scored ?? 0} of ${total} scored` },
            { label: "High-Risk Applications", value: t?.high_risk ?? 0, color: "var(--red)", sub: "Risk score 70 or more" },
            { label: "Requested Exposure", value: formatINR(exposure), color: "var(--blue)", sub: `${total} application${total === 1 ? "" : "s"}` },
            { label: "Fraud Cases", value: d?.fraud.cases ?? 0, color: "var(--amber)", sub: "Open and under review" },
          ].map(s => (
            <div key={s.label} className="stat-card">
              <div className="stat-label">{s.label}</div>
              <div style={{ fontFamily: "var(--font-sans)", fontSize: 22, fontWeight: 700, color: s.color, marginBottom: 2 }}>{s.value}</div>
              <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 2 }}>{s.sub}</div>
            </div>
          ))}
        </div>

        <div className="grid2" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Risk by Segment</div>
            {products.length === 0 ? <Empty>No applications yet</Empty> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Segment</th><th>Applications</th><th>Requested</th><th>Avg risk</th><th>High risk</th></tr></thead>
                  <tbody>
                    {products.map(p => (
                      <tr key={p.loan_product}>
                        <td style={{ fontSize: 12 }}>{p.name}</td>
                        <td style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text2)" }}>{p.n}</td>
                        <td style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text2)" }}>{formatINR(p.amount)}</td>
                        <td style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: riskColor(p.avg_risk) }}>{p.avg_risk == null ? "—" : Math.round(p.avg_risk)}</td>
                        <td style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: p.high_risk ? "var(--red)" : "var(--text2)" }}>{p.high_risk}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Watchlist</div>
            {watch.length === 0 ? <Empty>No open application is high-risk or flagged for fraud</Empty> : watch.map(a => (
              <div key={a.application_no} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--border)", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 12, color: "var(--text1)", fontWeight: 500 }}>{a.customer_name}</div>
                  <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{a.application_no} · {a.product_name}</div>
                </div>
                {a.risk_score !== null && <RiskBadge score={a.risk_score} />}
              </div>
            ))}
          </div>
        </div>

        <div className="grid3" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Risk Distribution</div>
            {total === 0 ? <Empty>No applications yet</Empty> : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {BANDS.map(b => {
                  const n = bandCount[b.key] || 0;
                  const pct = Math.round((n / total) * 100);
                  return (
                    <div key={b.key}>
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                        <span style={{ fontSize: 12, color: "var(--text2)" }}>{b.label}</span>
                        <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: b.color, fontWeight: 600 }}>{n} ({pct}%)</span>
                      </div>
                      <ProgressBar value={pct} color={b.color} />
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Risk by Branch</div>
            {(d?.by_branch ?? []).length === 0 ? <Empty>No applications yet</Empty> : d.by_branch.map(b => (
              <div key={b.branch} style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                  <span style={{ fontSize: 12, color: "var(--text2)" }}>{b.branch} <span style={{ color: "var(--text3)" }}>· {b.n}</span></span>
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: riskColor(b.avg_risk), fontWeight: 600 }}>{b.avg_risk == null ? "not scored" : Math.round(b.avg_risk)}</span>
                </div>
                <ProgressBar value={b.avg_risk ?? 0} color={riskColor(b.avg_risk)} />
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Concentration</div>
            {products.length === 0 || exposure === 0 ? <Empty>No requested amounts yet</Empty> : (
              <>
                {products.filter(p => p.amount > 0).map(p => (
                  <div key={p.loan_product} style={{ display: "flex", justifyContent: "space-between", padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ fontSize: 12, color: "var(--text2)" }}>{p.name}</span>
                    <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text1)" }}>{formatINR(p.amount)} ({Math.round((p.amount / exposure) * 100)}%)</span>
                  </div>
                ))}
                <div style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", marginTop: 4 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text1)" }}>Total requested</span>
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--blue)" }}>{formatINR(exposure)}</span>
                </div>
              </>
            )}
          </div>
        </div>
      </DataState>
    </div>
  );
}
