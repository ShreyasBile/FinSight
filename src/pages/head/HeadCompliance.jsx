import { useState, useEffect, useCallback } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import EmptyState from "../../components/EmptyState.jsx";
import {
  getStats, listDocuments, ingestDocument, deleteDocument, extractRules,
  listRules, createRule, listCandidates, approveCandidate, rejectCandidate,
  getAudit,
} from "../../utils/compliance.js";

// Head-Manager-only console for the integrated RBI Compliance module:
// manage the regulatory knowledge base, author rules, and review extracted
// drafts (maker-checker: the extractor proposes, the Head Manager approves).

const TABS = ["Rules", "Review Queue", "Knowledge Base", "Audit Log"];

const TYPE_BADGE = { mandatory: "badge-red", internal_policy: "badge-amber", advisory: "badge-gray" };

export default function HeadCompliance() {
  const [tab, setTab] = useState("Rules");
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");

  const refreshStats = useCallback(() => {
    getStats().then(setStats).catch(e => setError(e.message));
  }, []);
  useEffect(() => { refreshStats(); }, [refreshStats]);

  return (
    <div>
      <PageHeader title="RBI Compliance" sub="Knowledge base, rules and draft review — regulations current as of 2026">
        {stats && (
          <>
            <span className="badge badge-blue">{stats.active_documents} documents</span>
            <span className="badge badge-green">{stats.active_rules} active rules</span>
            {stats.pending_candidates > 0 && (
              <span className="badge badge-amber">{stats.pending_candidates} drafts pending review</span>
            )}
          </>
        )}
      </PageHeader>

      {error && (
        <div className="alert-banner alert-red" style={{marginBottom:16}}>
          <Icon d={Icons.alert} size={15}/>
          <span>Compliance service unreachable: {error}. Is the backend running?</span>
        </div>
      )}

      <div className="tabs" style={{marginBottom:16}}>
        {TABS.map(t => (
          <button key={t} className={`tab ${tab===t?"active":""}`} onClick={()=>setTab(t)}>
            {t}{t==="Review Queue" && stats?.pending_candidates > 0 ? ` (${stats.pending_candidates})` : ""}
          </button>
        ))}
      </div>

      {tab === "Rules" && <RulesTab onChanged={refreshStats} />}
      {tab === "Review Queue" && <ReviewTab onChanged={refreshStats} />}
      {tab === "Knowledge Base" && <KbTab onChanged={refreshStats} />}
      {tab === "Audit Log" && <AuditTab />}
    </div>
  );
}

