import { useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import StatusBadge from "../../components/StatusBadge.jsx";
import DataState from "../../components/DataState.jsx";
import DecisionPanel from "../../components/DecisionPanel.jsx";
import DocumentViewer from "../../components/DocumentViewer.jsx";
import { LOAN_PRODUCTS } from "../../data/loanProducts.js";
import { api, useApi, formatINR, formatWhen, formatDate } from "../../utils/api.js";

// Document verification states as the database records them
// (ref.document_status), not a parallel set of frontend-only labels.
const DOC_STATUS_CONFIG = {
  verified:    { badge: "badge-green", label: "Verified",   icon: Icons.check },
  processing:  { badge: "badge-blue",  label: "Processing" },
  pending:     { badge: "badge-blue",  label: "Awaiting verification" },
  rejected:    { badge: "badge-red",   label: "Needs Re-upload", icon: Icons.alert },
  resubmitted: { badge: "badge-amber", label: "Resubmitted" },
  expired:     { badge: "badge-red",   label: "Expired" },
};

// The steps an application actually moves through (api/app/decisions.py). The per-product
// lists in loanProducts.js name steps the workflow never uses (and most miss "Sanctioned"),
// so a status outside them left the rail empty.
const RAIL = [
  ["submitted", "Submitted"], ["documentsProcessing", "Documents Processing"],
  ["documentsVerified", "Documents Verified"], ["underReview", "Under Review"],
  ["offerGenerated", "Offer Ready"], ["accepted", "Offer Accepted"],
  ["sanctioned", "Sanctioned"], ["disbursed", "Disbursed"],
];
const RAIL_ALIAS = { underwritingReview: "underReview" };
const railIndex = (code) => RAIL.findIndex(([c]) => c === (RAIL_ALIAS[code] || code));

const labelSm = {
  fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)",
  textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 2,
};

export default function UserAppDetail({ setPage, applicationNo, session }) {
  const { data: app, loading, error, reload } = useApi(
    () => api.application(applicationNo), [applicationNo], { skip: !applicationNo }
  );

  if (!applicationNo) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
        <Icon d={Icons.clipboardList} size={38} style={{ color: "var(--text3)", marginBottom: 12 }} />
        <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>
          No application selected
        </div>
        <button className="btn btn-primary" onClick={() => setPage("my-applications")}>
          Back to My Applications
        </button>
      </div>
    );
  }

  return (
    <DataState loading={loading && app?.application_no !== applicationNo} error={error} onRetry={reload}
               empty={!app} label="this application"
               emptyTitle="Application not found"
               emptyAction={
                 <button className="btn btn-primary" onClick={() => setPage("my-applications")}>
                   Back to My Applications
                 </button>}>
      {app && <Detail app={app} setPage={setPage} session={session} reload={reload} />}
    </DataState>
  );
}

// The borrower's answer to "Request more documents": pick what it is, attach it, send.
function RequestedUpload({ app, product, session, onUploaded }) {  // onUploaded(message)
  const [docType, setDocType] = useState(product?.documents?.[0]?.id || "other");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const send = async () => {
    if (!file) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.uploadDocument(app.application_no, session.user_id, docType, file);
      setFile(null);
      onUploaded?.(`${r.document} uploaded. Your officer has been notified.`);
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card" style={{ marginBottom: 16, borderColor: "var(--amber)" }} data-panel="requested-upload">
      <div className="card-title" style={{ marginBottom: 6 }}>More documents needed</div>
      <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.6, marginBottom: 12 }}>
        {app.document_request?.requested_by || "Your officer"} asked
        {app.document_request?.requested_at ? ` ${formatWhen(app.document_request.requested_at)}` : ""}:
        {" "}<strong>“{app.document_request?.remarks || "please upload the missing documents"}”</strong>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select className="form-input" value={docType} onChange={e => setDocType(e.target.value)}
                style={{ fontSize: 12, width: "auto", minWidth: 180 }} name="requested-doc-type">
          {(product?.documents || []).map(d => <option key={d.id} value={d.id}>{d.label}</option>)}
          <option value="other">Other document</option>
        </select>
        <input type="file" accept="image/*,application/pdf" onChange={e => setFile(e.target.files?.[0] || null)}
               style={{ fontSize: 12 }} name="requested-doc-file" />
        <button className="btn btn-primary" disabled={!file || busy} onClick={send} style={{ fontSize: 12 }}>
          <Icon d={Icons.upload} size={12} /> {busy ? "Uploading…" : "Upload"}
        </button>
      </div>
      {msg && (
        <div style={{ fontSize: 12, marginTop: 10, color: msg.ok ? "var(--green)" : "var(--red)" }}>{msg.text}</div>
      )}
    </div>
  );
}

// Remarks the borrower is meant to read; internal notes (e.g. a referral to the Head) stay internal.
const BORROWER_VISIBLE_REASONS = new Set(["request_documents", "reject"]);

