import { useState, useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, formatDate } from "../../utils/api.js";

const TYPE_LABEL = {
  credit_memo: "Credit Memo", risk_report: "Risk Report",
  fraud_report: "Fraud Report", policy_exception: "Policy Exception",
};
const TYPE_BADGE = {
  credit_memo: "badge-blue", risk_report: "badge-amber",
  fraud_report: "badge-red", policy_exception: "badge-purple",
};
const STATUS_LABEL = {
  draft: "Draft", generated: "Generated", reviewed: "Reviewed", sent_to_manager: "Sent to Manager",
};
const STATUS_BADGE = {
  draft: "badge-gray", generated: "badge-blue", reviewed: "badge-green", sent_to_manager: "badge-amber",
};

const TABS = ["All", "credit_memo", "risk_report", "fraud_report", "policy_exception"];

export default function OfficerReports({ setPage, session, openApp }) {
  const [tab, setTab] = useState("All");
  const officerId = session?.user_id;

  const { data, loading, error, reload } = useApi(
    () => api.reports(officerId), [officerId], { skip: !officerId }
  );
  // Performance and the branch leaderboard are counted in SQL — the numbers
  // used to be constants that never moved no matter what the officer did.
  const { data: stats } = useApi(
    () => api.officerStats(officerId), [officerId], { skip: !officerId }
  );

  const reports = data?.reports ?? [];
  const shown = useMemo(
    () => tab === "All" ? reports : reports.filter(r => r.report_type === tab),
    [reports, tab]
  );

  const count = (s) => reports.filter(r => r.status === s).length;
  const q = stats?.queue;
  const perf = stats?.performance;
  const board = stats?.leaderboard ?? [];

  const open = (r) => { openApp?.(r.application_no); setPage("app-detail"); };

  return (
    <div>
      <PageHeader title="Reports & Memos"
                  sub="Credit memos, risk reports, fraud reports and policy exceptions" />

      <div className="grid4" style={{ gap: 12, marginBottom: 16 }}>
        {[
          { label: "Total Reports",   value: reports.length,           color: "var(--blue)" },
          { label: "Draft",           value: count("draft"),           color: "var(--text2)" },
          { label: "Generated",       value: count("generated"),       color: "var(--green)" },
          { label: "Sent to Manager", value: count("sent_to_manager"), color: "var(--amber)" },
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
        <div style={{ display: "flex", gap: 2, background: "var(--surface2)", borderRadius: 8, padding: 3 }}>
          {TABS.map(t => (
            <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {t === "All" ? "All" : TYPE_LABEL[t]}
            </div>
          ))}
        </div>
      </div>

      <DataState
        loading={loading} error={error} onRetry={reload}
        empty={shown.length === 0}
        label="reports"
        emptyIcon={Icons.report}
        emptyTitle={reports.length === 0 ? "No reports yet" : "No reports of this type"}
        emptySub={reports.length === 0
          ? "Reports appear here once generated from a case."
          : "Try another tab."}
      >
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title" style={{ marginBottom: 14 }}>All Reports</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Report ID</th><th>Application</th><th>Type</th>
                    <th>Generated</th><th>Status</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {shown.map(r => (
                  <tr key={r.report_no}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)", fontWeight: 600 }}>
                      {r.report_no}
                    </td>
                    <td>
                      <span className="bold" style={{ fontSize: 12 }}>{r.customer_name}</span><br />
                      <span style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                        {r.application_no}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${TYPE_BADGE[r.report_type] || "badge-gray"}`} style={{ fontSize: 10 }}>
                        {TYPE_LABEL[r.report_type] || r.report_type}
                      </span>
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                      {formatDate(r.generated_at)}
                    </td>
                    <td>
                      <span className={`badge ${STATUS_BADGE[r.status] || "badge-gray"}`} style={{ fontSize: 10 }}>
                        {STATUS_LABEL[r.status] || r.status}
                      </span>
                    </td>
                    <td>
                      <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 8px" }}
                              onClick={() => open(r)}>
                        <Icon d={Icons.eye} size={11} /> Preview
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </DataState>

      <div className="grid2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>My Performance</div>
          {[
            ["Applications Assigned", q?.total ?? "—"],
            ["Approved", q?.approved ?? "—"],
            ["Rejected", q?.rejected ?? "—"],
            ["Pending Review", q?.pending ?? "—"],
            ["Awaiting Documents", q?.awaiting_docs ?? "—"],
            ["Avg Processing Time", perf?.avg_hours != null ? `${perf.avg_hours.toFixed(1)} hrs` : "—"],
            ["Fraud Cases Escalated", perf?.fraud_found ?? "—"],
            ["Policy Tickets Raised", perf?.tickets_raised ?? "—"],
          ].map(([k, v]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between",
                                  padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
              <span style={{ fontSize: 12, color: "var(--text3)" }}>{k}</span>
              <span style={{ fontSize: 13, color: "var(--text1)", fontWeight: 600,
                             fontFamily: "var(--font-mono)" }}>{v}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>Rank in Team</div>
          {board.length === 0
            ? <div style={{ fontSize: 12, color: "var(--text3)" }}>No data yet.</div>
            : board.map((o, i) => {
                const you = o.officer_id === session?.user_id;
                return (
                  <div key={o.officer_id} style={{
                    display: "flex", alignItems: "center", gap: 10, marginBottom: 10,
                    padding: you ? "8px 10px" : "4px 0",
                    background: you ? "var(--blue-tint)" : "transparent",
                    borderRadius: 6,
                    border: you ? "1px solid var(--blue-tint-strong)" : "none",
                  }}>
                    <span style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)", width: 16 }}>
                      #{i + 1}
                    </span>
                    <span style={{ fontSize: 13, color: you ? "var(--blue)" : "var(--text2)",
                                   flex: 1, fontWeight: you ? 600 : 400 }}>
                      {you ? `You (${o.full_name.split(" ")[0]})` : o.full_name}
                    </span>
                    <span style={{ fontSize: 11, color: "var(--text3)" }}>{o.branch_code}</span>
                    <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text1)" }}>
                      {o.apps} apps
                    </span>
                  </div>
                );
              })}
        </div>
      </div>
    </div>
  );
}
