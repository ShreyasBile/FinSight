import { useEffect, useRef, useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { agentsApi, KIND_LABEL, OUTCOME_BADGE, OUTCOME_LABEL } from "../../utils/agents.js";

// Live view of every agent that worked on one application (case).
// Staff see agent, crew, kind, verdict, score/confidence, timing, model, tool
// calls and warnings. Borrowers see a plain "how we checked your documents".
// Every agent output is advisory evidence for a person — the UI never says
// "rejected".

const FLOW_LABEL = {
  extract: "Document read", integrity: "Integrity check", explain: "Plain-language explanation",
  locate: "Issue locations", mask: "Privacy masking", face: "Face match", store: "Application stored",
  assess: "Fraud & policy assessment", underwrite: "Underwriting", liveness: "Selfie liveness",
};

function ms(v) {
  if (v === null || v === undefined) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
}

function StepRow({ s, maxMs, staff }) {
  const [open, setOpen] = useState(false);
  const pct = maxMs ? Math.max(2, Math.min(100, ((s.duration_ms || 0) / maxMs) * 100)) : 0;
  const hosted = s.model?.provider === "anthropic";
  const detail = (s.tool_calls?.length || 0) + (s.warnings?.length || 0) + (s.llm_calls?.length || 0) > 0 || s.narrative;
  if (!staff) {
    return (
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 12 }}>
        <span style={{ color: "var(--text2)" }}>{s.agent_name}</span>
        <span style={{ color: s.status === "error" ? "var(--red)" : "var(--green)", fontSize: 11 }}>
          {s.status === "running" ? "working…" : s.status === "skipped" ? "not needed" : s.status === "error" ? "will be checked by our team" : "done"}
        </span>
      </div>
    );
  }
  return (
    <div style={{ borderBottom: "1px solid var(--border)", padding: "7px 0" }}>
      <div style={{ display: "grid", gridTemplateColumns: "26px 1.5fr 90px 110px 1.6fr 70px 16px", gap: 8, alignItems: "center", fontSize: 12, cursor: detail ? "pointer" : "default" }}
        onClick={() => detail && setOpen(!open)}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text3)" }}>#{s.agent_number}</span>
        <div>
          <div style={{ fontWeight: 600, color: "var(--text1)" }}>{s.agent_name}</div>
          <div style={{ fontSize: 10, color: "var(--text3)" }}>{s.crew?.replace("_", " ")} · {KIND_LABEL[s.kind]}</div>
        </div>
        <span>
          {s.status === "running"
            ? <span className="badge badge-blue">running</span>
            : s.status === "skipped" ? <span className="badge badge-gray">skipped</span>
            : s.status === "error" ? <span className="badge badge-red">error</span>
            : <span className={`badge ${OUTCOME_BADGE[s.ai_outcome] || "badge-gray"}`}>{OUTCOME_LABEL[s.ai_outcome] || "Info"}</span>}
        </span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text2)" }}>
          {s.score != null ? `score ${s.score.toFixed(2)}` : s.ai_confidence != null ? `conf ${s.ai_confidence.toFixed(2)}` : ""}
          {s.status === "degraded" && <span style={{ color: "var(--amber)" }}> degraded</span>}
        </span>
        <span style={{ fontSize: 11, color: "var(--text2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.summary}>
          {s.model && <span className={`badge ${hosted ? "badge-red" : "badge-purple"}`} style={{ fontSize: 9, marginRight: 4 }}>{s.model.model}</span>}
          {s.summary}
        </span>
        <div title={ms(s.duration_ms)}>
          <div style={{ height: 5, borderRadius: 3, background: "var(--surface2)" }}>
            <div style={{ height: 5, width: `${pct}%`, borderRadius: 3, background: s.status === "error" ? "var(--red)" : "var(--blue)" }} />
          </div>
          <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text3)", marginTop: 2 }}>{ms(s.duration_ms)}</div>
        </div>
        <span style={{ color: "var(--text3)" }}>{detail ? <Icon d={open ? Icons.chevronD : Icons.chevronR} size={11} /> : null}</span>
      </div>
      {open && (
        <div style={{ margin: "6px 0 2px 34px", fontSize: 11, color: "var(--text2)", lineHeight: 1.7 }}>
          {s.narrative && <div style={{ padding: "6px 8px", background: "var(--surface2)", borderRadius: 6, marginBottom: 6 }}>{s.narrative}</div>}
          {(s.llm_calls || []).map((c, i) => (
            <div key={`l${i}`} style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>
              LLM · {c.provider}/{c.model} · {ms(c.latency_ms)}{c.queue_wait_ms ? ` (queued ${ms(c.queue_wait_ms)})` : ""}{c.ok ? "" : ` · failed: ${c.error}`}
            </div>
          ))}
          {(s.tool_calls || []).length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
              {s.tool_calls.map((t, i) => (
                <span key={`t${i}`} className="badge badge-gray" style={{ fontSize: 9 }} title={t.detail || ""}>
                  {t.tool} · {ms(t.duration_ms)}{t.detail ? ` · ${t.detail}` : ""}
                </span>
              ))}
            </div>
          )}
          {(s.warnings || []).map((w, i) => <div key={`w${i}`} style={{ color: "var(--amber)" }}>⚠ {w}</div>)}
          {s.error && <div style={{ color: "var(--red)" }}>{s.error}</div>}
        </div>
      )}
    </div>
  );
}

