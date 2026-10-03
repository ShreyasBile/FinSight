import { useMemo } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import PageHeader from "../components/PageHeader.jsx";
import DataState from "../components/DataState.jsx";
import { api, useApi } from "../utils/api.js";
import { agentsApi } from "../utils/agents.js";

// Relationships as they exist in the database: customer -> application -> officer.
// Suspicious links (a PAN, Aadhaar, bank account, file or face shared between applications)
// are proposed by the Fraud Triage agent from the dedupe index; nothing here is invented.
const MAX_APPS = 12;
const COLS = [
  { key: "customer", label: "Customers", x: 90, fill: "var(--blue-tint-strong)", stroke: "var(--blue)" },
  { key: "application", label: "Applications", x: 250, fill: "var(--green-tint-strong)", stroke: "var(--green)" },
  { key: "officer", label: "Officers", x: 410, fill: "var(--purple-tint-strong)", stroke: "var(--purple)" },
];

function buildGraph(apps) {
  const shown = apps.slice(0, MAX_APPS);
  const nodes = { customer: [], application: [], officer: [] };
  const seen = { customer: {}, officer: {} };
  const edges = [];
  shown.forEach(a => {
    if (!seen.customer[a.customer_id]) { seen.customer[a.customer_id] = { id: a.customer_id, label: a.customer_name }; nodes.customer.push(seen.customer[a.customer_id]); }
    const app = { id: a.application_no, label: a.application_no.replace(/^APP-/, "") };
    nodes.application.push(app);
    edges.push([["customer", a.customer_id], ["application", app.id]]);
    if (a.assigned_officer_id) {
      if (!seen.officer[a.assigned_officer_id]) { seen.officer[a.assigned_officer_id] = { id: a.assigned_officer_id, label: a.officer_name }; nodes.officer.push(seen.officer[a.assigned_officer_id]); }
      edges.push([["application", app.id], ["officer", a.assigned_officer_id]]);
    }
  });
  return { nodes, edges, hidden: Math.max(0, apps.length - shown.length) };
}

