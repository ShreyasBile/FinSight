import { useState, useEffect, useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import RiskBadge from "../../components/RiskBadge.jsx";
import FraudBadge from "../../components/FraudBadge.jsx";
import StatusBadge from "../../components/StatusBadge.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, formatINR, formatWhen, FRAUD_LABEL } from "../../utils/api.js";

// Every application in the database, whoever it is assigned to. Filters run over the
// rows the API returned; the numbers in the header are counted in SQL (api.overview).
const APPROVED = ["accepted", "sanctioned", "disbursed"];
const IN_REVIEW = ["underReview", "underwritingReview"];
const NEW = ["draft", "submitted", "documentsProcessing", "documentsVerified"];
const HEAD_LIMIT = 500000;      // loans above ₹5 lakh need the Head Manager

const TABS = {
  All: () => true,
  New: a => NEW.includes(a.status_code),
  "Under Review": a => IN_REVIEW.includes(a.status_code),
  "Needs Head Approval": a => a.status_code === "underwritingReview" || (a.loan_amount > HEAD_LIMIT && !a.is_terminal),
  Approved: a => APPROVED.includes(a.status_code),
  Rejected: a => a.status_code === "rejected",
};
const RISK_BANDS = {
  Low: r => r < 40, Medium: r => r >= 40 && r < 65, High: r => r >= 65 && r < 80, "Very High": r => r >= 80,
};
const FRAUD_LEVELS = ["None", "Low", "Medium", "High", "Not assessed"];

function exportCsv(rows) {
  const cols = [["Application", a => a.application_no], ["Applicant", a => a.customer_name], ["Loan type", a => a.product_name],
    ["Amount", a => a.loan_amount ?? ""], ["Officer", a => a.officer_name || "Unassigned"], ["Risk score", a => a.risk_score ?? ""],
    ["Fraud", a => FRAUD_LABEL[a.fraud_level] || "None"], ["Status", a => a.status_label], ["Applied", a => a.applied_at || ""]];
  const esc = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv = [cols.map(c => esc(c[0])).join(","), ...rows.map(r => cols.map(c => esc(c[1](r))).join(","))].join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  link.download = "finsight-applications.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

export default function HeadApplications({ setPage, initialSearch = "", openApp }) {
  const [tab, setTab] = useState("All");
  const [loanType, setLoanType] = useState("All");
  const [riskBand, setRiskBand] = useState("All");
  const [fraud, setFraud] = useState("All");
  const [officer, setOfficer] = useState("All");
  const [search, setSearch] = useState(initialSearch);
  // The Topbar search can arrive while this page is already mounted.
  useEffect(() => { if (initialSearch) setSearch(initialSearch); }, [initialSearch]);

  const { data, loading, error, reload } = useApi(() => api.applications(), []);
  const { data: ov } = useApi(() => api.overview(), []);
  const apps = data?.applications ?? [];
  const t = ov?.totals;

  const loanTypes = useMemo(() => [...new Set(apps.map(a => a.product_name))].sort(), [apps]);
  const officers = useMemo(() => [...new Set(apps.map(a => a.officer_name || "Unassigned"))].sort(), [apps]);

  const filtered = useMemo(() => apps.filter(a => {
    if (!TABS[tab](a)) return false;
    if (loanType !== "All" && a.product_name !== loanType) return false;
    if (riskBand !== "All" && !(a.risk_score !== null && RISK_BANDS[riskBand](a.risk_score))) return false;
    if (fraud !== "All" && FRAUD_LABEL[a.fraud_level] !== fraud) return false;
    if (officer !== "All" && (a.officer_name || "Unassigned") !== officer) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!(a.customer_name || "").toLowerCase().includes(q) && !(a.application_no || "").toLowerCase().includes(q)) return false;
    }
    return true;
  }), [apps, tab, loanType, riskBand, fraud, officer, search]);

  const open = a => { openApp?.(a.application_no); setPage("app-detail"); };
  const clear = () => { setTab("All"); setLoanType("All"); setRiskBand("All"); setFraud("All"); setOfficer("All"); setSearch(""); };
  const select = { width: "auto", padding: "6px 10px", fontSize: 11 };

  return (
    <div>
      <PageHeader title="All Applications" sub={`${t?.applications ?? apps.length} total applications`}>
        <button className="btn btn-secondary" disabled={!filtered.length} onClick={() => exportCsv(filtered)}>
          <Icon d={Icons.download} size={13} />Export{filtered.length !== apps.length ? " filtered" : " all"}
        </button>
      </PageHeader>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 16, marginBottom: 24 }}>
        {[
          { label: "Total", val: t?.applications, color: "var(--blue)" },
          { label: "New", val: t?.new_applications, color: "var(--text1)" },
          { label: "Under Review", val: t?.pending_review, color: "var(--purple)" },
          { label: "Needs Head Approval", val: t?.needs_head, color: "var(--amber)" },
          { label: "Approved", val: t?.approved, color: "var(--green)" },
          { label: "Rejected", val: t?.rejected, color: "var(--red)" },
        ].map(s => (
          <div key={s.label} className="stat-card" style={{ padding: "12px 14px" }}>
            <div className="stat-label">{s.label}</div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 24, fontWeight: 700, color: s.color }}>{s.val ?? "—"}</div>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
          <div style={{ display: "flex", gap: 2, background: "var(--surface2)", borderRadius: 8, padding: 3, flexWrap: "wrap" }}>
            {Object.keys(TABS).map(f => <div key={f} className={`tab ${tab === f ? "active" : ""}`} onClick={() => setTab(f)}>{f}</div>)}
          </div>
          <div className="search-bar" style={{ marginLeft: "auto", width: 220 }}>
            <Icon d={Icons.search} size={13} style={{ color: "var(--text3)" }} />
            <input placeholder="Search by name or ID..." value={search} onChange={e => setSearch(e.target.value)} />
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <select className="form-input" style={select} value={loanType} onChange={e => setLoanType(e.target.value)}>
            {["All", ...loanTypes].map(x => <option key={x} value={x}>{x === "All" ? "All loan types" : x}</option>)}
          </select>
          <select className="form-input" style={select} value={riskBand} onChange={e => setRiskBand(e.target.value)}>
            {["All", ...Object.keys(RISK_BANDS)].map(x => <option key={x} value={x}>Risk: {x}</option>)}
          </select>
          <select className="form-input" style={select} value={fraud} onChange={e => setFraud(e.target.value)}>
            {["All", ...FRAUD_LEVELS].map(x => <option key={x} value={x}>Fraud: {x}</option>)}
          </select>
          <select className="form-input" style={select} value={officer} onChange={e => setOfficer(e.target.value)}>
            {["All", ...officers].map(x => <option key={x} value={x}>{x === "All" ? "All officers" : x}</option>)}
          </select>
        </div>
      </div>

      <DataState
        loading={loading} error={error} onRetry={reload}
        empty={filtered.length === 0}
        label="applications"
        emptyIcon={Icons.search}
        emptyTitle={apps.length === 0 ? "No applications yet" : "No applications found"}
        emptySub={apps.length === 0
          ? "Applications appear here as soon as one is submitted."
          : "Try changing the filters or search term."}
        emptyAction={apps.length > 0 && <button className="btn btn-ghost" onClick={clear}>Clear filters</button>}
      >
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>App ID</th><th>Applicant</th><th>Loan Type</th><th>Amount</th><th>Officer</th><th>Risk</th>
                    <th>Fraud</th><th>Applied</th><th>Status</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {filtered.map(a => (
                  <tr key={a.application_no}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)", fontWeight: 600 }}>{a.application_no}</td>
                    <td className="bold">
                      {a.customer_name}
                      {(a.status_code === "underwritingReview" || (a.loan_amount > HEAD_LIMIT && !a.is_terminal)) &&
                        <span className="badge badge-amber" style={{ marginLeft: 6, fontSize: 9 }}>Head approval</span>}
                    </td>
                    <td style={{ fontSize: 12 }}>{a.product_name}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text1)" }}>{formatINR(a.loan_amount)}</td>
                    <td style={{ fontSize: 11, color: "var(--text3)" }}>{a.officer_name || "Unassigned"}</td>
                    <td>{a.risk_score === null ? <span style={{ fontSize: 11, color: "var(--text3)" }}>Not scored</span> : <RiskBadge score={a.risk_score} />}</td>
                    <td><FraudBadge level={FRAUD_LABEL[a.fraud_level] || "None"} /></td>
                    <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{formatWhen(a.applied_at)}</td>
                    <td><StatusBadge status={a.status_label} /></td>
                    <td>
                      <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 8px" }} onClick={() => open(a)}>
                        <Icon d={Icons.eye} size={11} /> Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)", fontSize: 12, color: "var(--text3)" }}>
            Showing {filtered.length} of {apps.length} applications
          </div>
        </div>
      </DataState>
    </div>
  );
}
