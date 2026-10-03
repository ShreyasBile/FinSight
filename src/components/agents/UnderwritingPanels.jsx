import { useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { api, formatINR } from "../../utils/api.js";

// Views of the Underwriting agents' output (Income Analysis, Risk Scoring,
// Underwriting Supervisor, Credit Memo, Offer Generation, Workflow Router).
// Everything here is advisory: the recommendation is a suggestion for the
// officer, the offer is a draft, and no status changes without a person.

const REC_LABEL = {
  approve_recommended: "Approve recommended",
  request_more_info: "Request more information",
  refer_to_officer: "Refer for a closer review",
  decline_review_recommended: "Decline review recommended",
};
const REC_BADGE = {
  approve_recommended: "badge-green", request_more_info: "badge-gray",
  refer_to_officer: "badge-amber", decline_review_recommended: "badge-red",
};
const MEMO_TITLES = [
  ["executive_summary", "Executive summary"], ["applicant_profile", "Applicant profile"],
  ["income_assessment", "Income assessment"], ["risk_assessment", "Risk assessment"],
  ["fraud_and_compliance", "Fraud & compliance"], ["recommendation", "Recommendation"],
];
const pretty = (s) => String(s || "").replace(/_/g, " ");

export function RunUnderwritingButton({ onRun, busy, done, label = "Run underwriting agents" }) {
  return (
    <button className="btn btn-primary btn-sm" disabled={busy} onClick={onRun}>
      <Icon d={Icons.zap} size={12} /> {busy ? "Agents working…" : done ? "Re-run" : label}
    </button>
  );
}

function Bar({ value, max = 100, color }) {
  return (
    <div style={{ height: 6, background: "var(--surface2)", borderRadius: 3, overflow: "hidden", flex: 1 }}>
      <div style={{ width: `${Math.min(100, (value / max) * 100)}%`, height: "100%", background: color }} />
    </div>
  );
}

export function RiskAnalysisPanel({ uw, busy, error, onRun }) {
  const risk = uw?.risk;
  const income = uw?.income;
  if (!risk) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "32px 24px" }}>
        <Icon d={Icons.cpu} size={28} style={{ color: "var(--text3)", marginBottom: 10 }} />
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>Risk not scored by the agents yet</div>
        <div style={{ fontSize: 12, color: "var(--text3)", maxWidth: 460, margin: "0 auto 14px", lineHeight: 1.6 }}>
          Income Analysis triangulates every income figure on file, then Risk Scoring grades nine risk dimensions
          and adds an XGBoost opinion with per-feature contributions. About a minute with the local model.
        </div>
        <RunUnderwritingButton onRun={onRun} busy={busy} />
        {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 10 }}>{error}</div>}
      </div>
    );
  }
  const color = risk.overall >= 70 ? "var(--red)" : risk.overall >= 40 ? "var(--amber)" : "var(--green)";
  const ml = risk.ml_overlay || {};
  const maxC = Math.max(1, ...(risk.dimension_contributions || []).map(d => d.contribution));
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}>
        <RunUnderwritingButton onRun={onRun} busy={busy} done />
      </div>
      <div className="grid3" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <div className="stat-label">Risk score (agents)</div>
          <div style={{ fontSize: 22, fontWeight: 700, color }}>{risk.overall?.toFixed?.(1) ?? risk.overall} / 100</div>
          <div style={{ fontSize: 11, color: "var(--text3)" }}>Grade {risk.grade} · {pretty(risk.decision_advisory)}</div>
          {risk.engine && risk.overall > risk.engine.overall && (
            <div style={{ fontSize: 10.5, color: "var(--amber)", marginTop: 4 }}>
              Raised from {risk.engine.overall.toFixed?.(1) ?? risk.engine.overall} by FinSight's knock-out rules
            </div>
          )}
        </div>
        <div className="stat-card">
          <div className="stat-label">ML default probability</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "var(--blue)" }}>
            {ml.model_default_probability != null ? `${(ml.model_default_probability * 100).toFixed(1)}%` : "—"}
          </div>
          <div style={{ fontSize: 11, color: "var(--text3)" }}>XGBoost overlay · indicative</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Income (consensus)</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text1)" }}>
            {income?.consensus_monthly ? formatINR(income.consensus_monthly) : "—"}
          </div>
          <div style={{ fontSize: 11, color: income?.mismatches?.length ? "var(--amber)" : "var(--text3)" }}>
            {income?.mismatches?.length
              ? `${income.mismatches.length} mismatch(es) · lowest source used` +
                (income.declared_monthly && income.declared_monthly !== income.consensus_monthly
                  ? ` (declared ${formatINR(income.declared_monthly)})` : "")
              : `${income?.sources ?? 0} source(s) agree`} · per month
          </div>
        </div>
      </div>
      {(risk.overrides?.length > 0 || risk.conditions?.length > 0 || risk.decision_reason) && (
        <div className={`alert-banner ${risk.decision_advisory === "approve_recommended" ? "alert-blue" : "alert-amber"}`}
             style={{ marginBottom: 16, alignItems: "flex-start" }}>
          <div style={{ fontSize: 12, lineHeight: 1.6 }}>
            <strong>Why: </strong>{risk.decision_reason}
            {risk.overrides?.length > 0 && (
              <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                {risk.overrides.map((o, i) => <li key={i}>{o}</li>)}
              </ul>
            )}
            {risk.conditions?.length > 0 && (
              <div style={{ marginTop: 4, color: "var(--text2)" }}>Before approval: {risk.conditions.join(" · ")}</div>
            )}
          </div>
        </div>
      )}
      <div className="grid2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>What drives the score</div>
          {(risk.dimension_contributions || []).map(d => (
            <div key={d.dimension} style={{ marginBottom: 9 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text2)", width: 150, textTransform: "capitalize" }}>{pretty(d.dimension)}</span>
                <Bar value={d.contribution} max={maxC} color={d.score >= 60 ? "var(--red)" : d.score >= 35 ? "var(--amber)" : "var(--green)"} />
                <span style={{ fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)", width: 70, textAlign: "right" }}>
                  {d.score.toFixed(0)} × {d.weight}
                </span>
              </div>
              {d.factors?.[0] && <div style={{ fontSize: 11, color: "var(--text3)", marginLeft: 158 }}>{d.factors[0]}</div>}
            </div>
          ))}
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>Model feature contributions (XGBoost)</div>
          {(ml.feature_contributions || []).length === 0
            ? <div style={{ fontSize: 12, color: "var(--text3)" }}>{ml.error || "Not available for this case."}</div>
            : ml.feature_contributions.map(f => (
              <div key={f.feature} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7 }}>
                <span style={{ fontSize: 12, color: "var(--text2)", width: 90, fontFamily: "var(--font-mono)" }}>{f.feature}</span>
                <span style={{ fontSize: 11, color: f.contribution > 0 ? "var(--red)" : "var(--green)", width: 110 }}>
                  {f.contribution > 0 ? "raises risk" : "lowers risk"} {Math.abs(f.contribution).toFixed(2)}
                </span>
                <span style={{ fontSize: 11, color: "var(--text3)" }}>value {typeof f.value === "number" ? f.value.toFixed(1) : f.value}</span>
              </div>
            ))}
          <div className="section-head" style={{ fontSize: 12, marginTop: 14 }}>Income sources</div>
          {(income?.estimates || []).map(e => (
            <div key={e.source} style={{ display: "flex", justifyContent: "space-between", fontSize: 12, padding: "3px 0" }}>
              <span style={{ color: "var(--text2)" }}>{e.basis}</span>
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--text1)" }}>{formatINR(e.monthly)}</span>
            </div>
          ))}
          {(income?.mismatches || []).map((m, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--amber)", marginTop: 4 }}>⚠ {m.detail}</div>
          ))}
        </div>
      </div>
      <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 10, lineHeight: 1.6 }}>
        {(risk.model_notes || []).join(" ")}
      </div>
    </div>
  );
}