export default function AgentActivityPanel({ caseId, role = "officer", title, live = true, defaultOpen = true }) {
  const staff = role !== "user";
  const [runs, setRuns] = useState([]);
  const [open, setOpen] = useState(defaultOpen);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  useEffect(() => {
    if (!caseId) return undefined;
    let stopped = false;
    const tick = async () => {
      try {
        const r = await agentsApi.runs({ case_id: caseId, limit: 60 });
        if (!stopped) { setRuns(r); setError(null); }
      } catch (e) {
        if (!stopped) setError(e.message);
      }
      if (!stopped && live) timer.current = setTimeout(tick, 2500);
    };
    tick();
    return () => { stopped = true; clearTimeout(timer.current); };
  }, [caseId, live]);

  const ordered = [...runs].sort((a, b) => (a.started_at < b.started_at ? -1 : 1));
  const steps = ordered.flatMap((r) => r.steps.map((s) => ({ ...s, flow: r.flow })));
  const maxMs = Math.max(1, ...steps.map((s) => s.duration_ms || 0));
  const llmCalls = steps.reduce((n, s) => n + (s.llm_calls?.length || 0), 0);
  const agentsUsed = new Set(steps.map((s) => s.agent_id)).size;

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <div>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Icon d={Icons.cpu} size={14} /> {title || (staff ? "Agent Activity" : "How we checked your documents")}
          </div>
          <div className="card-sub">
            {steps.length === 0 ? "No agent has run yet for this application."
              : staff ? `${agentsUsed} agents · ${steps.length} steps · ${llmCalls} LLM call(s) · all results are advisory — a person decides`
              : `${agentsUsed} automated checks ran on your documents. A person reviews everything before any decision.`}
          </div>
        </div>
        <Icon d={open ? Icons.chevronD : Icons.chevronR} size={14} />
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {error && <div style={{ fontSize: 12, color: "var(--red)" }}>Could not load agent activity: {error}</div>}
          {ordered.map((r) => (
            <div key={r.run_id} style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--text3)", fontFamily: "var(--font-mono)", margin: "6px 0 2px" }}>
                {FLOW_LABEL[r.flow] || r.flow} · {new Date(r.started_at).toLocaleTimeString()} {r.duration_ms != null && staff ? `· ${ms(r.duration_ms)}` : ""}
                {r.status === "degraded" && staff && <span style={{ color: "var(--amber)" }}> · degraded</span>}
              </div>
              {r.steps.map((s) => <StepRow key={s.step_id} s={s} maxMs={maxMs} staff={staff} />)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
