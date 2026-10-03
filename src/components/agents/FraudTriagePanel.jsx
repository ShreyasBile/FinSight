import { useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { agentsApi } from "../../utils/agents.js";

// Fraud Triage agent: PROPOSALS only. It links applications that share an
// identity key they should not share (one face under several PANs, one PAN
// with different faces, one bank account or one file across PANs) and flags
// applications whose own evidence warrants a case. Opening a fraud case stays
// a human action; repeat applicants (same PAN and same face) are never
// proposed as rings.

const SEV_BADGE = { high: "badge-red", medium: "badge-amber" };
const LINK_LABEL = {
  face_across_ids: "same face, different PAN", id_different_face: "same PAN, different face",
  shared_account: "same bank account", shared_file: "same file",
};

export function ProposalCard({ p, openApp }) {
  return (
    <div className="card card-sm">
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
        <span className="badge badge-purple">{p.proposal_id}</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text1)" }}>
          {p.kind === "ring" ? (p.size >= 3 ? `Possible ring · ${p.size} applications` : "Linked pair of applications")
                             : "Open a fraud case"}
        </span>
        <span className={`badge ${SEV_BADGE[p.severity] || "badge-gray"}`} style={{ textTransform: "capitalize" }}>{p.severity}</span>
        {p.confidence != null && (
          <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
            {Math.round(p.confidence * 100)}% confidence
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        {(p.link_types || []).map(k => <span key={k} className="badge badge-gray">{LINK_LABEL[k] || k}</span>)}
        {(p.floors || []).map(f => <span key={f} className="badge badge-red">{f.replaceAll("_", " ")}</span>)}
      </div>
      {(p.reasons || []).slice(0, 4).map((r, i) => (
        <div key={i} style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.5 }}>• {r}</div>
      ))}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8, alignItems: "center" }}>
        {(p.application_nos || []).map(a => (
          <button key={a} className="btn btn-ghost" style={{ fontSize: 11, padding: "2px 8px", fontFamily: "var(--font-mono)" }}
                  onClick={() => openApp?.(a)}>{a}</button>
        ))}
        {!(p.application_nos || []).length && (
          <span style={{ fontSize: 11, color: "var(--text3)" }}>{p.case_ids.length} draft case(s) not yet submitted</span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text3)" }}>{p.suggested_action}</span>
      </div>
    </div>
  );
}

export default function FraudTriagePanel({ openApp, filterApplication }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setBusy(true); setError(null);
    try {
      setRes(await agentsApi.triage({ limit: 30 }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const props = (res?.proposals || []).filter(p => !filterApplication || (p.application_nos || []).includes(filterApplication));
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Icon d={Icons.fraud} size={14} /> AI proposals · Fraud Triage agent
          </div>
          <div className="card-sub">
            Rings and cases proposed from shared identities and fused fraud scores — you decide what to open.
          </div>
        </div>
        <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto" }} disabled={busy} onClick={run}>
          <Icon d={Icons.zap} size={12} /> {busy ? "Scanning…" : res ? "Re-scan" : "Scan all applications"}
        </button>
      </div>
      {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 10 }}>{error}</div>}
      {res && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, color: "var(--text3)", marginBottom: 10 }}>
            {res.cases_scanned} case file(s) scanned · {res.indexed_cases} in the identity index · {res.links} suspicious link(s)
            {res.skipped_already_open ? ` · ${res.skipped_already_open} application(s) already have an open case` : ""}
          </div>
          {props.length === 0
            ? <div style={{ fontSize: 12, color: "var(--text2)" }}>No proposals{filterApplication ? " involving this application" : ""}.</div>
            : <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {props.map(p => <ProposalCard key={p.proposal_id} p={p} openApp={openApp} />)}
              </div>}
        </div>
      )}
    </div>
  );
}
