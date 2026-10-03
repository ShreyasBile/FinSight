import { useEffect, useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import { agentsApi, KIND_LABEL, OUTCOME_BADGE, OUTCOME_LABEL } from "../../utils/agents.js";
import FraudTriagePanel from "../../components/agents/FraudTriagePanel.jsx";

// Head Manager view of the agent layer: the 29 agents (what each does, which
// model it uses, how fast it is), the latest runs, the process-mining analyst,
// and fraud-triage proposals. Read-only — nothing here changes an application.

const CREW_LABEL = {
  intake: "Intake", privacy: "Privacy", fraud_evidence: "Identity & fraud evidence",
  compliance: "Compliance", underwriting: "Underwriting", ops: "Operations",
};
const ms = (v) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

function AgentsTab() {
  const [agents, setAgents] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    agentsApi.list().then(setAgents).catch(e => setError(e.message));
    agentsApi.health().then(setHealth).catch(() => setHealth(null));
  }, []);
  if (error) return <div className="card" style={{ color: "var(--red)", fontSize: 12 }}>{error}</div>;
  if (!agents) return <div className="card" style={{ fontSize: 12, color: "var(--text3)" }}>Loading agents…</div>;
  const crews = [...new Set(agents.map(a => a.crew))];
  return (
    <div>
      {health && (
        <div className="grid4" style={{ gap: 12, marginBottom: 16 }}>
          {[
            { label: "Agents implemented", value: `${health.implemented} / ${health.registered}` },
            { label: "CrewAI", value: String(health.crewai).slice(0, 16) },
            { label: "Local model server", value: health.ollama_reachable ? "reachable" : "offline",
              color: health.ollama_reachable ? "var(--green)" : "var(--red)" },
            { label: "SQL event sink", value: health.sql_sink?.state || (health.sql_sink?.enabled ? "on" : "off") },
          ].map(s => (
            <div key={s.label} className="stat-card">
              <div className="stat-label">{s.label}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: s.color || "var(--text1)" }}>{s.value}</div>
            </div>
          ))}
        </div>
      )}
      {crews.map(crew => (
        <div key={crew} className="card" style={{ marginBottom: 14 }}>
          <div className="card-title" style={{ marginBottom: 10 }}>{CREW_LABEL[crew] || crew}</div>
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th>#</th><th>Agent</th><th>Kind</th><th>Model</th><th>Runs</th><th>p50</th><th>p95</th><th>Flagged</th>
              </tr></thead>
              <tbody>
                {agents.filter(a => a.crew === crew).map(a => (
                  <tr key={a.id}>
                    <td style={{ fontFamily: "var(--font-mono)", color: "var(--text3)" }}>{a.number}</td>
                    <td style={{ whiteSpace: "normal", minWidth: 260 }}>
                      <div style={{ fontWeight: 600, color: "var(--text1)" }}>{a.name}
                        {!a.enabled && <span className="badge badge-gray" style={{ marginLeft: 6 }}>disabled</span>}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text3)" }}>{a.goal}</div>
                    </td>
                    <td><span className="badge badge-gray">{KIND_LABEL[a.kind] || a.kind}</span></td>
                    <td style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>{a.model ? a.model.model : "—"}</td>
                    <td>{a.stats?.runs ?? 0}</td>
                    <td>{ms(a.stats?.p50_ms)}</td>
                    <td>{ms(a.stats?.p95_ms)}</td>
                    <td>{a.stats?.flagged ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function RunsTab() {
  const [runs, setRuns] = useState(null);
  const [open, setOpen] = useState(null);
  useEffect(() => { agentsApi.runs({ limit: 40 }).then(setRuns).catch(() => setRuns([])); }, []);
  if (!runs) return <div className="card" style={{ fontSize: 12, color: "var(--text3)" }}>Loading runs…</div>;
  if (!runs.length) return <div className="card" style={{ fontSize: 12, color: "var(--text3)" }}>No agent runs since the server started.</div>;
  return (
    <div className="card">
      {runs.map(r => {
        const llm = r.steps.reduce((n, s) => n + (s.llm_calls?.length || 0), 0);
        const isOpen = open === r.run_id;
        return (
          <div key={r.run_id} style={{ borderBottom: "1px solid var(--border)", padding: "8px 0" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", flexWrap: "wrap" }}
                 onClick={() => setOpen(isOpen ? null : r.run_id)}>
              <Icon d={Icons.chevronR} size={12} style={{ transform: isOpen ? "rotate(90deg)" : "none", color: "var(--text3)" }} />
              <span className="badge badge-blue">{r.flow}</span>
              <span style={{ fontSize: 12, color: "var(--text1)", fontFamily: "var(--font-mono)" }}>{r.case_key || (r.case_id || "").slice(0, 8)}</span>
              <span style={{ fontSize: 11, color: "var(--text3)" }}>{new Date(r.started_at).toLocaleString()}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text3)" }}>
                {r.steps.length} step(s) · {llm} LLM call(s) · {ms(r.duration_ms)}
              </span>
            </div>
            {isOpen && (
              <div style={{ marginTop: 6, marginLeft: 22 }}>
                {r.steps.map(s => (
                  <div key={s.step_id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, padding: "3px 0" }}>
                    <span style={{ width: 170, color: "var(--text1)" }}>{s.agent_name || s.agent_id}</span>
                    {s.ai_outcome && <span className={`badge ${OUTCOME_BADGE[s.ai_outcome] || "badge-gray"}`}>{OUTCOME_LABEL[s.ai_outcome] || s.ai_outcome}</span>}
                    <span style={{ flex: 1, color: "var(--text2)" }}>{s.summary}</span>
                    <span style={{ color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{ms(s.duration_ms)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ProcessTab() {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const load = async (narrate) => {
    setBusy(true); setError(null);
    try { setRes(await agentsApi.processInsights({ narrate: narrate || undefined })); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(false); }, []);
  const o = res?.overview, cf = res?.conformance;
  return (
    <div>
      <div className="card" style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div>
            <div className="card-title">Process-Mining Analyst</div>
            <div className="card-sub">Every figure is a read-only query on the event log; the briefing may only restate them.</div>
          </div>
          <button className="btn btn-secondary btn-sm" style={{ marginLeft: "auto" }} disabled={busy} onClick={() => load(true)}>
            <Icon d={Icons.sparkles} size={12} /> {busy ? "Working…" : "Write a briefing"}
          </button>
        </div>
        {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 8 }}>{error}</div>}
        {res?.narrative && (
          <div style={{ fontSize: 12.5, color: "var(--text1)", lineHeight: 1.6, marginTop: 10, padding: "8px 10px",
                        background: "var(--surface2)", borderRadius: 6 }}>{res.narrative}</div>
        )}
        {res?.warnings?.length > 0 && !res?.narrative && (
          <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8 }}>{res.warnings.join(" · ")}</div>
        )}
        {(res?.findings || []).map((f, i) => (
          <div key={i} style={{ fontSize: 12, color: "var(--text2)", marginTop: 6 }}>• {f}</div>
        ))}
      </div>
      {o && (
        <div className="grid4" style={{ gap: 12, marginBottom: 14 }}>
          {[
            { label: "Cases", value: o.cases, sub: `${o.closed} closed` },
            { label: "Conformant paths", value: cf?.conformance_pct != null ? `${cf.conformance_pct}%` : "—", sub: `${cf?.variants} variants` },
            { label: "Straight-through", value: `${o.straight_through_pct}%`, sub: "no human touch" },
            { label: "Overdue now", value: res.stuck?.overdue_cases ?? 0, sub: "past stage target" },
          ].map(s => (
            <div key={s.label} className="stat-card">
              <div className="stat-label">{s.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text1)" }}>{s.value}</div>
              <div style={{ fontSize: 11, color: "var(--text3)" }}>{s.sub}</div>
            </div>
          ))}
        </div>
      )}
      {res?.available && (
        <div className="grid2">
          <div className="card">
            <div className="card-title" style={{ marginBottom: 8 }}>Biggest waits</div>
            {res.bottlenecks.map((b, i) => (
              <div key={i} style={{ fontSize: 12, padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                {b.from_activity} → {b.to_activity}
                <span style={{ float: "right", fontFamily: "var(--font-mono)", color: "var(--text3)" }}>
                  {Number(b.avg_wait_hours).toFixed(1)} h avg · {b.transition_count}×
                </span>
              </div>
            ))}
          </div>
          <div className="card">
            <div className="card-title" style={{ marginBottom: 8 }}>Top variants</div>
            {res.top_variants.map(v => (
              <div key={v.variant_id} style={{ fontSize: 11.5, padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                <span className={`badge ${v.is_conformant ? "badge-green" : "badge-amber"}`} style={{ marginRight: 6 }}>
                  {v.case_count} case(s)
                </span>
                <span style={{ color: "var(--text2)" }}>{String(v.activity_sequence).slice(0, 140)}</span>
              </div>
            ))}
            <div className="card-title" style={{ margin: "12px 0 8px" }}>Slowest agents (p95)</div>
            {res.agent_latency.map(a => (
              <div key={a.agent_id} style={{ fontSize: 12, display: "flex", justifyContent: "space-between" }}>
                <span>{a.agent_id}</span><span style={{ fontFamily: "var(--font-mono)" }}>{ms(a.p95_ms)} · {a.runs} runs</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {res && !res.available && <div className="card" style={{ fontSize: 12, color: "var(--text3)" }}>{res.reason}</div>}
    </div>
  );
}

export default function HeadAgentActivity({ setPage, openApp }) {
  const [tab, setTab] = useState("Agents");
  const tabs = ["Agents", "Recent runs", "Process Mining", "Fraud Triage"];
  return (
    <div>
      <PageHeader title="Agent Activity" sub="The 29 FinSight agents — what ran, how fast, on which model, and what they found" />
      <div className="tabs">
        {tabs.map(t => <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</div>)}
      </div>
      {tab === "Agents" && <AgentsTab />}
      {tab === "Recent runs" && <RunsTab />}
      {tab === "Process Mining" && <ProcessTab />}
      {tab === "Fraud Triage" && <FraudTriagePanel openApp={(no) => { openApp?.(no); setPage?.("app-detail"); }} />}
    </div>
  );
}