// Download the memo as a document (api/app/memo_export.py) — PDF or Word, as the person prefers.
function MemoDownloads({ applicationNo, actorId }) {
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState(null);
  if (!applicationNo || !actorId) return null;
  const get = async (format) => {
    setBusy(format); setMsg(null);
    try {
      const name = await api.downloadMemo(applicationNo, actorId, format);
      setMsg({ ok: true, text: `Downloaded ${name}` });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(null);
    }
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => get("pdf")} data-download="pdf">
        <Icon d={Icons.download} size={12} /> {busy === "pdf" ? "Preparing…" : "PDF"}
      </button>
      <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => get("docx")} data-download="docx">
        <Icon d={Icons.download} size={12} /> {busy === "docx" ? "Preparing…" : "Word"}
      </button>
      {msg && <span style={{ fontSize: 11, color: msg.ok ? "var(--green)" : "var(--red)" }}>{msg.text}</span>}
    </span>
  );
}

export function CreditMemoPanel({ uw, memo, offer, workflow, busy, error, onRun, role, applicationNo, actorId }) {
  const rec = uw?.recommendation;
  if (!memo) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "32px 24px" }}>
        <Icon d={Icons.policy} size={28} style={{ color: "var(--text3)", marginBottom: 10 }} />
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text1)", marginBottom: 6 }}>No credit memo yet</div>
        <div style={{ fontSize: 12, color: "var(--text3)", maxWidth: 480, margin: "0 auto 14px", lineHeight: 1.6 }}>
          The Underwriting Crew drafts it: a supervisor recommends a next step (it may tighten the rule baseline,
          never loosen it) and a writer drafts the memo. Every figure in the memo must appear in the evidence, or that
          section is replaced by a factual template.
        </div>
        {role !== "user" && <RunUnderwritingButton onRun={onRun} busy={busy} label="Generate credit memo" />}
        {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 10 }}>{error}</div>}
      </div>
    );
  }
  const m = memo.memo || {};
  return (
    <div>
      <div className="card" style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="card-title">Recommendation</span>
          {rec && <span className={`badge ${REC_BADGE[rec.recommendation] || "badge-gray"}`}>{REC_LABEL[rec.recommendation] || pretty(rec.recommendation)}</span>}
          {rec?.requires_head && <span className="badge badge-blue">Head approval needed (&gt; ₹5L)</span>}
          <span className="badge badge-gray">Draft · advisory</span>
          <span style={{ marginLeft: "auto" }}>
            {role !== "user" && <RunUnderwritingButton onRun={onRun} busy={busy} done label="Regenerate" />}
          </span>
        </div>
        {rec && (
          <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 8, lineHeight: 1.6 }}>
            {rec.rationale}
            <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 4 }}>
              Rule baseline: {REC_LABEL[rec.baseline] || pretty(rec.baseline)}
              {rec.llm_recommendation ? ` · model suggested: ${REC_LABEL[rec.llm_recommendation] || pretty(rec.llm_recommendation)}` : ""}
              {rec.loosening_blocked ? " · the model's looser suggestion was overruled by the baseline" : ""}
            </div>
          </div>
        )}
        {error && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 8 }}>{error}</div>}
      </div>
      <div className="grid2">
        <div className="card">
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <span className="card-title">Credit memo</span>
            <span className="badge badge-gray">{memo.source === "llm" ? "written by the model, grounded" : "factual template"}</span>
            {role !== "user" && (
              <span style={{ marginLeft: "auto" }}>
                <MemoDownloads applicationNo={applicationNo} actorId={actorId} />
              </span>
            )}
          </div>
          {MEMO_TITLES.map(([k, t]) => (
            <div key={k} style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text3)", textTransform: "uppercase", letterSpacing: 0.4 }}>
                {t}{memo.sections_replaced?.includes(k) && <span style={{ fontWeight: 400, textTransform: "none" }}> · templated</span>}
              </div>
              <div style={{ fontSize: 12, color: "var(--text1)", lineHeight: 1.6 }}>{m[k] || "—"}</div>
            </div>
          ))}
          {(m.conditions || []).length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text3)", textTransform: "uppercase" }}>Conditions</div>
              <ul style={{ margin: "4px 0 0 16px", fontSize: 12, color: "var(--text1)" }}>
                {m.conditions.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {offer && (
            <div className="card">
              <div className="card-title" style={{ marginBottom: 6 }}>Draft offer</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: "var(--green)", fontFamily: "var(--font-mono)" }}>
                {formatINR(offer.offered_amount)}
              </div>
              <div style={{ fontSize: 12, color: "var(--text2)" }}>
                {offer.interest_rate}% · {offer.tenure_months} months · EMI {formatINR(offer.emi)}
              </div>
              {offer.capped_by_foir && (
                <div style={{ fontSize: 11, color: "var(--amber)", marginTop: 4 }}>
                  Reduced from {formatINR(offer.requested_amount)} to keep EMIs within 50% of income.
                </div>
              )}
              <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 6 }}>{offer.note}</div>
            </div>
          )}
          {offer?.kfs && (
            <div className="card" data-kfs>
              <div className="card-title" style={{ marginBottom: 2 }}>Key Facts Statement</div>
              <div style={{ fontSize: 11, color: "var(--text3)", marginBottom: 8 }}>
                Goes to the borrower with the offer, before the loan contract (RBI).
              </div>
              {[
                ["APR (all-in yearly cost)", offer.kfs.apr != null ? `${offer.kfs.apr}%` : "—"],
                ["Interest rate", `${offer.kfs.interest_rate}%`],
                ...(offer.kfs.fees || []).map((f) => [f.name, `${formatINR(f.amount)} · ${f.when}`]),
                ["Amount the borrower receives", formatINR(offer.kfs.net_disbursed)],
                ["Total repayable", formatINR(offer.kfs.total_amount_payable)],
                ["Cooling-off period", `${offer.kfs.cooling_off_period_days} day(s), exit without penalty`],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12, padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
                  <span style={{ color: "var(--text3)" }}>{k}</span>
                  <span style={{ color: "var(--text1)", fontWeight: 600, textAlign: "right" }}>{v}</span>
                </div>
              ))}
              <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 8, lineHeight: 1.5 }}>
                {offer.kfs.disbursal}. {offer.kfs.penalty}.
              </div>
            </div>
          )}
          {workflow && (
            <div className="card">
              <div className="card-title" style={{ marginBottom: 6 }}>Next step</div>
              <div style={{ fontSize: 13, color: "var(--text1)" }}>
                {workflow.next ? <>{pretty(workflow.status)} → <b>{workflow.next}</b></> : workflow.note}
              </div>
              {workflow.owner && <div style={{ fontSize: 11, color: "var(--text3)" }}>Owner: {workflow.owner}</div>}
              {(workflow.blocked_by || []).map((b, i) => (
                <div key={i} style={{ fontSize: 12, color: "var(--amber)", marginTop: 4 }}>Blocked: {b}</div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