/* ---------------- Rules ---------------- */
function RulesTab({ onChanged }) {
  const [rules, setRules] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ rule_id:"", rule_name:"", category:"KYC", rule_type:"mandatory", regulation:"", condition:'{"field":"customer.kyc_status","op":"eq","value":"complete"}' });
  const [err, setErr] = useState("");

  const load = () => listRules().then(setRules).catch(e => setErr(e.message));
  useEffect(() => { load(); }, []);  // wrap: load() returns a Promise, which useEffect must not receive as cleanup

  const submit = async () => {
    setErr("");
    let cond;
    try { cond = JSON.parse(form.condition); }
    catch (e) { return setErr("Condition is not valid JSON: " + e.message); }
    if (!form.rule_id.trim() || !form.rule_name.trim()) return setErr("Rule ID and name are required.");
    // the server enforces these too (a live rule runs against every application)
    if (!/^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$/.test(form.rule_id.trim())) return setErr("Rule ID must look like KYC_003 (capital letters and digits, parts joined by underscores).");
    if (form.rule_name.trim().split(/\s+/).length < 2 || form.rule_name.trim().length < 10) return setErr("Rule name must say what the rule enforces (at least two words).");
    if (form.rule_type === "mandatory" && form.regulation.trim().length < 5) return setErr("A mandatory rule can block approvals — cite the regulation or policy it enforces.");
    try {
      await createRule({ ...form, rule_id: form.rule_id.trim(), rule_name: form.rule_name.trim(), condition: cond });
      setShowForm(false);
      setForm(f => ({ ...f, rule_id:"", rule_name:"", regulation:"" }));
      load(); onChanged();
    } catch (e) { setErr(e.message); }
  };

  if (!rules) return <div className="card" style={{color:"var(--text3)",fontSize:13}}>Loading rules…</div>;
  return (
    <div>
      <div style={{display:"flex",justifyContent:"flex-end",marginBottom:12}}>
        <button className="btn btn-primary" onClick={()=>setShowForm(s=>!s)}>
          <Icon d={Icons.plus} size={13}/> New Rule
        </button>
      </div>
      {showForm && (
        <div className="card" style={{marginBottom:16}}>
          <div className="card-title" style={{marginBottom:12}}>Create Compliance Rule</div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10}}>
            <label style={{fontSize:11,color:"var(--text3)"}}>Rule ID
              <input className="form-input" value={form.rule_id} onChange={e=>setForm({...form,rule_id:e.target.value})} placeholder="e.g. KYC_003"/></label>
            <label style={{fontSize:11,color:"var(--text3)"}}>Severity
              <select className="form-input form-select" value={form.rule_type} onChange={e=>setForm({...form,rule_type:e.target.value})}>
                <option value="mandatory">mandatory (blocks approval)</option>
                <option value="internal_policy">internal_policy (warning)</option>
                <option value="advisory">advisory</option>
              </select></label>
            <label style={{fontSize:11,color:"var(--text3)",gridColumn:"1 / -1"}}>Rule name
              <input className="form-input" value={form.rule_name} onChange={e=>setForm({...form,rule_name:e.target.value})} placeholder="What this rule enforces"/></label>
            <label style={{fontSize:11,color:"var(--text3)"}}>Category
              <select className="form-input form-select" value={form.category} onChange={e=>setForm({...form,category:e.target.value})}>
                {["KYC","Digital Lending","Fair Practices","Credit Risk","Fraud Risk","General"].map(c=><option key={c}>{c}</option>)}
              </select></label>
            <label style={{fontSize:11,color:"var(--text3)"}}>Regulation cited
              <input className="form-input" value={form.regulation} onChange={e=>setForm({...form,regulation:e.target.value})} placeholder="e.g. RBI (Digital Lending) Directions, 2025"/></label>
            <label style={{fontSize:11,color:"var(--text3)",gridColumn:"1 / -1"}}>Condition (JSON)
              <textarea className="form-input" rows={3} style={{fontFamily:"var(--font-mono)",fontSize:11}}
                value={form.condition} onChange={e=>setForm({...form,condition:e.target.value})}/></label>
          </div>
          {err && <div style={{fontSize:12,color:"var(--red)",marginTop:8}}>{err}</div>}
          <div style={{display:"flex",gap:8,marginTop:12}}>
            <button className="btn btn-primary" onClick={submit}>Create Rule</button>
            <button className="btn btn-ghost" onClick={()=>setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}
      {err && !showForm && <div style={{fontSize:12,color:"var(--red)",marginBottom:8}}>{err}</div>}
      <div style={{display:"flex",flexDirection:"column",gap:10}}>
        {rules.map(r => (
          <div key={r.rule_id} className="card" style={{padding:"12px 16px"}}>
            <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}>
              <div>
                <span style={{fontFamily:"var(--font-mono)",fontSize:10,color:"var(--text3)"}}>{r.rule_id}</span>
                <div style={{fontSize:13,fontWeight:600,color:"var(--text1)"}}>{r.rule_name}</div>
              </div>
              <span className={`badge ${TYPE_BADGE[r.rule_type]||"badge-gray"}`}>{r.rule_type.replace(/_/g," ")}</span>
            </div>
            <div style={{fontSize:11,color:"var(--blue)",marginTop:4}}>
              📘 {r.regulation || "—"}{r.regulation_ref ? ` — ${r.regulation_ref}` : ""}
              <span style={{color:"var(--text3)",marginLeft:8}}>· origin: {r.origin || "seed"}</span>
            </div>
            <details style={{marginTop:6}}>
              <summary style={{fontSize:11,color:"var(--text3)",cursor:"pointer"}}>condition</summary>
              <pre style={{fontSize:10.5,fontFamily:"var(--font-mono)",background:"var(--surface2)",borderRadius:6,padding:8,marginTop:4,whiteSpace:"pre-wrap",wordBreak:"break-word",color:"var(--text2)"}}>{JSON.stringify(r.condition,null,2)}</pre>
              {r.applies_when && <div style={{fontSize:10.5,color:"var(--text3)",marginTop:4}}>applies when: <code>{JSON.stringify(r.applies_when)}</code></div>}
            </details>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- Review Queue (maker-checker) ---------------- */
function ReviewTab({ onChanged }) {
  const [cands, setCands] = useState(null);
  const [edits, setEdits] = useState({});
  const [err, setErr] = useState("");

  const load = () => listCandidates("pending").then(setCands).catch(e => setErr(e.message));
  useEffect(() => { load(); }, []);  // wrap: load() returns a Promise, which useEffect must not receive as cleanup

  const edit = (id, field, value) => setEdits(p => ({ ...p, [id]: { ...p[id], [field]: value } }));
  const val = (c, field, fallback) => edits[c.id]?.[field] ?? fallback;

  const approve = async (c) => {
    setErr("");
    let cond;
    try { cond = JSON.parse(val(c, "condition", JSON.stringify(c.proposed_condition))); }
    catch (e) { return setErr(`Draft #${c.id}: condition is not valid JSON — ${e.message}`); }
    try {
      await approveCandidate(c.id, {
        reviewer: "head_manager",
        rule_id: val(c, "rule_id", `EXT_${String(c.id).padStart(4,"0")}`).trim(),
        rule_name: val(c, "rule_name", c.proposed_rule_name).trim(),
        rule_type: val(c, "rule_type", c.proposed_rule_type),
        condition: cond,
      });
      load(); onChanged();
    } catch (e) { setErr(`Draft #${c.id}: ${e.message}`); }
  };

  const reject = async (c) => {
    // No blocking dialog: rejection is reversible in spirit (the draft stays
    // in the DB with status='rejected' and shows in the audit trail).
    try { await rejectCandidate(c.id, { reviewer: "head_manager", note: "" }); load(); onChanged(); }
    catch (e) { setErr(e.message); }
  };

  if (!cands) return <div className="card" style={{color:"var(--text3)",fontSize:13}}>Loading review queue…</div>;
  if (cands.length === 0) return (
    <div className="card">
      <EmptyState icon={Icons.check} title="No drafts pending review"
        sub="Use “Extract rules” on a Knowledge Base document to generate draft rules for approval." />
    </div>
  );
  return (
    <div>
      <div className="alert-banner alert-blue" style={{marginBottom:14}}>
        <Icon d={Icons.info} size={15}/>
        <span>Extracted drafts are <strong>not active</strong>. Map each condition's placeholder to a real application field, confirm the severity, then approve — approval is the “checker” step and is written to the audit log.</span>
      </div>
      {err && <div style={{fontSize:12,color:"var(--red)",marginBottom:10}}>{err}</div>}
      <div style={{display:"flex",flexDirection:"column",gap:14}}>
        {cands.map(c => (
          <div key={c.id} className="card">
            <div style={{display:"flex",justifyContent:"space-between",gap:10,marginBottom:8}}>
              <span style={{fontSize:10.5,color:"var(--text3)",fontFamily:"var(--font-mono)"}}>
                draft #{c.id} · via {c.extraction_method} · confidence {c.extraction_confidence}
              </span>
              {c.needs_field_mapping
                ? <span className="badge badge-purple">needs field mapping</span>
                : <span className="badge badge-green">field suggested</span>}
            </div>
            <div style={{fontSize:12.5,fontStyle:"italic",borderLeft:"2px solid var(--blue)",paddingLeft:10,marginBottom:10,color:"var(--text2)"}}>
              “{c.obligation_text}”
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10}}>
              <label style={{fontSize:11,color:"var(--text3)"}}>Rule ID
                <input className="form-input" style={{fontFamily:"var(--font-mono)"}}
                  value={val(c,"rule_id",`EXT_${String(c.id).padStart(4,"0")}`)}
                  onChange={e=>edit(c.id,"rule_id",e.target.value)}/></label>
              <label style={{fontSize:11,color:"var(--text3)"}}>Severity
                <select className="form-input form-select" value={val(c,"rule_type",c.proposed_rule_type)}
                  onChange={e=>edit(c.id,"rule_type",e.target.value)}>
                  <option value="mandatory">mandatory</option>
                  <option value="internal_policy">internal_policy</option>
                  <option value="advisory">advisory</option>
                </select></label>
              <label style={{fontSize:11,color:"var(--text3)",gridColumn:"1 / -1"}}>Rule name
                <input className="form-input" value={val(c,"rule_name",c.proposed_rule_name)}
                  onChange={e=>edit(c.id,"rule_name",e.target.value)}/></label>
              <label style={{fontSize:11,color:"var(--text3)",gridColumn:"1 / -1"}}>
                Condition (map the placeholder to a real field, e.g. <code>loan_terms.cooling_off_period_days</code>)
                <textarea className="form-input" rows={2} style={{fontFamily:"var(--font-mono)",fontSize:11}}
                  value={val(c,"condition",JSON.stringify(c.proposed_condition))}
                  onChange={e=>edit(c.id,"condition",e.target.value)}/></label>
            </div>
            <div style={{fontSize:11,color:"var(--blue)",marginTop:6}}>
              📘 {c.proposed_regulation || "—"}
            </div>
            <div style={{display:"flex",gap:8,marginTop:10}}>
              <button className="btn btn-primary" onClick={()=>approve(c)}>Approve → activate</button>
              <button className="btn btn-ghost" onClick={()=>reject(c)}>Reject</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- Knowledge Base ---------------- */
function KbTab({ onChanged }) {
  const [docs, setDocs] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title:"", category:"Digital Lending", source_type:"RBI", text:"" });
  const [busy, setBusy] = useState({});
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");

  const load = () => listDocuments().then(setDocs).catch(e => setErr(e.message));
  useEffect(() => { load(); }, []);  // wrap: load() returns a Promise, which useEffect must not receive as cleanup

  const ingest = async () => {
    setErr("");
    if (!form.title.trim() || !form.text.trim()) return setErr("Title and regulatory text are required.");
    try {
      await ingestDocument({ ...form, title: form.title.trim() });
      setShowForm(false); setForm(f => ({ ...f, title:"", text:"" }));
      load(); onChanged();
    } catch (e) { setErr(e.message); }
  };

  const extract = async (doc) => {
    setErr(""); setNotice("");
    setBusy(b => ({ ...b, [doc.id]: true }));
    try {
      const r = await extractRules(doc.id, true);
      setNotice(r.candidates > 0
        ? `Extracted ${r.candidates} draft rule(s) from “${doc.title}” via ${r.method}. They are drafts — review them in the Review Queue tab.`
        : `No obligations found to extract from “${doc.title}”.`);
      onChanged();
    } catch (e) { setErr(e.message); }
    finally { setBusy(b => ({ ...b, [doc.id]: false })); }
  };

  const remove = async (doc) => {
    if (!window.confirm(`Remove “${doc.title}” from the knowledge base?`)) return;
    try { await deleteDocument(doc.id); load(); onChanged(); }
    catch (e) { setErr(e.message); }
  };

  if (!docs) return <div className="card" style={{color:"var(--text3)",fontSize:13}}>Loading knowledge base…</div>;
  const active = docs.filter(d => d.status === "active");
  return (
    <div>
      <div style={{display:"flex",justifyContent:"flex-end",marginBottom:12}}>
        <button className="btn btn-primary" onClick={()=>setShowForm(s=>!s)}>
          <Icon d={Icons.plus} size={13}/> Add Regulatory Document
        </button>
      </div>
      {notice && <div className="alert-banner alert-green" style={{marginBottom:12}}><Icon d={Icons.check} size={15}/><span>{notice}</span></div>}
      {err && <div style={{fontSize:12,color:"var(--red)",marginBottom:10}}>{err}</div>}
      {showForm && (
        <div className="card" style={{marginBottom:16}}>
          <div className="card-title" style={{marginBottom:12}}>Ingest Regulatory Text</div>
          <div style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr",gap:10,marginBottom:10}}>
            <input className="form-input" placeholder="Document title (e.g. RBI Circular on Gold Loans, 2026)"
              value={form.title} onChange={e=>setForm({...form,title:e.target.value})}/>
            <select className="form-input form-select" value={form.category} onChange={e=>setForm({...form,category:e.target.value})}>
              {["Digital Lending","KYC","Fair Practices","Credit Risk","General"].map(c=><option key={c}>{c}</option>)}
            </select>
            <select className="form-input form-select" value={form.source_type} onChange={e=>setForm({...form,source_type:e.target.value})}>
              {["RBI","Internal NBFC","Circular"].map(c=><option key={c}>{c}</option>)}
            </select>
          </div>
          <textarea className="form-input" rows={5} placeholder="Paste the regulatory text here — it will be chunked and indexed for retrieval, and can then be used for rule extraction."
            value={form.text} onChange={e=>setForm({...form,text:e.target.value})}/>
          <div style={{display:"flex",gap:8,marginTop:10}}>
            <button className="btn btn-primary" onClick={ingest}>Ingest</button>
            <button className="btn btn-ghost" onClick={()=>setShowForm(false)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Title</th><th>Category</th><th>Source</th><th>Effective</th><th>Passages</th><th></th></tr></thead>
            <tbody>
              {active.map(d => (
                <tr key={d.id}>
                  <td style={{fontSize:12,fontWeight:600}}>{d.title}</td>
                  <td><span className="badge badge-blue">{d.category}</span></td>
                  <td style={{fontSize:11,color:"var(--text3)"}}>{d.source_type}</td>
                  <td style={{fontSize:11,color:"var(--text3)",fontFamily:"var(--font-mono)"}}>{d.effective_date || "—"}</td>
                  <td style={{fontSize:12,fontFamily:"var(--font-mono)"}}>{d.chunk_count || 0}</td>
                  <td style={{whiteSpace:"nowrap",textAlign:"right"}}>
                    <button className="btn btn-ghost" style={{fontSize:11,padding:"3px 8px",marginRight:6}}
                      disabled={!!busy[d.id]} onClick={()=>extract(d)}>
                      {busy[d.id] ? "Extracting…" : "Extract rules"}
                    </button>
                    <button className="btn btn-ghost" style={{fontSize:11,padding:"3px 8px",color:"var(--red)"}}
                      onClick={()=>remove(d)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Audit Log ---------------- */
function AuditTab() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { getAudit(100).then(setRows).catch(e => setErr(e.message)); }, []);

  if (err) return <div style={{fontSize:12,color:"var(--red)"}}>{err}</div>;
  if (!rows) return <div className="card" style={{color:"var(--text3)",fontSize:13}}>Loading audit log…</div>;
  return (
    <div className="card">
      {rows.length === 0
        ? <EmptyState icon={Icons.clipboardList} title="No audit entries yet"
            sub="Rule creation, extraction, approvals and rejections are recorded here." />
        : <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
              <tbody>
                {rows.map(a => (
                  <tr key={a.id}>
                    <td style={{fontSize:10.5,color:"var(--text3)",fontFamily:"var(--font-mono)",whiteSpace:"nowrap"}}>{a.ts}</td>
                    <td style={{fontSize:11}}>{a.actor}</td>
                    <td><span className={`badge ${a.action.includes("reject")?"badge-red":a.action.includes("approv")||a.action.includes("created")?"badge-green":"badge-blue"}`}>{a.action}</span></td>
                    <td style={{fontSize:10.5,fontFamily:"var(--font-mono)"}}>{a.entity_type}:{a.entity_id}</td>
                    <td style={{fontSize:10.5,color:"var(--text3)",fontFamily:"var(--font-mono)"}}>{JSON.stringify(a.detail)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
    </div>
  );
}
