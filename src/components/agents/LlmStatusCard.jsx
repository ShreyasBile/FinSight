import { useEffect, useState } from "react";
import { llmSettings } from "../../utils/agents.js";

// Read-only view of the AI model the agents run on (officer settings page).
// Only the Head Manager can change it — see PageSettings → AI Models.
export default function LlmStatusCard() {
  const [cfg, setCfg] = useState(null);
  const [error, setError] = useState(null);
  const [showActivity, setShowActivity] = useState(() => {
    try { return localStorage.getItem("finsight.showAgentActivity") !== "0"; } catch { return true; }
  });
  useEffect(() => { llmSettings.get().then(setCfg).catch((e) => setError(e.message)); }, []);

  const toggle = () => {
    const next = !showActivity;
    setShowActivity(next);
    try { localStorage.setItem("finsight.showAgentActivity", next ? "1" : "0"); } catch { /* private mode */ }
  };

  const extraction = cfg?.effective?.find((e) => e.agent_id === "extraction");
  const hosted = cfg?.effective?.filter((e) => e.hosted) || [];
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="section-head">AI assistant</div>
      {error && <div style={{ fontSize: 12, color: "var(--red)" }}>Could not load: {error}</div>}
      {!cfg && !error && <div style={{ fontSize: 12, color: "var(--text3)" }}>Loading…</div>}
      {cfg && (
        <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.8 }}>
          <div>Document reading: <b style={{ fontFamily: "var(--font-mono)" }}>{extraction?.model}</b> ({extraction?.provider === "ollama" ? "runs on this machine" : extraction?.provider})</div>
          <div>{hosted.length === 0
            ? "All agents run locally — no applicant data leaves this machine."
            : <span style={{ color: "var(--red)" }}>{hosted.length} agent(s) use a hosted model: {hosted.map((h) => h.label).join(", ")}.</span>}
          </div>
          <div style={{ color: "var(--text3)", fontSize: 11 }}>Set by the Head Manager.</div>
        </div>
      )}
      <div style={{ padding: "9px 0 0", marginTop: 8, borderTop: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 13, color: "var(--text2)" }}>Show agent activity on Review &amp; Submit</span>
        <div onClick={toggle} style={{ width: 36, height: 20, borderRadius: 10, background: showActivity ? "var(--green)" : "var(--surface2)", cursor: "pointer", display: "flex", alignItems: "center", padding: "0 3px", justifyContent: showActivity ? "flex-end" : "flex-start" }}>
          <div style={{ width: 14, height: 14, borderRadius: "50%", background: "white" }} />
        </div>
      </div>
    </div>
  );
}
