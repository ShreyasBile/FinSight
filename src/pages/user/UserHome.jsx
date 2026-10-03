import { useState } from "react";
import { LOAN_PRODUCTS, LOAN_TYPE_LIST } from "../../data/loanProducts.js";
import { Icon, Icons } from "../../icons/Icons.jsx";
import StatusBadge from "../../components/StatusBadge.jsx";
import { setSelectedLoanType } from "../../data/appState.js";
import { api, useApi, formatINR, formatDate, relativeTime } from "../../utils/api.js";

const PRIORITY_COLORS = {
  urgent: "var(--red)", high: "var(--amber)", medium: "var(--blue)", low: "var(--green)",
};

const COMPLEXITY_CLASSES = { Low: "badge-green", Medium: "badge-amber", High: "badge-red" };

export default function UserHome({ setPage, session, openApp }) {
  const [emiAmount, setEmiAmount] = useState("");
  const [emiTenure, setEmiTenure] = useState("12");
  const [emiResult, setEmiResult] = useState(null);

  const customerId = session?.customer_id;
  const userId = session?.user_id;

  const { data: appData } = useApi(
    () => api.applications({ customer_id: customerId, submitted_by: userId }), [customerId, userId], { skip: !customerId });
  const { data: stats } = useApi(
    () => api.borrowerStats(customerId, userId), [customerId, userId], { skip: !customerId });
  const { data: notifData } = useApi(
    () => api.notifications(userId), [userId], { skip: !userId });

  const apps = appData?.applications ?? [];
  const recent = apps.slice(0, 3);
  const notifications = (notifData?.notifications ?? []).slice(0, 3);
  // The "continue where you left off" banner only makes sense if a draft
  // actually exists — it used to show unconditionally.
  const draft = apps.find(a => a.status_code === "draft");
  const firstName = (session?.full_name || "").split(" ")[0];

  const calcEMI = () => {
    const P = parseFloat(emiAmount);
    const n = parseInt(emiTenure);
    if (!(P > 0) || !(n > 0)) return;
    const r = 12 / 12 / 100;
    const emi = P * r * Math.pow(1 + r, n) / (Math.pow(1 + r, n) - 1);
    setEmiResult(Math.round(emi));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {/* 1. WELCOME */}
      <div>
        <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text1)", fontFamily: "var(--font-sans)", marginBottom: 2 }}>
          Welcome to FinSight AI
        </div>
        <div style={{ fontSize: 13, color: "var(--text3)", marginBottom: 8 }}>
          Your trusted AI-powered lending partner
        </div>
        <div style={{ fontSize: 13, color: "var(--text2)", background: "var(--surface2)", borderRadius: 8, padding: "10px 14px", display: "inline-flex", alignItems: "center", gap: 8 }}>
          <Icon d={Icons.star} size={14} style={{ color: "var(--amber)" }} />
          Welcome back{firstName ? `, ${firstName}` : ""}!
        </div>
      </div>

      {/* 2. STATS ROW */}
      <div className="stats-grid">
        {[
          { label: "Total Applications", val: stats?.total ?? "—", color: "var(--blue)" },
          { label: "Active / Pending", val: stats?.active ?? "—", color: "var(--blue)" },
          { label: "Pending Action", val: stats?.awaiting ?? "—", color: "var(--amber)" },
          { label: "Offers", val: stats?.offers ?? "—", color: "var(--green)" },
        ].map((s) => (
          <div key={s.label} className="stat-card">
            <div className="stat-label">{s.label}</div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 28, fontWeight: 700, color: s.color }}>{s.val}</div>
          </div>
        ))}
      </div>

      {/* 3. PENDING ACTION ALERT — only when a draft actually exists */}
      {draft && (
        <div className="alert-banner alert-amber" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <Icon d={Icons.alert} size={18} style={{ color: "var(--amber)", flexShrink: 0 }} />
          <span style={{ flex: 1, fontSize: 13, color: "var(--text1)", lineHeight: 1.5 }}>
            Your {draft.product_name} application ({draft.application_no}) is still a draft —
            continue where you left off.
          </span>
          <button className="btn btn-primary" style={{ fontSize: 12, flexShrink: 0 }} onClick={() => setPage("new-app")}>
            Continue Application
          </button>
        </div>
      )}

      {/* 4. RECENT APPLICATIONS */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "16px 18px 10px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div className="card-title" style={{ marginBottom: 0 }}>Recent Applications</div>
          <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => setPage("my-applications")}>
            View All
          </button>
        </div>
        {recent.length === 0 ? (
          <div style={{ padding: "18px", borderTop: "1px solid var(--border)", fontSize: 12,
                        color: "var(--text3)", textAlign: "center" }}>
            No applications yet — pick a loan below to get started.
          </div>
        ) : recent.map((a) => (
          <div key={a.application_no} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "10px 18px",
            borderTop: "1px solid var(--border)", cursor: "pointer", transition: "background 0.15s",
          }}
            onMouseEnter={(e) => e.currentTarget.style.background = "var(--surface2)"}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
            onClick={() => { openApp?.(a.application_no); setPage("app-detail"); }}>
            <Icon d={LOAN_PRODUCTS[a.loan_product]?.icon || Icons.clipboardList} size={18} style={{flexShrink:0}} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text1)" }}>{a.product_name}</div>
              <div style={{ fontSize: 11, color: "var(--text3)" }}>
                {formatINR(a.loan_amount)} — {formatDate(a.applied_at)}
              </div>
            </div>
            <StatusBadge status={a.status_label} />
            <Icon d={Icons.chevronR} size={12} style={{ color: "var(--text3)", flexShrink: 0 }} />
          </div>
        ))}
      </div>

      {/* 6. LOAN MARKETPLACE */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
          <div className="card-title" style={{ marginBottom: 2 }}>Choose Your Loan</div>
          <div className="card-sub">Select the loan that fits your needs</div>
        </div>
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
          gap: 12, padding: 16,
        }}>
          {LOAN_TYPE_LIST.map((lt) => (
            <div key={lt.id} style={{
              padding: "16px 14px", borderRadius: 8,
              border: "1px solid var(--border)", background: "var(--surface)",
              transition: "all 0.2s", display: "flex", flexDirection: "column",
              cursor: "default",
            }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = lt.color; e.currentTarget.style.transform = "translateY(-2px)"; e.currentTarget.style.boxShadow = "var(--shadow-md)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.transform = "none"; e.currentTarget.style.boxShadow = "none"; }}
            >
              <Icon d={lt.icon} size={28} style={{marginBottom:8}} />
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text1)", marginBottom: 4 }}>{lt.label}</div>
              <div style={{ fontSize: 11, color: "var(--text3)", lineHeight: 1.4, marginBottom: 10, flex: 1 }}>{lt.purpose}</div>
              <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
                <span className={`badge ${lt.secured ? "badge-blue" : "badge-green"}`} style={{ fontSize: 10 }}>
                  {lt.secured ? "Secured" : "Unsecured"}
                </span>
                <span className={`badge ${COMPLEXITY_CLASSES[lt.complexity] || "badge-gray"}`} style={{ fontSize: 10 }}>
                  {lt.complexity}
                </span>
              </div>
              <button className="btn btn-primary" style={{ fontSize: 12, justifyContent: "center", padding: "6px 12px" }}
                onClick={() => { setSelectedLoanType(lt.id); setPage("new-app"); }}>
                Apply Now
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* 7. EMI ESTIMATOR */}
      <div className="card">
          <div className="card-title" style={{ marginBottom: 2 }}>Quick EMI Estimator</div>
          <div className="card-sub" style={{ marginBottom: 14 }}>at 12% p.a. (fixed rate)</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              <div className="form-label" style={{ marginBottom: 4 }}>Loan Amount</div>
              <input className="form-input" type="number" min={0} placeholder="e.g. 500000"
                value={emiAmount} onChange={(e) => setEmiAmount(e.target.value)} />
            </div>
            <div>
              <div className="form-label" style={{ marginBottom: 4 }}>Tenure (months)</div>
              <select className="form-input" value={emiTenure} onChange={(e) => setEmiTenure(e.target.value)}>
                {[6, 12, 18, 24, 36, 48, 60].map((m) => (
                  <option key={m} value={m}>{m} months</option>
                ))}
              </select>
            </div>
            <button className="btn btn-primary" style={{ justifyContent: "center", fontSize: 12 }}
              onClick={calcEMI}>
              Calculate EMI
            </button>
            {emiResult !== null && (
              <div style={{
                background: "var(--blue-tint)", border: "1px solid var(--blue-tint-strong)",
                borderRadius: 8, padding: "12px 14px", textAlign: "center",
              }}>
                <div style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)", marginBottom: 2 }}>Estimated Monthly EMI</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: "var(--blue)", fontFamily: "var(--font-sans)" }}>
                  ₹{emiResult.toLocaleString()}
                </div>
                <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 4 }}>for {emiTenure} months at 12% p.a.</div>
              </div>
            )}
          </div>
      </div>

      {/* 9. RECENT NOTIFICATIONS */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div className="card-title" style={{ marginBottom: 0 }}>Recent Notifications</div>
          <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => setPage("notifications")}>
            View All
          </button>
        </div>
        {notifications.length === 0 ? (
          <div style={{ padding: 18, fontSize: 12, color: "var(--text3)", textAlign: "center" }}>
            No notifications yet.
          </div>
        ) : notifications.map((n, i) => (
          <div key={n.notification_id} className="notif-item"
               style={{ padding: "10px 18px",
                        borderBottom: i < notifications.length - 1 ? "1px solid var(--border)" : "none" }}>
            <div className="notif-dot-marker"
                 style={{ background: PRIORITY_COLORS[n.priority] || "var(--blue)" }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.4 }}>{n.title}</div>
              <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)", marginTop: 3 }}>
                {relativeTime(n.minutes_ago)}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 10. NEED HELP */}
      <div className="card" style={{ textAlign: "center", padding: "18px 20px", background: "var(--surface2)", borderColor: "var(--border)" }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text1)", marginBottom: 4 }}>Need help choosing the right loan?</div>
        <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 12 }}>
          Our loan advisors are here to guide you
        </div>
        <div style={{ display: "flex", justifyContent: "center", gap: 20, fontSize: 12, color: "var(--text2)", flexWrap: "wrap" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Icon d={Icons.phone} size={12} style={{ color: "var(--blue)" }} /> 1800-XXX-XXXX
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Icon d={Icons.send} size={12} style={{ color: "var(--blue)" }} /> Chat with us
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Icon d={Icons.mail} size={12} style={{ color: "var(--blue)" }} /> support@finsight.ai
          </span>
        </div>
      </div>
    </div>
  );
}
