import { useState, useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import StatusBadge from "../../components/StatusBadge.jsx";
import DataState from "../../components/DataState.jsx";
import { LOAN_PRODUCTS } from "../../data/loanProducts.js";
import { api, useApi, formatINR, formatDate } from "../../utils/api.js";

// Tabs group the database's status codes. ref.application_status owns the
// labels and the terminal flag, so this only decides which group a status
// belongs to for filtering.
const TABS = ["All", "Draft", "Submitted", "Processing", "Action Required", "Offers", "Closed"];

const PROCESSING = ["documentsProcessing", "documentsVerified", "underReview", "underwritingReview",
                    "fieldVerification", "propertyValuation", "legalVerification",
                    "incomeVerification", "goldValuationScheduled", "goldValued",
                    "vehicleInspection", "bankGstAnalysis", "propertyDocReview",
                    "legalCheck", "technicalValuation"];

const TAB_FILTERS = {
  All: () => true,
  Draft: a => a.status_code === "draft",
  Submitted: a => a.status_code === "submitted",
  Processing: a => PROCESSING.includes(a.status_code),
  "Action Required": a => ["draft", "offerGenerated"].includes(a.status_code) ||
                           a.current_wait_reason === "awaiting_customer_doc",
  Offers: a => a.status_code === "offerGenerated",
  Closed: a => a.is_terminal,
};

export default function UserApplications({ setPage, session, openApp }) {
  const [filter, setFilter] = useState("All");
  const customerId = session?.customer_id;
  const userId = session?.user_id;   // their list also shows applications they submitted

  const { data, loading, error, reload } = useApi(
    () => api.applications({ customer_id: customerId, submitted_by: userId }), [customerId, userId], { skip: !customerId }
  );
  const { data: stats } = useApi(
    () => api.borrowerStats(customerId, userId), [customerId, userId], { skip: !customerId }
  );

  const apps = data?.applications ?? [];
  const filtered = useMemo(() => apps.filter(TAB_FILTERS[filter]), [apps, filter]);

  const urgencyColor = (a) => {
    if (a.status_code === "offerGenerated") return "var(--green)";
    if (a.status_code === "draft") return "var(--blue)";
    if (a.status_code === "documentsProcessing") return "var(--amber)";
    return "var(--text3)";
  };

  const view = (a) => { openApp?.(a.application_no); setPage("app-detail"); };

  const actionBtn = (a) => {
    if (a.status_code === "offerGenerated")
      return <button className="btn btn-primary" style={{ fontSize: 11, padding: "4px 10px", background: "var(--green)" }}
                     onClick={() => view(a)}><Icon d={Icons.eye} size={10} /> View Offer</button>;
    if (a.status_code === "draft")
      return <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px", color: "var(--blue)" }}
                     onClick={() => setPage("new-app")}><Icon d={Icons.edit} size={10} /> Continue</button>;
    return <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }}
                   onClick={() => view(a)}><Icon d={Icons.eye} size={10} /> View</button>;
  };

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>My Applications</h2>
          <p style={{ fontSize: 12, color: "var(--text3)", margin: 0 }}>
            Track and manage your loan applications
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setPage("new-app")}>
          <Icon d={Icons.plus} size={13} /> Apply for New Loan
        </button>
      </div>

      {apps.length > 0 && (
        <>
          <div style={{ display: "flex", gap: 12, margin: "16px 0", flexWrap: "wrap" }}>
            {[
              { key: "total",    label: "Total",            value: stats?.total ?? apps.length },
              { key: "active",   label: "Active / Pending", value: stats?.active ?? "—" },
              { key: "awaiting", label: "Awaiting Action",  value: stats?.awaiting ?? "—" },
              { key: "offers",   label: "Offers",           value: stats?.offers ?? "—" },
            ].map(s => (
              <div key={s.key} className="stat-card" style={{ flex: 1, minWidth: 120, padding: "12px 16px" }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text1)" }}>{s.value}</div>
                <div style={{ fontSize: 11, color: "var(--text3)" }}>{s.label}</div>
              </div>
            ))}
          </div>

          <div className="tabs" style={{ marginBottom: 16 }}>
            {TABS.map(t => (
              <button key={t} className={`tab${filter === t ? " active" : ""}`}
                      onClick={() => setFilter(t)}>{t}</button>
            ))}
          </div>
        </>
      )}

      <DataState
        loading={loading} error={error} onRetry={reload}
        empty={filtered.length === 0}
        label="your applications"
        emptyIcon={Icons.clipboardList}
        emptyTitle={apps.length === 0 ? "No applications yet" : "No applications match this filter"}
        emptySub={apps.length === 0
          ? "Start your first loan application to see it here."
          : "Try a different filter or create a new application."}
        emptyAction={
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            {apps.length > 0 && (
              <button className="btn btn-ghost" onClick={() => setFilter("All")}>Clear Filter</button>
            )}
            <button className="btn btn-primary" onClick={() => setPage("new-app")}>
              <Icon d={Icons.plus} size={13} /> Apply Now
            </button>
          </div>
        }
      >
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Application ID</th><th>Loan Type</th><th>Amount</th><th>Applied</th>
                  <th>Current Stage</th><th>Status</th><th>Pending Action</th>
                  <th>Last Updated</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(a => (
                  <tr key={a.application_no}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)" }}>
                      {a.application_no}
                    </td>
                    <td style={{ fontSize: 13 }}>
                      <Icon d={LOAN_PRODUCTS[a.loan_product]?.icon} size={14}
                            style={{ verticalAlign: "middle", marginRight: 4 }} />
                      {a.product_name}
                    </td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                      {formatINR(a.loan_amount)}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text3)" }}>{formatDate(a.applied_at)}</td>
                    <td style={{ fontSize: 12 }}>{a.stage_group}</td>
                    <td><StatusBadge status={a.status_label} /></td>
                    <td style={{ fontSize: 11 }}>
                      <span className="badge" style={{ background: urgencyColor(a), color: "#fff", border: "none" }}>
                        {a.borrower_next_action}
                      </span>
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text3)" }}>{formatDate(a.last_activity_at)}</td>
                    <td>{actionBtn(a)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </DataState>
    </div>
  );
}
