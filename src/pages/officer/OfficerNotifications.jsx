import { useState, useMemo } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import DataState from "../../components/DataState.jsx";
import { api, useApi, relativeTime } from "../../utils/api.js";

const PRIORITY_COLOR = {
  urgent: "var(--red)", high: "var(--amber)", medium: "var(--blue)", low: "var(--text3)",
};

// Tabs filter on fields the row actually carries (priority, reference to an
// application, read state) rather than on a hand-assigned category, so a new
// notification type needs no code change to appear correctly.
const TABS = ["All", "Urgent", "Applications", "Unread"];

const MATCHES = {
  All: () => true,
  Urgent: n => ["urgent", "high"].includes(n.priority),
  Applications: n => !!n.application_no,
  Unread: n => !n.is_read,
};

// Used by every role (officer, head, borrower): the rows are per user_id.
export default function OfficerNotifications({ session, setPage, openApp }) {
  const [tab, setTab] = useState("All");
  const userId = session?.user_id;

  const { data, loading, error, reload } = useApi(
    () => api.notifications(userId), [userId], { skip: !userId }
  );

  const all = data?.notifications ?? [];
  const shown = useMemo(() => all.filter(MATCHES[tab]), [all, tab]);

  // Opening a notification marks it read and, when it is about an application, opens that.
  const open = async (n) => {
    if (!n.is_read) {
      try { await api.markRead(n.notification_id, userId); } catch { /* the badge just stays */ }
    }
    if (n.application_no && openApp && setPage) {
      openApp(n.application_no);
      setPage("app-detail");
    } else {
      reload();
    }
  };
  const markAll = async () => {
    try { await api.markAllRead(userId); } finally { reload(); }
  };

  return (
    <div>
      <PageHeader
        title="Notifications"
        sub={data ? `${data.unread} unread of ${all.length}` : "Loading…"}
      >
        {data?.unread > 0 && (
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={markAll}>
            <Icon d={Icons.check} size={12} /> Mark all as read
          </button>
        )}
      </PageHeader>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 2, background: "var(--surface2)", borderRadius: 8, padding: 3 }}>
          {TABS.map(t => (
            <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</div>
          ))}
        </div>
      </div>

      <DataState
        loading={loading} error={error} onRetry={reload}
        empty={shown.length === 0}
        label="notifications"
        emptyIcon={Icons.bell}
        emptyTitle={all.length === 0 ? "No notifications" : "Nothing in this tab"}
        emptySub={all.length === 0 ? "You're all caught up." : "Try another tab to see the rest."}
      >
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          {shown.map((n, i) => (
            <div key={n.notification_id} className="notif-item" onClick={() => open(n)}
                 title={n.application_no ? `Open ${n.application_no}` : "Mark as read"} style={{
              padding: "12px 18px", cursor: "pointer",
              borderBottom: i < shown.length - 1 ? "1px solid var(--border)" : "none",
              background: n.is_read ? "transparent" : "var(--surface2)",
            }}>
              <div className="notif-dot-marker"
                   style={{ background: PRIORITY_COLOR[n.priority] || "var(--text3)" }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: n.is_read ? 400 : 600, color: "var(--text1)" }}>
                  {n.title}
                </div>
                {n.body && (
                  <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.5, marginTop: 3 }}>
                    {n.body}
                  </div>
                )}
                <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 5, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                    {relativeTime(n.minutes_ago)}
                  </span>
                  {n.application_no && (
                    <span style={{ fontSize: 10, color: "var(--blue)", fontFamily: "var(--font-mono)" }}>
                      {n.application_no}
                    </span>
                  )}
                  {["urgent", "high"].includes(n.priority) && (
                    <span className={`badge ${n.priority === "urgent" ? "badge-red" : "badge-amber"}`}
                          style={{ fontSize: 9, textTransform: "capitalize" }}>
                      {n.priority}
                    </span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </DataState>
    </div>
  );
}