function Detail({ app, setPage, session, reload }) {
  const [viewing, setViewing] = useState(null);
  const [notice, setNotice] = useState(null);
  const product = LOAN_PRODUCTS[app.loan_product];
  const offer = (app.offers || [])[0] || null;
  const docs = app.documents || [];
  const timeline = app.timeline || [];

  // Where the application is on the rail. A closed one (rejected / cancelled) shows how
  // far it got: the last rail step in its history.
  const closed = railIndex(app.status_code) < 0;
  const currentIdx = !closed ? railIndex(app.status_code)
    : Math.max(-1, ...timeline.map(t => railIndex(t.to_status)));

  const isDraft     = app.status_code === "draft";
  const isRejected  = app.status_code === "rejected";
  const isDisbursed = app.status_code === "disbursed";
  const showOffer   = !!offer;
  const showAction  = app.status_code === "offerGenerated";

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ cursor: "pointer", color: "var(--text3)", display: "flex" }}
                  onClick={() => setPage("my-applications")}>
              <Icon d={Icons.chevronR} size={16} style={{ transform: "rotate(180deg)" }} />
            </span>
            {app.application_no}
          </div>
          <div className="page-sub">Track the status of your loan application</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <StatusBadge status={app.status_label} />
          {isDraft && (
            <button className="btn btn-primary" onClick={() => setPage("new-app")}>
              <Icon d={Icons.edit} size={13} /> Continue Application
            </button>
          )}
        </div>
      </div>

      {/* Key facts */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="grid4">
          {[
            { k: "Loan Type", v: app.product_name },
            { k: "Amount", v: formatINR(app.loan_amount) },
            { k: "Tenure", v: app.tenure_months ? `${app.tenure_months} months` : "—" },
            { k: "Applied On", v: formatDate(app.applied_at) },
          ].map(m => (
            <div key={m.k}>
              <div style={labelSm}>{m.k}</div>
              <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text1)" }}>{m.v}</div>
            </div>
          ))}
        </div>
      </div>

      {isRejected && (
        <div className="alert-banner alert-red" style={{ marginBottom: 16 }}>
          <Icon d={Icons.alert} size={16} style={{ color: "var(--red)" }} />
          <span style={{ fontSize: 13 }}>
            This application was not approved.
            {app.cancellation_reason_code ? ` Reason: ${app.cancellation_reason_code}.` : ""}
            {" "}Contact support if you'd like to understand the decision.
          </span>
        </div>
      )}
      {isDisbursed && (
        <div className="alert-banner alert-green" style={{ marginBottom: 16 }}>
          <Icon d={Icons.check} size={16} style={{ color: "var(--green)" }} />
          <span style={{ fontSize: 13 }}>
            Your loan has been disbursed
            {(app.disbursements || [])[0]
              ? `: ${formatINR(app.disbursements[0].disbursed_amount)} by ${app.disbursements[0].disbursement_method}, reference ${app.disbursements[0].reference_number}.`
              : "."}
          </span>
        </div>
      )}
      {app.status_code === "cancelled" && (
        <div className="alert-banner alert-amber" style={{ marginBottom: 16 }}>
          <Icon d={Icons.info} size={16} style={{ color: "var(--amber)" }} />
          <span style={{ fontSize: 13 }}>You declined the offer, so this application is closed.</span>
        </div>
      )}
      {notice && (
        <div className="alert-banner alert-green" style={{ marginBottom: 16 }}>
          <Icon d={Icons.check} size={16} style={{ color: "var(--green)" }} />
          <span style={{ fontSize: 13 }}>{notice}</span>
        </div>
      )}
      {app.document_request && (
        <RequestedUpload app={app} product={product} session={session}
                         onUploaded={(m) => { setNotice(m); reload?.(); }} />
      )}

      {/* Progress rail */}
      {!isDraft && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title" style={{ marginBottom: 16 }}>Application Progress</div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {RAIL.map(([s, label], i) => {
              const done = currentIdx >= 0 && i <= currentIdx;
              const now = !closed && i === currentIdx;
              return (
                <div key={s} style={{ flex: "1 1 90px", minWidth: 90 }}>
                  <div style={{
                    height: 4, borderRadius: 2, marginBottom: 6,
                    background: done ? (now ? "var(--blue)" : "var(--green)") : "var(--border)",
                  }} />
                  <div style={{
                    fontSize: 10, lineHeight: 1.3,
                    color: now ? "var(--blue)" : done ? "var(--text2)" : "var(--text3)",
                    fontWeight: now ? 600 : 400,
                  }}>{now && app.status_code === "underwritingReview" ? "Final Approval" : label}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="detail-grid">
        <div>
          {/* Offer */}
          {showOffer && (
            <div className="card" style={{ marginBottom: 16, borderColor: "var(--green)" }}>
              <div className="card-title" style={{ marginBottom: 14 }}>Your Loan Offer</div>
              <div className="grid3" style={{ marginBottom: 14 }}>
                {[
                  { k: "Approved Amount", v: formatINR(offer.offered_amount), c: "var(--green)" },
                  { k: "Monthly EMI", v: formatINR(offer.emi_amount), c: "var(--blue)" },
                  { k: "Interest Rate", v: offer.interest_rate != null ? `${offer.interest_rate}%` : "—" },
                ].map(m => (
                  <div key={m.k}>
                    <div style={labelSm}>{m.k}</div>
                    <div style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)",
                                  color: m.c || "var(--text1)" }}>{m.v}</div>
                  </div>
                ))}
              </div>
              <div className="grid3">
                {[
                  ["Tenure", offer.tenure_months ? `${offer.tenure_months} months` : "—"],
                  ["Processing Fee", formatINR(offer.processing_fee)],
                  ["Offer Valid Until", formatDate(offer.offer_valid_until)],
                ].map(([k, v]) => (
                  <div key={k}>
                    <div style={labelSm}>{k}</div>
                    <div style={{ fontSize: 13, color: "var(--text1)" }}>{v}</div>
                  </div>
                ))}
              </div>
              {showAction ? (
                <div style={{ marginTop: 16, maxWidth: 420 }}>
                  <DecisionPanel bare applicationNo={app.application_no} session={session} onDone={() => reload?.()} />
                </div>
              ) : (
                <div style={{ marginTop: 12 }}>
                  <span className={`badge ${offer.status === "accepted" ? "badge-green" : "badge-gray"}`}
                        style={{ textTransform: "capitalize" }}>
                    {offer.status === "accepted" ? "Offer accepted" : `Offer ${offer.status}`}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Documents */}
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-title" style={{ marginBottom: 14 }}>
              Documents <span className="badge badge-gray" style={{ marginLeft: 6 }}>{docs.length}</span>
            </div>
            {docs.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--text3)" }}>
                No documents uploaded for this application yet.
              </div>
            ) : docs.map(d => {
              const cfg = DOC_STATUS_CONFIG[d.verification_status] || { badge: "badge-gray", label: d.verification_status };
              return (
                <div key={d.document_id} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 0", borderBottom: "1px solid var(--border)",
                }}>
                  <Icon d={Icons.fileText} size={16} style={{ color: "var(--text3)", flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: "var(--text1)" }}>{d.doc_label}</div>
                    <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>
                      {d.file_name} · {formatDate(d.uploaded_at)}
                      {d.attempt_no > 1 ? ` · attempt ${d.attempt_no}` : ""}
                    </div>
                  </div>
                  {d.tampering_risk_tier && d.tampering_risk_tier !== "LOW" && (
                    <span className="badge badge-amber" style={{ fontSize: 9 }}>
                      {d.tampering_risk_tier} risk
                    </span>
                  )}
                  <span className={`badge ${cfg.badge}`} style={{ fontSize: 10 }}>
                    {cfg.icon && <Icon d={cfg.icon} size={10} />} {cfg.label}
                  </span>
                  <button className="btn btn-ghost" style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => setViewing(d)}>
                    <Icon d={Icons.eye} size={11} />View
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* Timeline — the real status history, not a fixed script */}
        <div className="page-aside">
          <div className="card">
            <div className="card-title" style={{ marginBottom: 14 }}>Progress Timeline</div>
            {timeline.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--text3)" }}>Nothing recorded yet.</div>
            ) : (
              <div className="timeline">
                {timeline.map((t, i) => {
                  const current = i === timeline.length - 1;
                  return (
                    <div key={t.event_seq} className="timeline-item">
                      <div className="timeline-dot" style={{
                        background: current ? "var(--blue)" : "var(--green)",
                      }} />
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 600,
                                      color: current ? "var(--blue)" : "var(--text1)" }}>
                          {t.to_label}
                          {!!t.is_rework && (
                            <span className="badge badge-amber" style={{ marginLeft: 6, fontSize: 9 }}>
                              revisited
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>
                          {t.from_label ? `from ${t.from_label}` : "Application created"}
                          {t.actor ? ` · ${t.actor}` : t.is_automated ? " · automated" : ""}
                        </div>
                        {t.change_reason && BORROWER_VISIBLE_REASONS.has(t.reason_code) && (
                          <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 3, fontStyle: "italic" }}>
                            “{t.change_reason}”
                          </div>
                        )}
                        <div style={{ fontSize: 10, color: "var(--text3)",
                                      fontFamily: "var(--font-mono)", marginTop: 3 }}>
                          {formatWhen(t.created_at)}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
      <DocumentViewer doc={viewing} session={session} onClose={() => setViewing(null)} />
    </div>
  );
}
