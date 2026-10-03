import { useEffect, useState } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import { api } from "../utils/api.js";

// Opens one stored document in an overlay. The API decides which copy the viewer
// gets: an officer only ever receives the masked render (PAN / Aadhaar numbers
// redacted); the Head and the borrower who applied get the original.
export default function DocumentViewer({ doc, session, onClose }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    if (!doc) return undefined;
    let url = null;
    let alive = true;
    setState({ loading: true });
    fetch(api.documentUrl(doc.document_id, session?.user_id), { cache: "no-store" })
      .then(async r => {
        if (!r.ok) {
          let msg = `${r.status} ${r.statusText}`;
          try { msg = (await r.json()).detail || msg; } catch { /* keep status */ }
          throw new Error(msg);
        }
        const blob = await r.blob();
        url = URL.createObjectURL(blob);
        if (alive) setState({ url, type: blob.type || r.headers.get("content-type") || "" });
      })
      .catch(e => alive && setState({ error: e.message }));
    const onKey = e => e.key === "Escape" && onClose?.();
    window.addEventListener("keydown", onKey);
    return () => { alive = false; window.removeEventListener("keydown", onKey); if (url) URL.revokeObjectURL(url); };
  }, [doc, session?.user_id, onClose]);

  if (!doc) return null;
  const masked = session?.role_code === "officer";
  return (
    <div onClick={onClose} role="dialog" aria-label={doc.doc_label}
         style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 1000,
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div onClick={e => e.stopPropagation()} className="card"
           style={{ width: "min(960px, 100%)", maxHeight: "100%", display: "flex", flexDirection: "column", padding: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px",
                      borderBottom: "1px solid var(--border)" }}>
          <Icon d={Icons.fileText} size={16} style={{ color: "var(--blue)" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text1)" }}>{doc.doc_label}</div>
            <div style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{doc.file_name}</div>
          </div>
          <span className={`badge ${masked ? "badge-amber" : "badge-blue"}`} style={{ fontSize: 10 }}>
            {masked ? "Masked copy — PAN / Aadhaar hidden" : "Original"}
          </span>
          <button className="btn btn-ghost" onClick={onClose} style={{ fontSize: 12 }} aria-label="Close">
            <Icon d={Icons.x} size={13} />
          </button>
        </div>
        <div style={{ flex: 1, overflow: "auto", background: "var(--surface2)", minHeight: 300,
                      display: "flex", alignItems: "center", justifyContent: "center" }}>
          {state.loading && <span style={{ fontSize: 12, color: "var(--text3)" }}>
            <span className="spinner" style={{ width: 12, height: 12, marginRight: 6 }} />Loading document…</span>}
          {state.error && <div style={{ fontSize: 12, color: "var(--text2)", padding: 24, textAlign: "center" }}>
            <Icon d={Icons.info} size={18} style={{ color: "var(--amber)", marginBottom: 8 }} /><br />{state.error}</div>}
          {state.url && state.type.includes("pdf") && (
            <iframe title={doc.doc_label} src={state.url} style={{ width: "100%", height: "75vh", border: 0 }} />
          )}
          {state.url && !state.type.includes("pdf") && (
            <img src={state.url} alt={doc.doc_label} style={{ maxWidth: "100%", maxHeight: "75vh", objectFit: "contain" }} />
          )}
        </div>
      </div>
    </div>
  );
}
