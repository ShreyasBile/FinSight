// Fraud Fusion result: one advisory score, its components, floors and evidence.
const TIER_BADGE = { LOW: "badge-green", MEDIUM: "badge-amber", HIGH: "badge-red" };
const COMP_LABEL = {
  tampering: "Document tampering", face: "Face match", cross_check: "Cross-document checks",
  validation: "Field validation", dedupe: "Duplicate identity", patterns: "Statement patterns",
};
const FLOOR_LABEL = {
  face_mismatch_confident: "Confident face mismatch",
  exact_duplicate_identity: "Duplicate identity / reused file",
  tamper_high: "A document's tampering tier is HIGH",
};

export default function FraudAssessmentView({ fraud, compact = false }) {
  if (!fraud) return null;
  const comps = Object.entries(fraud.components || {});
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span className={`badge ${TIER_BADGE[fraud.tier]}`} style={{ fontSize: 12 }}>Fraud risk {fraud.tier}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>{fraud.fused_score.toFixed(2)}</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>
          weighted average {fraud.weighted_average?.toFixed(2)} · weights {fraud.weights_version}
          {fraud.dominant_component ? ` · driven by ${COMP_LABEL[fraud.dominant_component] || fraud.dominant_component}` : ""}
        </span>
      </div>
      {fraud.floors?.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--red)", marginBottom: 8 }}>
          Floor applied: {fraud.floors.map((f) => FLOOR_LABEL[f] || f).join(" + ")}
          {fraud.floors.length >= 2 ? " (two floors → HIGH)" : " (one floor → at least MEDIUM)"}
        </div>
      )}
      {comps.map(([k, c]) => (
        <div key={k} style={{ display: "grid", gridTemplateColumns: "170px 1fr 50px", gap: 10, alignItems: "center", padding: "5px 0", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 12, color: c.available ? "var(--text1)" : "var(--text3)" }}>
            {COMP_LABEL[k] || k}
            <div style={{ fontSize: 10, color: "var(--text3)" }}>weight {Math.round((c.weight || 0) * 100)}%</div>
          </div>
          <div>
            {c.available ? (
              <div style={{ height: 6, borderRadius: 3, background: "var(--surface2)" }}>
                <div style={{ height: 6, borderRadius: 3, width: `${Math.max(2, (c.score || 0) * 100)}%`,
                  background: c.score >= 0.6 ? "var(--red)" : c.score >= 0.3 ? "var(--amber)" : "var(--green)" }} />
              </div>
            ) : <span style={{ fontSize: 11, color: "var(--text3)" }}>unavailable</span>}
            {!compact && (
              <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 3 }}>{(c.evidence || []).slice(0, 3).join(" · ")}</div>
            )}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, textAlign: "right" }}>{c.available ? c.score.toFixed(2) : "—"}</div>
        </div>
      ))}
      <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 8 }}>
        Advisory evidence for a reviewer — the fraud score never rejects an application on its own.
      </div>
    </div>
  );
}