export default function PageKnowledgeGraph({ setPage, openApp }) {
  const apps = useApi(() => api.applications(), []);
  const ov = useApi(() => api.overview(), []);
  const triage = useApi(() => agentsApi.triage(), []);
  const list = apps.data?.applications ?? [];
  const graph = useMemo(() => buildGraph(list), [list]);
  const t = ov.data?.totals;
  const proposals = triage.data?.proposals ?? [];

  const rows = Math.max(1, ...Object.values(graph.nodes).map(n => n.length));
  const H = Math.max(160, rows * 44 + 40);
  const pos = {};
  COLS.forEach(c => graph.nodes[c.key].forEach((n, i) => {
    pos[`${c.key}:${n.id}`] = { x: c.x, y: 30 + (H - 60) * (graph.nodes[c.key].length === 1 ? 0.5 : i / (graph.nodes[c.key].length - 1)) };
  }));

  return (
    <div>
      <PageHeader title="Relationship Graph" sub="How customers, applications and officers connect — shared identities come from the Fraud Triage agent" />

      <div className="grid4" style={{ marginBottom: 16 }}>
        {[
          { label: "Customers", value: t?.customers, color: "var(--blue)" },
          { label: "Applications", value: t?.applications, color: "var(--green)" },
          { label: "Documents", value: ov.data?.documents.documents, color: "var(--purple)" },
          { label: "Suspicious Links", value: triage.data?.links, color: "var(--red)" },
        ].map(s => (
          <div key={s.label} className="stat-card">
            <div className="stat-label">{s.label}</div>
            <div style={{ fontFamily: "var(--font-sans)", fontSize: 24, fontWeight: 700, color: s.color }}>{s.value ?? "—"}</div>
          </div>
        ))}
      </div>

      <DataState
        loading={apps.loading} error={apps.error} onRetry={apps.reload}
        empty={list.length === 0} label="the relationship graph"
        emptyIcon={Icons.graph} emptyTitle="No relationships yet" emptySub="The graph fills in as applications are submitted."
      >
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title" style={{ marginBottom: 12 }}>Customer → Application → Officer</div>
          <div className="graph-canvas" style={{ minHeight: H }}>
            <svg width="100%" viewBox={`0 0 500 ${H + 24}`} style={{ display: "block", maxWidth: 620, margin: "0 auto" }}>
              {COLS.map(c => (
                <text key={c.key} x={c.x} y={12} textAnchor="middle" fill="var(--text3)" fontSize="9" fontFamily="var(--font-mono)">{c.label.toUpperCase()}</text>
              ))}
              <g transform="translate(0,16)">
                {graph.edges.map(([a, b], i) => (
                  <line key={i} x1={pos[`${a[0]}:${a[1]}`].x} y1={pos[`${a[0]}:${a[1]}`].y} x2={pos[`${b[0]}:${b[1]}`].x} y2={pos[`${b[0]}:${b[1]}`].y}
                        stroke="var(--border-strong)" strokeWidth="1.5" />
                ))}
                {COLS.map(c => graph.nodes[c.key].map(n => {
                  const p = pos[`${c.key}:${n.id}`];
                  return (
                    <g key={`${c.key}:${n.id}`} style={c.key === "application" ? { cursor: "pointer" } : undefined}
                       onClick={c.key === "application" ? () => { openApp?.(n.id); setPage("app-detail"); } : undefined}>
                      <circle cx={p.x} cy={p.y} r="11" fill={c.fill} stroke={c.stroke} strokeWidth="1.5" />
                      <text x={p.x} y={p.y + 24} textAnchor="middle" fill="var(--text2)" fontSize="8" fontFamily="var(--font-mono)">
                        {n.label && n.label.length > 16 ? n.label.slice(0, 15) + "…" : n.label}
                      </text>
                    </g>
                  );
                }))}
              </g>
            </svg>
          </div>
          {graph.hidden > 0 && <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8 }}>Showing {MAX_APPS} of {list.length} applications.</div>}
        </div>

        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>Suspicious Relationships</div>
          {triage.loading ? <div style={{ fontSize: 12, color: "var(--text3)" }}>Checking shared identities…</div>
            : triage.error ? <div style={{ fontSize: 12, color: "var(--text3)" }}>Could not run the Fraud Triage agent: {triage.error}</div>
            : proposals.filter(p => p.kind === "ring").length === 0 ? (
              <div style={{ display: "flex", gap: 8, padding: "10px 12px", background: "var(--green-tint)", borderRadius: 6, border: "1px solid var(--green-tint-strong)" }}>
                <Icon d={Icons.check} size={14} style={{ color: "var(--green)", flexShrink: 0, marginTop: 1 }} />
                <div style={{ fontSize: 12, color: "var(--text1)" }}>
                  No PAN, Aadhaar, bank account, file or face is shared between applications
                  <span style={{ color: "var(--text3)" }}> — {triage.data?.indexed_cases ?? 0} case(s) in the identity index.</span>
                </div>
              </div>
            ) : proposals.filter(p => p.kind === "ring").map(p => (
              <div key={p.id} style={{ display: "flex", gap: 8, padding: "8px 10px", background: "var(--red-tint)", borderRadius: 6, border: "1px solid var(--red-tint-strong)", marginBottom: 6 }}>
                <Icon d={Icons.alert} size={14} style={{ color: "var(--red)", flexShrink: 0 }} />
                <div>
                  <div style={{ fontSize: 12, color: "var(--text1)", fontWeight: 500 }}>{(p.reasons || [])[0] || "Linked applications"}</div>
                  <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)", marginTop: 2 }}>{(p.application_nos || []).join(" · ")}</div>
                </div>
              </div>
            ))}
        </div>
      </DataState>
    </div>
  );
}
