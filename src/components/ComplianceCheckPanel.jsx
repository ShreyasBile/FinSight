import { useState, useEffect } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import { checkApplication } from "../utils/compliance.js";
import { agentsApi } from "../utils/agents.js";

// Runs an application through the RBI compliance engine and renders the
// per-rule comparison with regulation citations. With `applicationNo` it asks
// the Policy Check AGENT (POST /agents/policy/check), which builds the rules'
// input from real evidence (documents, tampering, face, fraud fusion) and
// leaves unverified fields out — so they show as "needs info" instead of the
// hardcoded passes the pages used to send. `application` (a raw payload) still
// works for ad-hoc checks.

const STATUS_STYLE = {
  PASS:           { badge: "badge-green",  label: "PASS" },
  FAIL:           { badge: "badge-red",    label: "FAIL" },
  WARNING:        { badge: "badge-amber",  label: "WARNING" },
  INCONCLUSIVE:   { badge: "badge-purple", label: "INCONCLUSIVE" },
  NOT_APPLICABLE: { badge: "badge-gray",   label: "N/A" },
};
const OVERALL_STYLE = {
  PASS:               { cls: "alert-green", label: "Compliant" },
  PASS_WITH_WARNINGS: { cls: "alert-amber", label: "Compliant with warnings" },
  FAIL:               { cls: "alert-red",   label: "Non-compliant" },
  NEEDS_INFO:         { cls: "alert-blue",  label: "Needs more information" },
};
const ORDER = { FAIL: 0, WARNING: 1, INCONCLUSIVE: 2, PASS: 3, NOT_APPLICABLE: 4 };

export default function ComplianceCheckPanel({ application, applicationNo, extra }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const run = () => {
    setLoading(true); setError("");
    (applicationNo ? agentsApi.policyCheck({ application_no: applicationNo, extra }) : checkApplication(application))
      .then(setResult)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(run, []); // application payload is static per profile view

  if (loading) return (
    <div className="card" style={{display:"flex",alignItems:"center",gap:10,fontSize:13,color:"var(--text3)"}}>
      <span className="spinner" style={{width:14,height:14}}/> Checking this applicant against the active RBI compliance ruleset…
    </div>
  );
  if (error) return (
    <div className="card">
      <div className="alert-banner alert-red" style={{marginBottom:10}}>
        <Icon d={Icons.alert} size={15}/>
        <span>Compliance service unreachable: {error}</span>
      </div>
      <button className="btn btn-ghost" onClick={run}>Retry</button>
    </div>
  );

  const ov = OVERALL_STYLE[result.overall_status] || OVERALL_STYLE.NEEDS_INFO;
  const s = result.summary;
  const checks = [...result.checks].sort((a,b)=>(ORDER[a.status]??9)-(ORDER[b.status]??9));

  return (
    <div>
      <div className={`alert-banner ${ov.cls}`} style={{marginBottom:14,alignItems:"center"}}>
        <Icon d={result.overall_status==="PASS"?Icons.check:Icons.alert} size={16}/>
        <div style={{flex:1}}>
          <strong>{ov.label}</strong> — {result.recommendation}
        </div>
        <span className="badge badge-gray" style={{flexShrink:0}}>
          auto-approval: {result.auto_approval_allowed ? "allowed" : "blocked"}
        </span>
      </div>

      <div style={{display:"flex",gap:14,flexWrap:"wrap",fontSize:12,marginBottom:14,color:"var(--text3)"}}>
        <span style={{color:"var(--green)"}}>✓ {s.passed} passed</span>
        <span style={{color:"var(--red)"}}>✗ {s.failed} failed</span>
        <span style={{color:"var(--amber)"}}>! {s.warnings} warnings</span>
        <span style={{color:"var(--purple)"}}>? {s.inconclusive} inconclusive</span>
        <span>– {s.not_applicable} not applicable</span>
      </div>

      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {checks.map(c => {
          const st = STATUS_STYLE[c.status] || STATUS_STYLE.INCONCLUSIVE;
          return (
            <div key={c.rule_id} className="card" style={{padding:"10px 14px"}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}>
                <div style={{fontSize:12.5,fontWeight:600,color:"var(--text1)"}}>{c.rule_name}</div>
                <span className={`badge ${st.badge}`} style={{flexShrink:0}}>{st.label}</span>
              </div>
              <div style={{fontSize:11,color:"var(--text3)",marginTop:2}}>{c.evidence}</div>
              {c.regulation && (
                <div style={{fontSize:10.5,color:"var(--blue)",marginTop:4}}>
                  📘 {c.regulation}{c.regulation_ref ? ` — ${c.regulation_ref}` : ""}
                </div>
              )}
              {c.remediation && c.status !== "PASS" && c.status !== "NOT_APPLICABLE" && (
                <div style={{fontSize:11,color:"var(--text2)",marginTop:5,background:"var(--surface2)",borderRadius:6,padding:"5px 9px"}}>
                  → {c.remediation}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
