import { useState, useEffect } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import RegulatoryChat from "../components/RegulatoryChat.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { listDocuments } from "../utils/compliance.js";

// Policy Compliance Engine (head view) — live, backed by the integrated RBI
// compliance module. The query box is the real regulatory chat; the document
// library lists the actual knowledge base. Managing the KB (ingest, extract
// rules, delete) happens on the RBI Compliance page — linked from here.

export default function PagePolicy({ setPage }) {
  const [docs, setDocs] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    listDocuments().then(setDocs).catch(e => setErr(e.message));
  }, []);

  const active = docs ? docs.filter(d => d.status === "active") : null;

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Policy Compliance Engine</div>
          <div className="page-sub">RAG-powered retrieval over the RBI regulatory knowledge base</div>
        </div>
        <button className="btn btn-primary" onClick={()=>setPage && setPage("compliance")}>
          <Icon d={Icons.compliance} size={13}/> Manage Knowledge Base & Rules
        </button>
      </div>
      <div className="grid2" style={{ marginBottom:16 }}>
        <div className="card">
          <div className="card-title" style={{ marginBottom:10 }}>AI Policy Query</div>
          <RegulatoryChat height={480}/>
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom:14 }}>Policy Document Library</div>
          {err && (
            <div className="alert-banner alert-red">
              <Icon d={Icons.alert} size={15}/>
              <span>Compliance service unreachable: {err}</span>
            </div>
          )}
          {!err && !active && <div style={{fontSize:12,color:"var(--text3)"}}>Loading documents…</div>}
          {active && active.length === 0 && (
            <EmptyState icon={Icons.policy} title="No documents in the knowledge base"
              sub="Add regulatory text on the RBI Compliance page." />
          )}
          {active && active.map(d => (
            <div key={d.id} style={{ display:"flex", gap:10, padding:"8px 0", borderBottom:"1px solid var(--border)", alignItems:"center" }}>
              <Icon d={Icons.policy} size={14} style={{ color:"var(--blue)", flexShrink:0 }} />
              <div style={{ flex:1 }}>
                <div style={{ fontSize:12, color:"var(--text1)", fontWeight:500 }}>{d.title}</div>
                <div style={{ fontSize:10, color:"var(--text3)", fontFamily:"var(--font-mono)" }}>
                  {d.source_type} · effective {d.effective_date || "—"} · {d.chunk_count || 0} passages indexed
                </div>
              </div>
              <span className="badge badge-blue">{d.category}</span>
              <span className="badge badge-green">Active</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
