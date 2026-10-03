import { Icons } from "../icons/Icons.jsx";
import DataState from "../components/DataState.jsx";
import { api, useApi, formatWhen } from "../utils/api.js";
import { agentsApi } from "../utils/agents.js";

// The three Settings tabs that used to show invented data. Each reads the real thing:
// ops.users, audit.audit_log (written by database triggers) and the running services.

const ROLE_LABEL = { head: "Head Manager", officer: "Underwriting Officer", user: "Borrower", admin: "Admin" };
const OP_BADGE = { INSERT: "badge-green", UPDATE: "badge-amber", DELETE: "badge-red" };

export function UsersTab() {
  const { data, loading, error, reload } = useApi(() => api.users(), []);
  const rows = data?.users ?? [];
  return (
    <div className="card">
      <div className="card-title" style={{ marginBottom: 14 }}>Team Members</div>
      <DataState loading={loading} error={error} onRetry={reload} empty={rows.length === 0} label="users"
                 emptyIcon={Icons.users} emptyTitle="No users" emptySub="Run docs/mssql/07-seed-demo-data.sql to create the demo people.">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Branch</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map(u => (
                <tr key={u.user_id}>
                  <td className="bold">{u.full_name}</td>
                  <td style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text3)" }}>{u.email}</td>
                  <td style={{ fontSize: 12 }}>{ROLE_LABEL[u.role_code] || u.role_code}</td>
                  <td style={{ fontSize: 12 }}>{u.branch_code || "—"}</td>
                  <td><span className={`badge ${u.is_active ? "badge-green" : "badge-gray"}`}>{u.is_active ? "Active" : "Inactive"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DataState>
    </div>
  );
}

export function AuditTab() {
  const { data, loading, error, reload } = useApi(() => api.audit(100), []);
  const rows = data?.entries ?? [];
  return (
    <div className="card">
      <div className="card-title" style={{ marginBottom: 4 }}>Audit Trail</div>
      <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 14 }}>
        Recorded by database triggers: every change to an application, customer, offer, fraud case, risk assessment or user. Latest {rows.length || 100} entries.
      </div>
      <DataState loading={loading} error={error} onRetry={reload} empty={rows.length === 0} label="the audit trail"
                 emptyIcon={Icons.clipboardList} emptyTitle="Nothing recorded yet" emptySub="Changes appear here as soon as they happen.">
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Application</th><th>Changed</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.log_id}>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text3)", whiteSpace: "nowrap" }}>{formatWhen(r.occurred_at)}</td>
                  <td style={{ fontSize: 12, fontWeight: 500, color: "var(--text1)" }}>{r.changed_by || "System"}</td>
                  <td><span className={`badge ${OP_BADGE[r.operation] || "badge-gray"}`} style={{ fontSize: 10 }}>{r.operation}</span></td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text2)" }}>{r.table_name}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--blue)" }}>{r.application_no || "—"}</td>
                  <td style={{ fontSize: 11, color: "var(--text3)", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.changed_columns || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DataState>
    </div>
  );
}

const Dot = ({ ok }) => <span style={{ width: 8, height: 8, borderRadius: "50%", background: ok ? "var(--green)" : "var(--red)", display: "inline-block" }} />;

export function StatusTab() {
  const agents = useApi(() => agentsApi.health(), []);
  const db = useApi(() => api.health(), []);
  const h = agents.data;
  const sink = h?.sql_sink;
  const rows = [
    { name: "API", detail: h ? `CrewAI ${h.crewai} · environment ${h.environment}` : "not reachable", ok: !!h },
    { name: "Database", detail: db.data ? `${db.data.database} on ${db.data.server} · ${db.data.applications} application(s)` : "not reachable", ok: !!db.data?.ok },
    { name: "Local model server", detail: h?.ollama_reachable ? "Ollama answering" : "not reachable — document reading will fail", ok: !!h?.ollama_reachable },
    { name: "Agents", detail: h ? `${h.implemented} of ${h.registered} implemented · ${h.agents_enabled ? "enabled" : "kill switch on"}` : "—", ok: !!h && h.agents_enabled },
    { name: "Model queue", detail: h ? `${Object.values(h.llm_queue?.active || {}).reduce((a, b) => a + b, 0)} running · ${Object.values(h.llm_queue?.waiting || {}).reduce((a, b) => a + b, 0)} waiting` : "—", ok: !!h },
    { name: "Event log (process mining)", detail: sink ? (sink.enabled ? `writing to SQL Server · ${sink.events_written} events · ${sink.spooled} spooled` : "off") : "—", ok: !!sink?.enabled && !sink?.breaker_open },
  ];
  return (
    <div className="card">
      <div className="card-title" style={{ marginBottom: 14 }}>System Status</div>
      {rows.map(p => (
        <div key={p.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
          <div>
            <div style={{ fontSize: 12, color: "var(--text1)", fontWeight: 500 }}>{p.name}</div>
            <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{p.detail}</div>
          </div>
          <Dot ok={p.ok} />
        </div>
      ))}
    </div>
  );
}
