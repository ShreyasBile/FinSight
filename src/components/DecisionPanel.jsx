import { useEffect, useMemo, useState } from "react";
import { Icon, Icons } from "../icons/Icons.jsx";
import { api, formatINR } from "../utils/api.js";

// The buttons that move an application on (verify, review, approve, refer, send
// back, reject, accept the offer, sanction, disburse). Which ones appear — and why
// a button is disabled — comes from the API (GET .../actions), which applies the
// same rules again when the action is performed. So this component never decides
// what a role may do; it only asks, shows and confirms.

const STYLE = {
  primary: "btn btn-primary", success: "btn btn-success", danger: "btn btn-danger", secondary: "btn btn-secondary",
};
const ICON = {
  verify_documents: Icons.check, start_review: Icons.eye, approve: Icons.check, refer_to_head: Icons.send,
  send_back: Icons.chevronR, request_documents: Icons.upload, reject: Icons.x, accept_offer: Icons.check,
  decline_offer: Icons.x, sanction: Icons.landmark, disburse: Icons.banknote,
};

function emi(amount, rate, months) {
  const r = rate / 12 / 100;
  if (!(amount > 0) || !(months > 0)) return 0;
  if (r <= 0) return amount / months;
  const f = (1 + r) ** months;
  return (amount * r * f) / (f - 1);
}

const TITLES = { officer: "Officer Actions", head: "Head Manager Decision", user: "Your Decision" };

// refreshKey: changes when something the rules look at changed elsewhere on the page
// (an assessment set the fraud tier, the agents scored the risk) -> the actions are re-read.
export default function DecisionPanel({ applicationNo, session, onDone, bare = false, title, refreshKey }) {
  const actorId = session?.user_id;
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState(null);
  const [remarks, setRemarks] = useState("");
  const [offer, setOffer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!applicationNo || !actorId) return;
    let alive = true;
    setLoading(true); setError(null);
    api.actions(applicationNo, actorId)
      .then(d => { if (alive) { setData(d); setLoading(false); } })
      .catch(e => { if (alive) { setError(e.message); setLoading(false); } });
    return () => { alive = false; };
  }, [applicationNo, actorId, tick, refreshKey]);

  const current = useMemo(() => data?.actions?.find(a => a.action === picked) || null, [data, picked]);

  const pick = (a) => {
    setResult(null); setError(null);
    if (picked === a.action) { setPicked(null); return; }
    setPicked(a.action); setRemarks("");
    const d = data?.offer_draft;
    setOffer(a.offer && d ? { amount: d.amount, rate: d.rate, tenure_months: d.tenure_months } : null);
  };

  const submit = async () => {
    if (!current) return;
    setBusy(true); setError(null);
    try {
      const body = { actor_id: actorId, remarks: remarks.trim() || undefined };
      if (current.offer && offer) {
        body.offer = { amount: Number(offer.amount), rate: Number(offer.rate), tenure_months: Number(offer.tenure_months) };
      }
      const r = await api.act(applicationNo, current.action, body);
      setResult({ ok: true, text: `${current.label} — done. The application is now "${r.status_label}".` });
      setPicked(null); setRemarks("");
      setTick(t => t + 1);
      onDone?.(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!actorId) return null;
  const role = data?.role || session?.role_code;
  const actions = data?.actions || [];
  const remarksOk = !current?.remarks_required || remarks.trim().length >= 3;
  const liveEmi = offer ? emi(Number(offer.amount), Number(offer.rate), Number(offer.tenure_months)) : 0;
  const lim = data?.offer_draft?.limits || {};

  const body = (
    <>
      {loading && !data && <div style={{ fontSize: 12, color: "var(--text3)" }}>Loading actions…</div>}
      {data && actions.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text3)", lineHeight: 1.6 }}>
          {data.waiting_for || (["disbursed", "rejected", "cancelled", "expired"].includes(data.status_code)
            ? `This application is ${data.status_label.toLowerCase()} — nothing more to do.`
            : `Nothing for you to do while the application is "${data.status_label}".`)}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {actions.map(a => (
          <div key={a.action}>
            <button className={STYLE[a.style] || "btn btn-secondary"} disabled={!a.enabled || busy}
                    onClick={() => pick(a)} data-action={a.action}
                    style={{ width: "100%", justifyContent: "center", fontSize: 12,
                             opacity: a.enabled ? 1 : 0.55,
                             outline: picked === a.action ? "2px solid var(--blue)" : "none", outlineOffset: 1 }}>
              <Icon d={ICON[a.action] || Icons.check} size={12} />{a.label}
            </button>
            {!a.enabled && a.reason && (
              <div style={{ fontSize: 10.5, color: "var(--amber)", marginTop: 3, lineHeight: 1.4 }}>{a.reason}</div>
            )}
          </div>
        ))}
      </div>

      {current && (
        <div style={{ marginTop: 12, padding: 12, borderRadius: 8, background: "var(--surface2)",
                      border: "1px solid var(--border)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text1)", marginBottom: 4 }}>{current.label}</div>
          <div style={{ fontSize: 11, color: "var(--text3)", marginBottom: 10, lineHeight: 1.5 }}>{current.hint}</div>

          {current.offer && offer && (
            <div style={{ marginBottom: 10 }}>
              <div className="grid3" style={{ gap: 8 }}>
                <label style={{ fontSize: 10, color: "var(--text3)" }}>Amount (₹)
                  <input className="form-input" type="number" min={lim.min_amount || 1} max={lim.max_amount || undefined}
                         value={offer.amount} onChange={e => setOffer({ ...offer, amount: e.target.value })}
                         style={{ fontSize: 12, marginTop: 3 }} name="offer-amount" />
                </label>
                <label style={{ fontSize: 10, color: "var(--text3)" }}>Rate (% p.a.)
                  <input className="form-input" type="number" step="0.05" min={1} max={36}
                         value={offer.rate} onChange={e => setOffer({ ...offer, rate: e.target.value })}
                         style={{ fontSize: 12, marginTop: 3 }} name="offer-rate" />
                </label>
                <label style={{ fontSize: 10, color: "var(--text3)" }}>Tenure (months)
                  <input className="form-input" type="number" min={lim.min_tenure || 1} max={lim.max_tenure || undefined}
                         value={offer.tenure_months} onChange={e => setOffer({ ...offer, tenure_months: e.target.value })}
                         style={{ fontSize: 12, marginTop: 3 }} name="offer-tenure" />
                </label>
              </div>
              <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 8 }}>
                EMI <strong style={{ fontFamily: "var(--font-mono)" }}>{formatINR(Math.round(liveEmi))}</strong>
                <span style={{ color: "var(--text3)" }}>
                  {" "}· {data.offer_draft.source === "agents"
                    ? `pre-filled from the Offer Generation agent's draft${data.offer_draft.capped_by_foir ? " (capped to keep FOIR ≤ 50%)" : ""}`
                    : "pre-filled from the requested amount at the product's base rate — generate the credit memo for a risk-priced draft"}
                </span>
              </div>
            </div>
          )}

          <textarea className="form-input" rows={3} value={remarks} onChange={e => setRemarks(e.target.value)}
                    name="decision-remarks"
                    placeholder={current.action === "request_documents" ? "Which documents are needed, and why…"
                               : current.action === "reject" ? "Reason (the borrower sees this)…"
                               : current.remarks_required ? "Remarks (required)…" : "Remarks (optional)…"}
                    style={{ fontSize: 12, resize: "vertical", marginBottom: 8 }} />
          <div style={{ display: "flex", gap: 8 }}>
            <button className={STYLE[current.style] || "btn btn-primary"} disabled={busy || !remarksOk}
                    onClick={submit} data-confirm={current.action} style={{ fontSize: 12, flex: 1, justifyContent: "center" }}>
              {busy ? <><span className="spinner" style={{ width: 11, height: 11 }} /> Saving…</>
                    : <>{current.confirm ? "Confirm: " : ""}{current.label}</>}
            </button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => setPicked(null)} style={{ fontSize: 12 }}>
              Cancel
            </button>
          </div>
          {!remarksOk && (
            <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 5 }}>Remarks are required for this step.</div>
          )}
        </div>
      )}

      {result && (
        <div className="alert-banner alert-green" style={{ marginTop: 10, fontSize: 12 }}>
          <Icon d={Icons.check} size={13} style={{ color: "var(--green)", flexShrink: 0 }} />
          <span>{result.text}</span>
        </div>
      )}
      {error && (
        <div className="alert-banner alert-red" style={{ marginTop: 10, fontSize: 12 }}>
          <Icon d={Icons.alert} size={13} style={{ color: "var(--red)", flexShrink: 0 }} />
          <span>{error}</span>
        </div>
      )}
    </>
  );

  if (bare) return body;
  return (
    <div className="card" data-panel="decision">
      <div className="card-title" style={{ marginBottom: 10, fontSize: 12 }}>{title || TITLES[role] || "Actions"}</div>
      {body}
    </div>
  );
}
