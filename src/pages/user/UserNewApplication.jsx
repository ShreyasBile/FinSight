import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { LOAN_PRODUCTS, LOAN_TYPE_LIST } from "../../data/loanProducts.js";
import { selectedLoanType, setSelectedLoanType } from "../../data/appState.js";
import { DOC_TYPES } from "../../data/docTypes.js";
import { FIELD_LABELS } from "../../data/fieldLabels.js";
import { runOCR, analyzeIntegrity, explainTampering, locateTampering, verifyFaces, maskDocument, storeApplication, setAgentContext } from "../../utils/ocr.js";
import { newCaseId } from "../../utils/agents.js";
import AgentActivityPanel from "../../components/agents/AgentActivityPanel.jsx";
import CaseAssessmentCard from "../../components/agents/CaseAssessmentCard.jsx";
import SelfieLivenessCard from "../../components/agents/SelfieLivenessCard.jsx";
import {
  EXPECTED_FIELDS, validateField, checkDocumentIdentity,
  crossVerifyDocuments, collectAutofillCandidates,
} from "../../utils/docVerification.js";
import { TIER } from "../../utils/statusTiers.js";

const BACKEND_DOC_IDS = new Set(DOC_TYPES.map((d) => d.id));

// Document-integrity result from the backend tampering analysis. Non-blocking:
// LOW is a quiet green tick; MEDIUM/HIGH surface the reasons for a human to
// review. Never auto-rejects — matches the cascade's design.
const TAMPER_STYLE = {
  LOW: { ...TIER.good, bg: "var(--green-tint)", text: "Document integrity check passed" },
  MEDIUM: { ...TIER.warn, bg: "var(--amber-tint)", text: "Some checks need review" },
  HIGH: { ...TIER.bad, bg: "var(--red-tint)", text: "Document integrity concerns" },
};
// (The compact per-upload TamperBadge that used to render here was removed
// along with upload-step tampering: the integrity analysis now runs on the
// Review & Submit step, which has its own full display.)

// Small thumbnail of an uploaded file — the image itself, or a document icon
// for PDFs/non-images. Hovering enlarges it into a floating preview (works for
// PDFs too: the backend returns a clean rendered preview since the browser
// can't rasterize a PDF page itself). Used in the upload cards and review step.
// `secure` mode (Review & Submit): the enlarged hover preview shows ONLY the
// PII-masked render — Aadhaar / PAN / account numbers redacted to their last 4
// digits — never the raw document. It's fetched lazily on first hover via
// onSecureHover; until it arrives, a "Securing preview…" placeholder is shown
// so the raw sensitive image is never flashed.
function FileThumb({ upload, size = 44, secure = false, secureSrc = null,
                    secureLoading = false, secureError = false, onSecureHover }) {
  const [hover, setHover] = useState(false);
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  if (!upload) return null;
  // Prefer the backend's cleaned (auto-oriented/deskewed/contrast-corrected)
  // render once it arrives — the image actually read for OCR, not the
  // borrower's raw photo. Falls back to the instant client-side object URL
  // (shown the moment a file is picked, before any response), then to the
  // backend's PDF page render.
  const src = upload.cleanedPreview || upload.previewUrl || upload.tampering?.document_preview || null;
  const isPdf = (upload.fileType || "").includes("pdf") || /\.pdf$/i.test(upload.fileName || "");

  const box = {
    width: size, height: size, flexShrink: 0, borderRadius: 6,
    border: "1px solid var(--border)", background: "var(--surface2)",
    display: "flex", alignItems: "center", justifyContent: "center",
    overflow: "hidden", cursor: src ? "zoom-in" : "default",
  };

  const showPreview = () => {
    if (!src || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const PW = 360, MARGIN = 12;
    // prefer the right of the thumbnail; flip left if it would overflow
    let left = r.right + MARGIN;
    if (left + PW > window.innerWidth - 8) left = Math.max(8, r.left - PW - MARGIN);
    let top = r.top + r.height / 2;
    setPos({ left, top, width: PW });
    setHover(true);
    if (secure && onSecureHover) onSecureHover();  // lazy-fetch the masked render
  };
  const hide = () => setHover(false);

  return (
    <div
      ref={ref}
      style={box}
      onMouseEnter={showPreview}
      onMouseLeave={hide}
    >
      {src ? (
        <img src={src} alt={upload.fileName || "upload"}
             style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <Icon d={isPdf ? Icons.fileText : Icons.image} size={20} style={{ color: "var(--text3)" }} />
      )}
      {hover && src && pos && createPortal(
        <div
          style={{
            position: "fixed", left: pos.left, top: pos.top, transform: "translateY(-50%)",
            width: pos.width, maxWidth: "92vw", zIndex: 10000,
            background: "var(--surface1, #fff)", border: "1px solid var(--border)",
            borderRadius: 10, boxShadow: "0 10px 40px rgba(0,0,0,0.25)", padding: 6,
            pointerEvents: "none",
          }}
        >
          {secure ? (
            secureSrc ? (
              <img src={secureSrc} alt={upload.fileName || "preview (masked)"}
                   style={{ width: "100%", maxHeight: "80vh", objectFit: "contain",
                            display: "block", borderRadius: 6 }} />
            ) : (
              <div style={{ height: 200, borderRadius: 6, background: "var(--surface2)",
                            border: "1px dashed var(--border)", display: "flex",
                            flexDirection: "column", alignItems: "center", justifyContent: "center",
                            gap: 8, color: "var(--text3)", fontSize: 11.5, textAlign: "center", padding: 12 }}>
                {secureError ? (
                  <>Couldn't build a secure preview.</>
                ) : (
                  <><span className="spinner" style={{ width: 16, height: 16 }} />
                     Securing preview…<br />hiding ID &amp; account numbers</>
                )}
              </div>
            )
          ) : (
            <img src={src} alt={upload.fileName || "preview"}
                 style={{ width: "100%", maxHeight: "80vh", objectFit: "contain",
                          display: "block", borderRadius: 6 }} />
          )}
          {secure && secureSrc && (
            <div style={{ fontSize: 10, color: "var(--text3)", padding: "5px 4px 0",
                          display: "flex", alignItems: "center", gap: 5 }}>
              <Icon d={Icons.lock} size={10} style={{ color: "var(--green)" }} />
              ID &amp; account numbers masked — last 4 digits shown
            </div>
          )}
          {upload.fileName && (
            <div style={{ fontSize: 10.5, color: "var(--text3)", padding: "5px 4px 2px",
                          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {upload.fileName}
            </div>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}

// Clean a raw backend explanation for display — strip the internal [signal]
// prefix and the FAILED: marker (wording the applicant/officer shouldn't see).
const cleanFinding = (e) => e.replace(/^\[[^\]]+\]\s*/, "").replace(/^FAILED:\s*/, "");

// Is this explanation a real, showable concern (not a "skipped"/"passed"/
// internal-fusion line)?
const isConcern = (e) =>
  !/skipped:/.test(e) && !/^Risk driven/.test(e) &&
  !/^\[[^\]]+\]\s*(All \d|no applicable)/.test(e);

// Per-document investigation shown on the Review & Submit page for a flagged
// (MEDIUM/HIGH) document. Everything loads AUTOMATICALLY (no toggle) when the
// review step opens: the plain-language summary, the concrete findings, and an
// annotated render of the document with the located issues boxed. See the
// auto-load effect in the component body (fetchLocate / fetchExplain).
function IntegrityDetail({ upload: u, st }) {
  const t = u.tampering;
  const reasons = (t.explanations || []).filter(isConcern).map(cleanFinding);
  const loc = u.located;
  const plain = t.plain_explanation;
  const IMG_W = 340; // fixed left-column width so the text column gets the rest
  const loadingRow = (label) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--text3)" }}>
      <span className="spinner" style={{ width: 12, height: 12 }} /> {label}
    </div>
  );

  const hasImagePane = u.locateLoading || u.locateError || (loc && loc.annotated_image);

  return (
    <div style={{ marginTop: 10, padding: 12, background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: 8 }}>
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* document image — left column, fixed width */}
        {hasImagePane && (
          <div style={{ flex: `0 0 ${IMG_W}px`, width: IMG_W, maxWidth: "100%" }}>
            {u.locateLoading && !loc && loadingRow("Reading the document to locate the issue…")}
            {u.locateError && <div style={{ fontSize: 10.5, color: "var(--red)" }}>{u.locateError}</div>}
            {loc && loc.annotated_image && (
              <>
                <img src={loc.annotated_image} alt="Reviewed document"
                  style={{ width: "100%", display: "block", borderRadius: 6, border: "1px solid var(--border)" }} />
                <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 6, display: "flex", alignItems: "flex-start", gap: 5 }}>
                  <Icon d={Icons.scanText} size={11} style={{ color: st.color, flexShrink: 0, marginTop: 1 }} />
                  {loc.located_count > 0
                    ? "Read with OCR — boxed where each numbered issue appears."
                    : "This concern isn't printed on the page — it's in the file's data — so there's nothing to box."}
                </div>
              </>
            )}
          </div>
        )}

        {/* everything else — right column, fills remaining width */}
        <div style={{ flex: "1 1 260px", minWidth: 0 }}>
          {/* plain-language summary — the borrower/officer-friendly read of the concern */}
          {u.explainLoading && !plain && loadingRow("Summarising the concern…")}
          {plain && (plain.summary || (plain.issues || []).length > 0) && (
            <div style={{ padding: 10, background: st.bg, border: `1px solid ${st.color}`, borderRadius: 8, marginBottom: 10 }}>
              {plain.summary && <div style={{ fontSize: 12, color: "var(--text1)", lineHeight: 1.6, fontWeight: 500 }}>{plain.summary}</div>}
              {(plain.issues || []).length > 0 && (
                <ul style={{ margin: "7px 0 0", paddingLeft: 18, fontSize: 11, color: "var(--text2)", lineHeight: 1.6 }}>
                  {plain.issues.map((e, i) => <li key={i} style={{ marginBottom: 2 }}>{e}</li>)}
                </ul>
              )}
            </div>
          )}

          {/* The findings are shown ONCE. Prefer the numbered legend when locate
              actually boxed something on the page (its numbers map to those
              boxes); otherwise the numbers reference nothing, so fall back to a
              plain bullet list. Showing both — as this used to — duplicated
              every finding, which was glaring for PDF-metadata concerns that are
              never on the page (all annotations un-located). */}
          {loc && loc.located_count > 0 && (loc.annotations || []).length > 0 ? (
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
              {loc.annotations.map((a) => (
                <div key={a.n} style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 11, color: "var(--text2)", lineHeight: 1.5 }}>
                  <span style={{ flexShrink: 0, width: 17, height: 17, borderRadius: 5, fontSize: 10, fontWeight: 700, marginTop: 1,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    background: a.located ? st.color : "var(--surface3)", color: a.located ? "#fff" : "var(--text3)" }}>{a.n}</span>
                  <span>{a.text}{!a.located && <span style={{ color: "var(--text3)", fontStyle: "italic" }}> — in the file's data, not on the page</span>}</span>
                </div>
              ))}
            </div>
          ) : (
            reasons.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11, color: "var(--text2)", lineHeight: 1.6 }}>
                {reasons.map((e, i) => <li key={i} style={{ marginBottom: 3 }}>{e}</li>)}
              </ul>
            )
          )}
        </div>
      </div>
    </div>
  );
}

// Documents come FIRST so personal details can be auto-filled from what the
// documents say — the user confirms values instead of typing them twice.
// answers the store payload already carries as `loan` / `applicant`
const FORM_CORE_KEYS = ["name", "mobile", "email", "dob", "city", "state", "pincode", "loanAmount", "tenure",
  "purpose", "monthlyIncome", "existingEmi", "employmentType"];
const positive = (v) => { const n = Number(String(v ?? "").replace(/,/g, "")); return Number.isFinite(n) && n > 0 ? n : null; };

const STEPS = [
  { num: 1, label: "Required Documents" },
  { num: 2, label: "Upload Documents" },
  { num: 3, label: "Personal & Loan Details" },
  { num: 4, label: "Review & Submit" },
];

const EMPLOYMENT_OPTIONS = [
  "Salaried","Self-employed","Business owner","Farmer","Pensioner","Student","Homemaker",
];

const DOC_ICON = (label) => {
  const l = label.toLowerCase();
  if (l.includes("pan") || l.includes("aadhaar") || l.includes("identity") || l.includes("kyc") || l.includes("age proof")) return Icons.idCard;
  if (l.includes("photo") || l.includes("photograph")) return Icons.image;
  if (l.includes("bank")) return Icons.landmark;
  if (l.includes("salary")) return Icons.receiptText;
  if (l.includes("itr") || l.includes("form")) return Icons.fileText;
  if (l.includes("property") || l.includes("deed") || l.includes("title") || l.includes("tax receipt") || l.includes("encumbrance")) return Icons.housePlus;
  if (l.includes("land") || l.includes("cultivat") || l.includes("crop")) return Icons.wheat;
  if (l.includes("gold")) return Icons.gem;
  if (l.includes("quotation") || l.includes("vehicle") || l.includes("car") || l.includes("rc book") || l.includes("insurance")) return Icons.car;
  if (l.includes("tractor")) return Icons.tractor;
  if (l.includes("business") || l.includes("gst") || l.includes("udyam") || l.includes("balance") || l.includes("registration") || l.includes("trade license")) return Icons.briefcaseBus;
  if (l.includes("address")) return Icons.house;
  if (l.includes("admission") || l.includes("fee") || l.includes("academic") || l.includes("marksheet")) return Icons.graduation;
  if (l.includes("invoice") || l.includes("agreement") || l.includes("lease") || l.includes("building plan")) return Icons.fileText;
  if (l.includes("license") || l.includes("rc") || l.includes("driving")) return Icons.truck;
  if (l.includes("partnership") || l.includes("moa") || l.includes("aoa")) return Icons.scrollText;
  if (l.includes("employer") || l.includes("work id")) return Icons.userRound;
  if (l.includes("collateral")) return Icons.lock;
  if (l.includes("declaration") || l.includes("noc")) return Icons.copy;
  return Icons.fileText;
};

const calcEMI = (amount, tenure) => {
  if (!amount || !tenure || Number(amount) <= 0 || Number(tenure) <= 0) return 0;
  const P = Number(amount);
  const n = Number(tenure);
  const r = 0.12 / 12;
  return Math.round(P * r * Math.pow(1 + r, n) / (Math.pow(1 + r, n) - 1));
};

const formatCurrency = (val) => {
  if (!val) return "";
  const n = Number(val);
  if (isNaN(n)) return val;
  return "₹" + n.toLocaleString("en-IN");
};

const generateTenureOptions = (min, max) => {
  const opts = [];
  let step;
  if (max <= 12) step = 1;
  else if (max <= 36) step = 3;
  else if (max <= 84) step = 6;
  else step = 12;
  for (let i = min; i <= max; i += step) opts.push(String(i));
  if (opts[opts.length - 1] !== String(max)) opts.push(String(max));
  return opts;
};

export default function UserNewApplication({ setPage, role = "user", session }) {
  const isStaff = role === "officer" || role === "head";
  // Officer's "My Applications" list is at a different page key than the
  // borrower's — same destination concept, different route per role.
  const listPage = role === "user" ? "my-applications" : "applications";
  // Generated once (not inline in render, which would regenerate — and change
  // the visible ID — on every keystroke).
  const [draftId] = useState(() => String(Date.now()).slice(-6));
  // Agent case id: minted once per application draft and sent as X-Case-Id on
  // every document call, so every agent step for this application lands in one
  // case file (and one process-mining case). Linked to the APP-... id at submit.
  const [caseId] = useState(() => newCaseId());
  // actorId lets the backend attach the application to the right person: the borrower's own
  // record, or the officer's queue
  useEffect(() => { setAgentContext({ caseId, role, actorId: session?.user_id }); }, [caseId, role, session?.user_id]);
  const [showAgentActivity] = useState(() => {
    try { return localStorage.getItem("finsight.showAgentActivity") !== "0"; } catch { return true; }
  });
  const [step, setStep] = useState(() => (selectedLoanType ? 1 : 0));
  const [loanTypeId, setLoanTypeId] = useState(() => selectedLoanType || null);
  const [formData, setFormData] = useState({});
  const [uploads, setUploads] = useState({});
  const [consent, setConsent] = useState({});
  const [submitted, setSubmitted] = useState(false);
  // audit-store outcome, shown on the success screen: null | {state:"saving"} |
  // {state:"ok", rows} | {state:"error", error}
  const [storeStatus, setStoreStatus] = useState(null);
  const [appId, setAppId] = useState(null);
  const [stepErrors, setStepErrors] = useState({});
  const [decisionNote, setDecisionNote] = useState("");
  // Cross-document face verification result for the Review & Submit page.
  // {key, loading, error, data} — `key` identifies the exact set of uploaded
  // files it was computed for, so re-entering step 4 doesn't re-run it unless
  // a document changed.
  const [faceCheck, setFaceCheck] = useState(null);
  // Masked hover-preview renders, keyed by docId: { loading, src, error }.
  // Fetched lazily the first time a document thumbnail is hovered on the review
  // step (see ensureMasked).
  const [masked, setMasked] = useState({});

  const product = loanTypeId ? LOAN_PRODUCTS[loanTypeId] : null;

  const updateField = (id, val) => {
    setFormData((p) => ({ ...p, [id]: val }));
    // Typing into a field clears its validation error immediately.
    setStepErrors((p) => {
      if (!(id in p)) return p;
      const next = { ...p };
      delete next[id];
      return next;
    });
  };

  const toggleConsent = (key) => setConsent((p) => ({ ...p, [key]: !p[key] }));

  const generateTenureOptionsForProduct = () => {
    if (!product) return [];
    return generateTenureOptions(product.minTenure, product.maxTenure);
  };

  const handleSelectLoanType = (id) => {
    setSelectedLoanType(id);
    setLoanTypeId(id);
    setFormData({});
    setUploads({});
    setConsent({});
    setStep(1);
  };

  const handleChangeLoanType = () => {
    setSelectedLoanType(null);
    setLoanTypeId(null);
    setFormData({});
    setUploads({});
    setConsent({});
    setStep(0);
  };

  const handleUpload = async (docId, file) => {
    if (!file) return;
    const automated = BACKEND_DOC_IDS.has(docId);
    // small preview of the uploaded file (images only; PDFs get an icon).
    // Object URLs are revoked when a new file replaces this slot (see below).
    const isImage = file.type && file.type.startsWith("image/");
    const previewUrl = isImage ? URL.createObjectURL(file) : null;
    setUploads((prev) => {
      if (prev[docId]?.previewUrl) URL.revokeObjectURL(prev[docId].previewUrl);
      return {
        ...prev,
        // `file` is kept so a MEDIUM/HIGH document's "explain in detail" toggle
        // can re-send it on demand without asking the user to re-upload.
        [docId]: { file, fileName: file.name, fileType: file.type, previewUrl,
                   status: "processing", extracted: null, parsed: null, automated },
      };
    });

    // This document type has no automated check yet — it goes straight to a
    // human reviewer instead of being labelled with fields we can't actually read.
    if (!automated) {
      await new Promise((r) => setTimeout(r, 600));
      setUploads((prev) => ({
        ...prev,
        [docId]: {
          ...prev[docId],
          status: "completed",
          extracted: { "Status": { value: "Uploaded — our team will verify this", flag: "ok" } },
        },
      }));
      return;
    }

    try {
      // Preprocessing + field extraction (fast now). Tampering is deferred to
      // the Review & Submit step (see fetchIntegrity). cleanedPreview is the
      // warped/auto-oriented render the VLM read — used for the hover preview
      // and the deferred PII masking. `timings` = {preprocess_ms, extract_ms}
      // for the application audit trail (see buildStorePayload).
      const uploadedAt = new Date().toISOString();
      const { parsed, cleanedPreview, timings } = await runOCR(file, docId);

      setUploads((prev) => ({
        ...prev,
        [docId]: prev[docId]
          ? { ...prev[docId], uploadedAt, timings: { ...(prev[docId].timings || {}), ...(timings || {}) },
              ...(cleanedPreview ? { cleanedPreview } : {}) }
          : prev[docId],
      }));

      // Wrong-document gate: if the identifying field for this doc type is
      // absent or malformed (regex check, no model judgment), don't show
      // whatever stray text was read — tell the user to re-upload.
      if (!checkDocumentIdentity(docId, parsed || {}).ok) {
        setUploads((prev) => ({
          ...prev,
          [docId]: { ...prev[docId], status: "wrong_doc", parsed: null },
        }));
        return;
      }

      // Build rows over the EXPECTED field list so fields the model could not
      // read still appear — as red "Not found" — instead of silently vanishing.
      let extracted = {};
      if (docId === "photo") {
        extracted = { "Photo check": { value: "Face detected", flag: "ok" } };
      } else {
        const expected = EXPECTED_FIELDS[docId] || Object.keys(parsed || {});
        for (const key of expected) {
          const value = parsed?.[key];
          const flag = validateField(key, value); // ok | warn | error | missing
          const label = FIELD_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
          extracted[label] = { value: flag === "missing" ? null : value, flag };
        }
      }

      setUploads((prev) => ({
        ...prev,
        [docId]: { ...prev[docId], status: "review", extracted, parsed },
      }));
    } catch (err) {
      console.error(`OCR failed for ${docId}:`, err);
      setUploads((prev) => ({
        ...prev,
        [docId]: { ...prev[docId], status: "error" },
      }));
    }
  };

  const handleConfirmExtracted = (docId) => {
    setUploads((prev) => ({
      ...prev,
      [docId]: { ...prev[docId], status: "completed" },
    }));
  };

  // Run the deferred tampering analysis for one document (fetch-once). Invoked
  // when the Review & Submit step opens — the upload step no longer pays for it.
  // The cleaned preview already arrived with runOCR at upload time.
  const fetchIntegrity = async (docId) => {
    const u = uploads[docId];
    // integrityDone (not !tampering) is the fetch-once guard — a genuine LOW
    // result can be null-ish, and keying off tampering alone would re-fetch it
    // forever as the effect re-runs on every uploads change.
    if (!u?.file || !u?.automated || u.integrityDone || u.integrityLoading) return;
    setUploads((prev) => ({
      ...prev,
      [docId]: { ...prev[docId], integrityLoading: true, integrityError: null },
    }));
    try {
      const { tampering, timings } = await analyzeIntegrity(u.file, docId, u.parsed || {});
      setUploads((prev) => ({
        ...prev,
        [docId]: {
          ...prev[docId], integrityLoading: false, integrityDone: true, tampering,
          timings: { ...(prev[docId].timings || {}), ...(timings || {}) },
        },
      }));
    } catch (err) {
      console.error(`Integrity analysis failed for ${docId}:`, err);
      setUploads((prev) => ({
        ...prev,
        [docId]: { ...prev[docId], integrityLoading: false, integrityDone: true, integrityError: "Couldn't run the integrity check." },
      }));
    }
  };

  // Fetch the plain-language explanation for a flagged document (fetch-once, no
  // toggle). Auto-invoked when the Review & Submit step opens (see effect below).
  const fetchExplain = async (docId) => {
    const u = uploads[docId];
    if (!u?.file || !u?.tampering) return;
    if (u.tampering.plain_explanation || u.explainLoading) return;
    setUploads((prev) => ({
      ...prev,
      [docId]: { ...prev[docId], explainLoading: true, explainError: null },
    }));
    try {
      const explanation = await explainTampering(u.file, docId, u.tampering);
      setUploads((prev) => ({
        ...prev,
        [docId]: {
          ...prev[docId],
          explainLoading: false,
          tampering: { ...prev[docId].tampering, plain_explanation: explanation },
        },
      }));
    } catch (err) {
      console.error(`Explanation failed for ${docId}:`, err);
      setUploads((prev) => ({
        ...prev,
        [docId]: { ...prev[docId], explainLoading: false, explainError: "Couldn't load the plain-language summary." },
      }));
    }
  };

  // Fetch the OCR-grounded annotated render for a flagged document (fetch-once,
  // no toggle). Auto-invoked when the Review & Submit step opens.
  const fetchLocate = async (docId) => {
    const u = uploads[docId];
    if (!u?.file || !u?.tampering) return;
    if (u.located || u.locateLoading) return;
    setUploads((prev) => ({
      ...prev,
      [docId]: { ...prev[docId], locateLoading: true, locateError: null },
    }));
    try {
      const located = await locateTampering(u.file, docId, u.tampering);
      setUploads((prev) => ({
        ...prev,
        [docId]: { ...prev[docId], locateLoading: false, located },
      }));
    } catch (err) {
      console.error(`Localization failed for ${docId}:`, err);
      setUploads((prev) => ({
        ...prev,
        [docId]: { ...prev[docId], locateLoading: false, locateError: "Couldn't render the annotated document." },
      }));
    }
  };

  const handleRemove = (docId) => {
    setUploads((prev) => {
      const n = { ...prev };
      delete n[docId];
      return n;
    });
    // drop the cached masked render too — otherwise a re-uploaded document
    // keeps showing the PREVIOUS file's mask forever (ensureMasked is
    // fetch-once keyed on this cache)
    setMasked((p) => {
      const n = { ...p };
      delete n[docId];
      return n;
    });
  };

  // Lazily fetch the PII-masked preview for a document (first hover only, on
  // EITHER the upload step or Review & Submit — same cache, so hovering once
  // on either page makes the other instant).
  //
  // Masks the same auto-oriented/deskewed/contrast-corrected image the OCR
  // model actually read (upload's `cleanedPreview`), not the borrower's raw
  // photo — the redaction-pattern OCR pass reads it more reliably, and it
  // avoids re-deriving a second, differently-cleaned version of the same
  // document. Falls back to the raw file for PDFs / when preprocessing
  // didn't run (cleanedPreview is null in both cases).
  const ensureMasked = (docId) => {
    const u = uploads[docId];
    if (!u?.file || masked[docId]) return;
    setMasked((p) => ({ ...p, [docId]: { loading: true } }));
    const toBlob = u.cleanedPreview
      ? fetch(u.cleanedPreview).then((r) => r.blob())
      : Promise.resolve(u.file);
    toBlob
      .then((src) => maskDocument(src, u.parsed || {}, docId))
      .then((res) => {
        setMasked((p) => ({ ...p, [docId]: { loading: false, src: res.masked_image, timings: res.timings } }));
        // fold the masking time into the document's audit trail
        setUploads((prev) => (prev[docId] ? {
          ...prev,
          [docId]: { ...prev[docId], timings: { ...(prev[docId].timings || {}), ...(res.timings || {}) } },
        } : prev));
      })
      .catch((err) => {
        console.error(`Masking failed for ${docId}:`, err);
        setMasked((p) => ({ ...p, [docId]: { loading: false, error: true } }));
      });
  };

  const allRequiredUploaded = product
    ? product.documents.filter((d) => d.required).every((d) => {
        const u = uploads[d.id];
        return u && u.status === "completed";
      })
    : false;

  const allConsents = Object.values(consent).filter(Boolean).length >= 5;

  // Documents eligible for cross-checks and autofill: automated + successfully read.
  const verifiableDocs = {};
  Object.entries(uploads).forEach(([docId, u]) => {
    if (u.automated && u.parsed && (u.status === "review" || u.status === "completed")) {
      const doc = product?.documents.find((d) => d.id === docId);
      verifiableDocs[docId] = { parsed: u.parsed, label: doc ? doc.label : docId };
    }
  });
  const crossChecks = crossVerifyDocuments(verifiableDocs);
  const autofill = collectAutofillCandidates(verifiableDocs);

  // For the final review, the same fact (e.g. "Full Name") is often read off
  // several documents — PAN, Aadhaar, and a bank statement all say who you
  // are. Show each unique field once, in the same priority order autofill
  // uses, so the applicant's own ID documents win over a bank/business doc —
  // but keep a source tag so it's clear which document it actually came from.
  const DOC_PRIORITY = ["pan", "aadhaar", "salary_slip", "bank_statement", "itr", "form16"];
  const dedupedDocFields = (() => {
    const completed = Object.entries(uploads)
      .filter(([, u]) => u.status === "completed" && u.extracted)
      .sort(([a], [b]) => {
        const ra = DOC_PRIORITY.indexOf(a), rb = DOC_PRIORITY.indexOf(b);
        return (ra === -1 ? 99 : ra) - (rb === -1 ? 99 : rb);
      });
    const seen = new Map();
    for (const [docId, u] of completed) {
      const doc = product?.documents.find((d) => d.id === docId);
      const docLabel = doc ? doc.label : docId;
      for (const [field, data] of Object.entries(u.extracted)) {
        if (!seen.has(field)) seen.set(field, { field, data, docLabel });
      }
    }
    return [...seen.values()];
  })();

  // Entering the personal-details step: pre-fill any field that has exactly one
  // candidate from the documents and hasn't been typed into yet. Conflicting
  // candidates are left empty — the user picks via the chips under the field.
  useEffect(() => {
    setStepErrors({});
    if (step !== 3) return;
    setFormData((prev) => {
      const next = { ...prev };
      for (const [fid, cands] of Object.entries(autofill)) {
        if (cands.length === 1 && !next[fid]) next[fid] = cands[0].value;
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  // Entering Review & Submit: run the deferred integrity analysis (tampering +
  // cleaned render) for every automated document, then — once a document comes
  // back flagged (MEDIUM/HIGH) — load its plain-language summary and annotated
  // render, so the reviewer sees exactly what and where with no clicks. Every
  // fetcher is fetch-once (no-ops if already loaded/loading), and the effect
  // re-runs as `uploads` updates, which is what chains explain/locate behind
  // the integrity result without a manual callback pyramid.
  useEffect(() => {
    if (step !== 4 || !product) return;
    for (const d of product.documents) {
      const u = uploads[d.id];
      if (!u?.file) continue;
      if (!u.integrityDone) {
        fetchIntegrity(d.id);
        continue;
      }
      if (!u.tampering || u.tampering.risk_tier === "LOW") continue;
      fetchExplain(d.id);
      fetchLocate(d.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, uploads]);

  // Entering Review & Submit: run the cross-document face check — every kept
  // file goes to /face/verify in one call; the backend finds the face on each
  // document (any orientation) and reports whether the same person appears on
  // all of them. Cached per exact file set, so revisiting the step is free
  // unless a document was replaced.
  useEffect(() => {
    if (step !== 4 || !product) return;
    const entries = product.documents
      .map((d) => ({ d, u: uploads[d.id] }))
      .filter(({ u }) => u?.file && (u.status === "completed" || u.status === "review"));
    if (entries.length === 0) return;
    const key = entries.map(({ d, u }) => `${d.id}:${u.fileName || ""}`).join("|");
    if (faceCheck && faceCheck.key === key && (faceCheck.loading || faceCheck.data)) return;
    setFaceCheck({ key, loading: true, error: null, data: null });
    verifyFaces(entries.map(({ d, u }) => ({ docType: d.id, file: u.file })))
      .then((data) => setFaceCheck({ key, loading: false, error: null, data }))
      .catch((err) => {
        console.error("Face verification failed:", err);
        setFaceCheck({ key, loading: false, error: "Couldn't run the identity (face) check.", data: null });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  // Persist the whole application: the database rows every officer / head screen
  // reads, plus the audit store (storage/ folder + applications.xlsx). Sends every
  // automated document's original file, the masked render if the hover preview
  // already made one, the extracted fields, the per-stage timings and the
  // tampering + face results. Documents without a masked render are masked by the
  // API in the background AFTER it answers (api/app/mask_jobs.py) — masking five
  // documents here first used to keep the borrower on "Saving…" for minutes.
  const persistApplication = async (id, ctx) => {
    const fileToDataURL = (file) =>
      new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = reject;
        r.readAsDataURL(file);
      });

    const automated = (product?.documents || [])
      .map((d) => ({ d, u: uploads[d.id] }))
      .filter(({ u }) => u?.file && u?.automated && (u.status === "completed" || u.status === "review"));
    if (automated.length === 0) return { ok: false, error: "no automated documents to store" };

    const documents = await Promise.all(
      automated.map(async ({ d, u }) => {
        // masked render: only if the hover preview already made one — else the API masks it later
        const maskedSrc = masked[d.id]?.src || null;
        let original_b64 = null;
        try {
          original_b64 = await fileToDataURL(u.file);
        } catch (e) {
          console.error(`could not read original for ${d.id}:`, e);
        }
        return {
          doc_type: d.id,
          label: d.label,
          file_name: u.fileName || u.file.name || "",
          mime: u.file.type || "",
          uploaded_at: u.uploadedAt || "",
          fields: u.parsed || {},
          original_b64,
          masked_b64: maskedSrc,
          timings: { ...(u.timings || {}), ...(masked[d.id]?.timings || {}) },
          tampering: u.tampering || null,
        };
      })
    );

    const fc = faceCheck?.data || null;
    const payload = {
      app_id: id,
      case_id: caseId,
      applicant_name: formData.name || "",
      loan_type: product?.label || "",
      submitted_at: new Date().toISOString(),
      face_check: fc
        ? { overall: fc.overall, summary: fc.summary, time_ms: fc.timings?.face_ms }
        : null,
      documents,
      loan: {
        amount: positive(formData.loanAmount),
        tenure_months: positive(formData.tenure),
        purpose: formData.purpose || null,
        monthly_income: positive(formData.monthlyIncome),
        existing_emi: positive(formData.existingEmi),
        employment_type: formData.employmentType || null,
      },
      applicant: {
        name: formData.name || "", mobile: formData.mobile || "", email: formData.email || "",
        dob: formData.dob || "", city: formData.city || "", state: formData.state || "", pincode: formData.pincode || "",
      },
      // the five "Consent & Declaration" boxes on this screen (all are required before Submit is enabled);
      // the policy check needs the data-collection and credit-bureau consents on record
      consent: {
        information_correct: !!consent[0],
        data_collection: !!consent[1],
        credit_bureau_pull: !!consent[2],
        communication: !!consent[3],
        no_guarantee_understood: !!consent[4],
        accepted_at: new Date().toISOString(),
        captured_by: isStaff ? "staff_on_behalf_of_customer" : "borrower",
      },
      // everything else the applicant answered (product-specific fields)
      form: Object.fromEntries(Object.entries(formData).filter(([k]) =>
        !FORM_CORE_KEYS.includes(k) && (product?.fields || []).some((f) => f.id === k))),
    };
    const res = await storeApplication(payload, ctx);
    if (!res?.ok) console.error("Application storage error:", res?.error);
    else console.log(`Stored application ${id}: ${res.rows} rows -> ${res.dir}`);
    return res;
  };

  // Submit = one request. Success is shown only once the application is really in the
  // database (that is what the officer's queue reads); a failure stays on screen with
  // Retry instead of redirecting to a list the application never reached.
  const submitTimer = useRef(null);
  useEffect(() => () => clearTimeout(submitTimer.current), []);
  const handleSubmit = () => {
    const id = appId || "APP-" + String(Date.now()).slice(-8);   // a retry keeps the same number
    setAppId(id);
    setSubmitted(true);
    setStoreStatus({ state: "saving", started: Date.now() });
    // who is submitting, fixed now: the user may switch role while this runs
    const ctx = { caseId, role, actorId: session?.user_id };
    persistApplication(id, ctx)
      .then((res) => {
        if (!res?.ok) throw new Error(res?.error || "the documents could not be stored");
        if (res.sql_ok === false) throw new Error(`the database did not accept it (${res.sql_error || "unknown error"})`);
        setStoreStatus({ state: "ok", rows: res.rows, masking: res.masking_in_background || 0 });
        submitTimer.current = setTimeout(() => setPage(listPage), 2500);
      })
      .catch((e) => {
        console.error("persistApplication failed:", e);
        setStoreStatus({ state: "error", error: String(e?.message || e) });
      });
  };
  // a slow save (very large files) gets a note after 20 s rather than a silent spinner
  const [slowSave, setSlowSave] = useState(false);
  useEffect(() => {
    if (storeStatus?.state !== "saving") { setSlowSave(false); return undefined; }
    const t = setTimeout(() => setSlowSave(true), 20000);
    return () => clearTimeout(t);
  }, [storeStatus]);

  const loanAmount = Number(formData.loanAmount) || 0;
  const tenure = Number(formData.tenure) || 0;
  const emi = calcEMI(loanAmount, tenure);
  const monthlyIncome = Number(formData.monthlyIncome) || 0;
  const eligibilityEstimate = monthlyIncome * 4;

  const loanSpecificFields = product
    ? product.fields.filter(
        (f) => !["loanAmount", "tenure", "monthlyIncome", "existingEmi", "employmentType"].includes(f.id)
      )
    : [];

  const required = product ? product.documents.filter((d) => d.required) : [];
  const optional = product ? product.documents.filter((d) => !d.required) : [];

  // flag: ok → green check · warn → amber alert · error → red x (bad format) ·
  // missing → red "Not found" (expected on this document but not readable)
  const renderExtractedFields = (extracted) =>
    Object.entries(extracted).map(([field, data]) => {
      const missing = data.value === null || data.value === undefined || data.value === "";
      const flag = data.flag || "ok";
      return (
        <div
          key={field}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 11,
            padding: "4px 0",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <span style={{ color: "var(--text3)", minWidth: 120 }}>{field}</span>
          {missing ? (
            <span style={{ color: "var(--red)", fontStyle: "italic", fontSize: 11 }}>
              Not found — couldn't read this from your document
            </span>
          ) : (
            <span
              style={{
                color: flag === "error" ? TIER.bad.color : "var(--text1)",
                fontWeight: 500,
                fontFamily: "var(--font-mono)",
                fontSize: 11,
              }}
            >
              {String(data.value)}
            </span>
          )}
          <span style={{ marginLeft: "auto" }}>
            {missing || flag === "error" ? (
              <Icon d={Icons.x} size={12} style={{ color: TIER.bad.color }} />
            ) : flag === "warn" ? (
              <Icon d={TIER.warn.icon} size={12} style={{ color: TIER.warn.color }} />
            ) : (
              <Icon d={TIER.good.icon} size={12} style={{ color: TIER.good.color }} />
            )}
          </span>
        </div>
      );
    });

  // Inline validation message under a form field (set when Next is clicked).
  const ErrText = ({ id }) =>
    stepErrors[id] ? (
      <div style={{ fontSize: 10, color: "var(--red)", marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Icon d={Icons.alert} size={10} style={{ color: "var(--red)", flexShrink: 0 }} />
        {stepErrors[id]}
      </div>
    ) : null;

  // Under a personal-details input: shows where an auto-filled value came from,
  // or — when documents disagree — lets the user pick which value to use.
  const AutofillChips = ({ fieldId }) => {
    const cands = autofill[fieldId] || [];
    if (cands.length === 0) return null;
    if (cands.length === 1) {
      if (formData[fieldId] !== cands[0].value) return null;
      return (
        <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
          <Icon d={Icons.check} size={10} style={{ color: "var(--green)" }} /> Filled from your {cands[0].source}
        </div>
      );
    }
    return (
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
        <span style={{ fontSize: 10, color: "var(--text3)" }}>Your documents show different values — pick one:</span>
        {cands.map((c) => (
          <button
            key={c.key}
            type="button"
            style={{
              fontSize: 10,
              padding: "3px 10px",
              borderRadius: 999,
              cursor: "pointer",
              border: `1px solid ${formData[fieldId] === c.value ? "var(--blue)" : "var(--border)"}`,
              color: formData[fieldId] === c.value ? "var(--blue)" : "var(--text2)",
              background: formData[fieldId] === c.value ? "var(--blue-tint)" : "var(--surface)",
            }}
            onClick={() => updateField(fieldId, c.value)}
          >
            {c.value} · {c.source}
          </button>
        ))}
      </div>
    );
  };

  if (!loanTypeId || !product) {
    return (
      <div>
        <div className="page-header">
          <div>
            <div className="page-title">New Loan Application</div>
            <div className="page-sub">Choose the loan that fits your needs</div>
          </div>
        </div>
        <div className="card">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))",
              gap: 12,
            }}
          >
            {LOAN_TYPE_LIST.map((lt) => (
              <div
                key={lt.id}
                onClick={() => handleSelectLoanType(lt.id)}
                style={{
                  padding: "18px 16px",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  background: "var(--surface)",
                  cursor: "pointer",
                  textAlign: "center",
                  transition: "all 0.15s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = lt.color;
                  e.currentTarget.style.background = lt.color + "08";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--border)";
                  e.currentTarget.style.background = "var(--surface)";
                }}
              >
                <div style={{ fontSize: 32, marginBottom: 8 }}><Icon d={lt.icon} size={32} /></div>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--text1)",
                    marginBottom: 4,
                  }}
                >
                  {lt.label}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--text3)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {lt.documents.length} documents needed
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      {!submitted && (
        <>
          {isStaff && (
            <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:12,padding:"8px 12px",background:"var(--surface2)",borderRadius:8}}>
              <Icon d={Icons.chevronL} size={12} style={{color:"var(--text3)",cursor:"pointer"}} onClick={()=>setPage(listPage)} />
              <span style={{fontSize:11,color:"var(--text3)",fontFamily:"var(--font-mono)"}}>New</span>
              <Icon d={Icons.chevronR} size={12} style={{color:"var(--text3)"}} />
              <span style={{fontSize:11,color:"var(--blue)",fontFamily:"var(--font-mono)"}}>APP-{role==="head"?"HQ":"OFF"}-{draftId}</span>
            </div>
          )}
          <div className="page-header">
            <div>
              <div className="page-title">New Application — {product.label}</div>
              <div className="page-sub">{product.purpose}</div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {product.secured && (
                <span className="badge badge-blue" style={{ fontSize: 10 }}>
                  Secured Loan
                </span>
              )}
              {product.complexity === "High" && (
                <span
                  className="badge"
                  style={{
                    fontSize: 10,
                    background: "var(--amber-tint-strong)",
                    color: "var(--amber)",
                    border: "1px solid var(--amber-tint-strong)",
                  }}
                >
                  ⏱ 3-5 business days
                </span>
              )}
            </div>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              marginBottom: 8,
              padding: "10px 14px",
              background: "var(--surface)",
              borderRadius: 8,
              border: "1px solid var(--border)",
            }}
          >
            <span style={{ fontSize: 24 }}><Icon d={product.icon} size={24} /></span>
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: "var(--text1)",
                }}
              >
                {product.label}
              </div>
              <div style={{ fontSize: 11, color: "var(--text3)" }}>
                {product.useCase}
              </div>
            </div>
            <button
              className="btn btn-ghost"
              style={{ fontSize: 11, padding: "4px 10px", whiteSpace: "nowrap" }}
              onClick={handleChangeLoanType}
            >
              <Icon d={Icons.x} size={11} /> Change
            </button>
          </div>

          <div
            style={{
              display: "flex",
              gap: 0,
              marginBottom: 20,
              alignItems: "stretch",
            }}
          >
            {STEPS.map((s, i) => {
              const status =
                i + 1 < step
                  ? "completed"
                  : i + 1 === step
                  ? "active"
                  : "future";
              return (
                <div
                  key={s.num}
                  style={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      width: "100%",
                      justifyContent: "center",
                    }}
                  >
                    <div
                      style={{
                        flex: 1,
                        height: 2,
                        background:
                          i === 0
                            ? "transparent"
                            : status === "future"
                            ? "var(--border)"
                            : status === "active" &&
                              STEPS[i - 1] &&
                              step > 1
                            ? "var(--green)"
                            : status === "completed"
                            ? "var(--green)"
                            : "var(--border)",
                        marginRight: 0,
                      }}
                    />
                    <div
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 700,
                        fontFamily: "var(--font-mono)",
                        flexShrink: 0,
                        background:
                          status === "completed"
                            ? "var(--green)"
                            : status === "active"
                            ? "var(--blue)"
                            : "var(--surface2)",
                        color: status === "future" ? "var(--text3)" : "var(--white)",
                        border:
                          status === "future"
                            ? "1px solid var(--border)"
                            : "none",
                      }}
                    >
                      {status === "completed" ? (
                        <Icon d={Icons.check} size={14} />
                      ) : (
                        s.num
                      )}
                    </div>
                    <div
                      style={{
                        flex: 1,
                        height: 2,
                        background:
                          i === STEPS.length - 1
                            ? "transparent"
                            : status === "completed"
                            ? "var(--green)"
                            : "var(--border)",
                        marginLeft: 0,
                      }}
                    />
                  </div>
                  <div
                    className="step-label"
                    style={{
                      fontSize: 10,
                      color:
                        status === "active"
                          ? "var(--text1)"
                          : "var(--text3)",
                      fontWeight: status === "active" ? 600 : 400,
                      marginTop: 6,
                      textAlign: "center",
                      lineHeight: 1.3,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {s.label}
                  </div>
                </div>
              );
            })}
          </div>

          {step === 3 && (
            <div className="card">
              <div className="section-head">Personal Information</div>
              <div className="form-row">
                <div className="form-group">
                  <div className="form-label">Full Name</div>
                  <input
                    className="form-input"
                    placeholder="Enter your full name"
                    value={formData.name || ""}
                    onChange={(e) => updateField("name", e.target.value)}
                  />
                  <ErrText id="name" />
                  <AutofillChips fieldId="name" />
                </div>
                <div className="form-group">
                  <div className="form-label">Mobile Number</div>
                  <input
                    className="form-input"
                    placeholder="Enter mobile number"
                    value={formData.mobile || ""}
                    onChange={(e) => updateField("mobile", e.target.value)}
                  />
                  <ErrText id="mobile" />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <div className="form-label">Email</div>
                  <input
                    className="form-input"
                    type="email"
                    placeholder="Enter email address"
                    value={formData.email || ""}
                    onChange={(e) => updateField("email", e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <div className="form-label">Date of Birth</div>
                  <input
                    className="form-input"
                    type="date"
                    value={formData.dob || ""}
                    onChange={(e) => updateField("dob", e.target.value)}
                  />
                  <AutofillChips fieldId="dob" />
                </div>
              </div>
              <div className="form-row3">
                <div className="form-group">
                  <div className="form-label">City</div>
                  <input
                    className="form-input"
                    placeholder="Enter city"
                    value={formData.city || ""}
                    onChange={(e) => updateField("city", e.target.value)}
                  />
                  <AutofillChips fieldId="city" />
                </div>
                <div className="form-group">
                  <div className="form-label">State</div>
                  <input
                    className="form-input"
                    placeholder="Enter state"
                    value={formData.state || ""}
                    onChange={(e) => updateField("state", e.target.value)}
                  />
                  <AutofillChips fieldId="state" />
                </div>
                <div className="form-group">
                  <div className="form-label">Pincode</div>
                  <input
                    className="form-input"
                    placeholder="Enter pincode"
                    value={formData.pincode || ""}
                    onChange={(e) => updateField("pincode", e.target.value)}
                  />
                  <AutofillChips fieldId="pincode" />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <div className="form-label">Employment Type</div>
                  <select
                    className="form-input"
                    value={formData.employmentType || ""}
                    onChange={(e) => updateField("employmentType", e.target.value)}
                  >
                    <option value="">Select employment type</option>
                    {EMPLOYMENT_OPTIONS.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                  <AutofillChips fieldId="employmentType" />
                </div>
                <div className="form-group">
                  <div className="form-label">Monthly / Business Income</div>
                  <div style={{ position: "relative" }}>
                    <span
                      style={{
                        position: "absolute",
                        left: 10,
                        top: "50%",
                        transform: "translateY(-50%)",
                        fontSize: 13,
                        color: "var(--text3)",
                        pointerEvents: "none",
                        zIndex: 1,
                      }}
                    >
                      ₹
                    </span>
                    <input
                      className="form-input"
                      type="number"
                      min={0}
                      placeholder="Enter monthly income"
                      style={{ paddingLeft: 24 }}
                      value={formData.monthlyIncome || ""}
                      onChange={(e) => updateField("monthlyIncome", e.target.value)}
                    />
                  </div>
                  <AutofillChips fieldId="monthlyIncome" />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <div className="form-label">
                    Existing EMI (per month){" "}
                    <span style={{ color: "var(--text3)", fontWeight: 400 }}>
                      (optional)
                    </span>
                  </div>
                  <div style={{ position: "relative" }}>
                    <span
                      style={{
                        position: "absolute",
                        left: 10,
                        top: "50%",
                        transform: "translateY(-50%)",
                        fontSize: 13,
                        color: "var(--text3)",
                        pointerEvents: "none",
                        zIndex: 1,
                      }}
                    >
                      ₹
                    </span>
                    <input
                      className="form-input"
                      type="number"
                      min={0}
                      placeholder="Enter existing EMI"
                      style={{ paddingLeft: 24 }}
                      value={formData.existingEmi || ""}
                      onChange={(e) => updateField("existingEmi", e.target.value)}
                    />
                  </div>
                </div>
              </div>

              <div className="section-head" style={{ marginTop: 20 }}>
                Loan Details
              </div>
              <div className="form-row">
                <div className="form-group">
                  <div className="form-label">Requested Loan Amount</div>
                  <div style={{ position: "relative" }}>
                    <span
                      style={{
                        position: "absolute",
                        left: 10,
                        top: "50%",
                        transform: "translateY(-50%)",
                        fontSize: 13,
                        color: "var(--text3)",
                        pointerEvents: "none",
                        zIndex: 1,
                      }}
                    >
                      ₹
                    </span>
                    <input
                      className="form-input"
                      type="number"
                      min={0}
                      placeholder={formatCurrency(product.minAmount) + " – " + formatCurrency(product.maxAmount)}
                      style={{ paddingLeft: 24 }}
                      value={formData.loanAmount || ""}
                      onChange={(e) => updateField("loanAmount", e.target.value)}
                    />
                  </div>
                  <ErrText id="loanAmount" />
                </div>
                <div className="form-group">
                  <div className="form-label">Preferred Tenure</div>
                  <select
                    className="form-input"
                    value={formData.tenure || ""}
                    onChange={(e) => updateField("tenure", e.target.value)}
                  >
                    <option value="">Select tenure</option>
                    {generateTenureOptionsForProduct().map((t) => (
                      <option key={t} value={t}>
                        {t} months
                      </option>
                    ))}
                  </select>
                  <ErrText id="tenure" />
                </div>
              </div>

              {loanSpecificFields.length > 0 && (
                <>
                  {/* two fields per row; each field exactly once (the old pairing rendered
                      every odd-indexed field a second time, on its own row) */}
                  {Array.from({ length: Math.ceil(loanSpecificFields.length / 2) },
                              (_, r) => loanSpecificFields.slice(r * 2, r * 2 + 2)).map((fields) => {
                    return (
                      <div className="form-row" key={fields[0].id}>
                        {fields.map((field) =>
                          field ? (
                            <div className="form-group" key={field.id}>
                              <div className="form-label">
                                {field.label}
                                {!field.required && (
                                  <span
                                    style={{
                                      color: "var(--text3)",
                                      fontWeight: 400,
                                    }}
                                  >
                                    {" "}
                                    (optional)
                                  </span>
                                )}
                              </div>
                              {field.type === "select" ? (
                                <select
                                  className="form-input"
                                  value={formData[field.id] || ""}
                                  onChange={(e) =>
                                    updateField(field.id, e.target.value)
                                  }
                                >
                                  <option value="">Select {field.label}</option>
                                  {(field.options || []).map((o) => (
                                    <option key={o} value={o}>
                                      {o}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <div style={{ position: "relative" }}>
                                  {field.prefix === "₹" && (
                                    <span
                                      style={{
                                        position: "absolute",
                                        left: 10,
                                        top: "50%",
                                        transform: "translateY(-50%)",
                                        fontSize: 13,
                                        color: "var(--text3)",
                                        pointerEvents: "none",
                                        zIndex: 1,
                                      }}
                                    >
                                      ₹
                                    </span>
                                  )}
                                  <input
                                    className="form-input"
                                    type={field.type === "number" ? "number" : "text"}
                                    min={field.type === "number" ? 0 : undefined}
                                    placeholder={field.placeholder || "Enter " + field.label}
                                    style={
                                      field.prefix === "₹"
                                        ? { paddingLeft: 24 }
                                        : {}
                                    }
                                    value={formData[field.id] || ""}
                                    onChange={(e) =>
                                      updateField(field.id, e.target.value)
                                    }
                                  />
                                </div>
                              )}
                              <AutofillChips fieldId={field.id} />
                              <ErrText id={field.id} />
                              {field.help && (
                                <div
                                  style={{
                                    fontSize: 10,
                                    color: "var(--text3)",
                                    marginTop: 4,
                                    fontStyle: "italic",
                                  }}
                                >
                                  {field.help}
                                </div>
                              )}
                            </div>
                          ) : null
                        )}
                      </div>
                    );
                  })}
                </>
              )}

              <div
                style={{
                  marginTop: 16,
                  padding: "12px 16px",
                  background: "var(--blue-tint)",
                  border: "1px solid var(--blue-tint-strong)",
                  borderRadius: 8,
                }}
              >
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--blue)",
                    marginBottom: 4,
                  }}
                >
                  Eligibility Estimate
                </div>
                <div style={{ fontSize: 12, color: "var(--text2)" }}>
                  Based on the information provided, you may be eligible for up to{" "}
                  <strong>{formatCurrency(eligibilityEstimate)}</strong>
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--text3)",
                    marginTop: 4,
                  }}
                >
                  This is an initial estimate and does not guarantee approval.
                </div>
              </div>

              {product.id === "gold" && (
                <div
                  style={{
                    marginTop: 12,
                    padding: "10px 14px",
                    background: "var(--amber-tint)",
                    border: "1px solid var(--amber-tint-strong)",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "var(--amber)",
                  }}
                >
                  <Icon d={Icons.gem} size={14} style={{color:"var(--amber)"}} /> Final loan amount depends on physical gold valuation at
                  branch
                </div>
              )}
              {product.id === "home" && (
                <div
                  style={{
                    marginTop: 12,
                    padding: "10px 14px",
                    background: "var(--blue-tint)",
                    border: "1px solid var(--blue-tint-strong)",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "var(--blue)",
                  }}
                >
                  <Icon d={Icons.house} size={14} style={{color:"var(--blue)"}} /> Property verification will be required after initial
                  approval
                </div>
              )}

              {Object.keys(stepErrors).length > 0 && (
                <div
                  style={{
                    marginTop: 16,
                    padding: "10px 14px",
                    background: "var(--red-tint)",
                    border: "1px solid var(--red-tint-strong)",
                    borderRadius: 8,
                    fontSize: 12,
                    color: "var(--text1)",
                    lineHeight: 1.6,
                  }}
                >
                  <Icon d={Icons.alert} size={13} style={{ color: "var(--red)", marginRight: 6 }} />
                  <strong>Almost there —</strong> please fix the highlighted field
                  {Object.keys(stepErrors).length > 1 ? "s" : ""} above:{" "}
                  {Object.values(stepErrors).join(" · ")}
                </div>
              )}

              <div
                style={{
                  marginTop: 20,
                  display: "flex",
                  gap: 8,
                  justifyContent: "flex-end",
                }}
              >
                <button className="btn btn-ghost" onClick={() => setStep(2)}>
                  Back
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => {
                    const errs = {};
                    if (!formData.name?.trim()) errs.name = "Please enter your full name";
                    if (!/^[6-9]\d{9}$/.test(formData.mobile || ""))
                      errs.mobile = "Enter a valid 10-digit mobile number";
                    const amt = Number(formData.loanAmount);
                    const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
                    if (!(amt > 0))
                      errs.loanAmount = "Enter the loan amount you need";
                    else if (product.minAmount && amt < product.minAmount)
                      errs.loanAmount = `The minimum for a ${product.label} is ${inr(product.minAmount)}`;
                    else if (product.maxAmount && amt > product.maxAmount)
                      errs.loanAmount = `The maximum for a ${product.label} is ${inr(product.maxAmount)}`;
                    if (!formData.tenure) errs.tenure = "Select how long you want to repay over";
                    for (const f of loanSpecificFields) {
                      if (f.required && !String(formData[f.id] ?? "").trim()) errs[f.id] = `Enter the ${f.label.toLowerCase()}`;
                    }
                    // "Minimum 20% of on-road price", and the loan should not exceed the price
                    const price = Number(formData.onRoadPrice), down = Number(formData.downPayment);
                    if (price > 0 && formData.downPayment !== undefined && formData.downPayment !== "" &&
                        /20%/.test(loanSpecificFields.find((f) => f.id === "downPayment")?.help || "") && down < price * 0.2)
                      errs.downPayment = `At least 20% of the on-road price (${inr(Math.ceil(price * 0.2))})`;
                    if (price > 0 && amt > price)
                      errs.loanAmount = `The loan cannot be more than the on-road price (${inr(price)})`;
                    setStepErrors(errs);
                    if (Object.keys(errs).length === 0) setStep(4);
                  }}
                >
                  Next: Review & Submit{" "}
                  <Icon d={Icons.chevronR} size={13} />
                </button>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="card">
              <div className="card-title" style={{ marginBottom: 16 }}>
                Documents Required for {product.label}
              </div>

              {required.length > 0 && (
                <>
                  <div
                    className="section-head"
                    style={{ fontSize: 12, marginBottom: 10 }}
                  >
                    Required Documents
                  </div>
                  {required.map((d) => (
                    <div
                      key={d.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        padding: "10px 12px",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        marginBottom: 8,
                        background: "var(--surface)",
                      }}
                    >
                      <Icon d={DOC_ICON(d.label)} size={16} />
                      <div style={{ flex: 1 }}>
                        <div
                          style={{
                            fontSize: 12,
                            fontWeight: 500,
                            color: "var(--text1)",
                          }}
                        >
                          {d.label}
                        </div>
                        <div
                          style={{
                            fontSize: 10,
                            color: "var(--text3)",
                            marginTop: 2,
                          }}
                        >
                          {d.why}
                        </div>
                      </div>
                      <span
                        className="badge badge-red"
                        style={{ fontSize: 10, flexShrink: 0 }}
                      >
                        Required
                      </span>
                    </div>
                  ))}
                </>
              )}

              {optional.length > 0 && (
                <>
                  <div
                    className="section-head"
                    style={{
                      fontSize: 12,
                      marginBottom: 10,
                      marginTop: 16,
                    }}
                  >
                    Optional Documents
                  </div>
                  {optional.map((d) => (
                    <div
                      key={d.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        padding: "10px 12px",
                        border: "1px dashed var(--border)",
                        borderRadius: 8,
                        marginBottom: 8,
                        background: "var(--surface)",
                      }}
                    >
                      <Icon d={DOC_ICON(d.label)} size={16} />
                      <div style={{ flex: 1 }}>
                        <div
                          style={{
                            fontSize: 12,
                            fontWeight: 500,
                            color: "var(--text1)",
                          }}
                        >
                          {d.label}
                        </div>
                        <div
                          style={{
                            fontSize: 10,
                            color: "var(--text3)",
                            marginTop: 2,
                          }}
                        >
                          {d.why}
                        </div>
                      </div>
                      <span
                        className="badge badge-gray"
                        style={{ fontSize: 10, flexShrink: 0 }}
                      >
                        Optional
                      </span>
                    </div>
                  ))}
                </>
              )}

              <div style={{ marginTop: 16 }}>
                <div className="section-head" style={{ fontSize: 12 }}>
                  Information We'll Collect From These Documents
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {product.fields.map((f) => (
                    <span key={f.id} className="badge badge-blue" style={{ fontSize: 10 }}>
                      {f.label}
                    </span>
                  ))}
                </div>
              </div>

              <div
                style={{
                  marginTop: 20,
                  display: "flex",
                  gap: 8,
                  justifyContent: "flex-end",
                }}
              >
                <button
                  className="btn btn-primary"
                  onClick={() => setStep(2)}
                >
                  Next: Upload Documents{" "}
                  <Icon d={Icons.chevronR} size={13} />
                </button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="card">
              <div className="card-title" style={{ marginBottom: 16 }}>
                Upload Your Documents
              </div>

              {product.documents.map((d) => {
                const u = uploads[d.id];
                return (
                  <div
                    key={d.id}
                    className="card-sm"
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      overflow: "hidden",
                      padding: 0,
                      marginBottom: 12,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "10px 12px",
                        background: "var(--surface2)",
                      }}
                    >
                      <Icon d={DOC_ICON(d.label)} size={16} />
                      <span
                        style={{
                          fontSize: 13,
                          color: "var(--text1)",
                          fontWeight: 500,
                          flex: 1,
                        }}
                      >
                        {d.label}
                      </span>
                      <span
                        className={`badge ${
                          d.required ? "badge-red" : "badge-gray"
                        }`}
                        style={{ fontSize: 10 }}
                      >
                        {d.required ? "Required" : "Optional"}
                      </span>
                      {u && u.status === "completed" && (
                        <span
                          className="badge badge-green"
                          style={{ fontSize: 10 }}
                        >
                          <><Icon d={Icons.check} size={11} style={{color:"var(--green)"}} /> Confirmed</>
                        </span>
                      )}
                      {u && u.status === "review" && (
                        <span
                          className="badge"
                          style={{ fontSize: 10, background: "var(--amber-tint-strong)", color: "var(--amber)" }}
                        >
                          Please Check & Confirm
                        </span>
                      )}
                      {u && u.status === "processing" && (
                        <span
                          className="badge"
                          style={{
                            fontSize: 10,
                            background: "var(--blue-tint)",
                            color: "var(--blue)",
                          }}
                        >
                          <><span className="spinner" style={{width:10,height:10}}/> {u.automated ? "Reading" : "Uploading"}</>
                        </span>
                      )}
                      {u && u.status === "error" && (
                        <span
                          className="badge"
                          style={{ fontSize: 10, background: "var(--red-tint-strong)", color: "var(--red)" }}
                        >
                          Failed
                        </span>
                      )}
                      {u && u.status === "wrong_doc" && (
                        <span
                          className="badge"
                          style={{ fontSize: 10, background: "var(--red-tint-strong)", color: "var(--red)" }}
                        >
                          Wrong Document?
                        </span>
                      )}
                    </div>

                    {u && u.status === "processing" && (
                      <div
                        style={{
                          padding: 16,
                          textAlign: "center",
                          color: "var(--blue)",
                          fontSize: 12,
                        }}
                      >
                        <div
                          style={{
                            display: "inline-block",
                            width: 20,
                            height: 20,
                            border: "2px solid var(--blue)",
                            borderTopColor: "transparent",
                            borderRadius: "50%",
                            animation: "spin 0.8s linear infinite",
                            marginRight: 8,
                            verticalAlign: "middle",
                          }}
                        />
                        {u.automated ? "Reading your document..." : "Uploading..."}
                      </div>
                    )}

                    {u && u.status === "error" && (
                      <div style={{ padding: "10px 12px" }}>
                        <div
                          style={{
                            fontSize: 12,
                            color: "var(--red)",
                            marginBottom: 10,
                          }}
                        >
                          <Icon d={Icons.alert} size={12} style={{ marginRight: 4 }} />
                          We couldn't read this document. Please try again.
                        </div>
                        <button
                          className="btn btn-ghost"
                          style={{ fontSize: 11, padding: "5px 12px" }}
                          onClick={() => handleRemove(d.id)}
                        >
                          <Icon d={Icons.x} size={11} /> Try Again
                        </button>
                      </div>
                    )}

                    {u && u.status === "wrong_doc" && (
                      <div style={{ padding: "10px 12px" }}>
                        <div
                          style={{
                            padding: "10px 12px",
                            background: "var(--red-tint)",
                            border: "1px solid var(--red-tint-strong)",
                            borderRadius: 6,
                            fontSize: 12,
                            color: "var(--text1)",
                            marginBottom: 10,
                            lineHeight: 1.5,
                          }}
                        >
                          <Icon d={Icons.alert} size={13} style={{ color: "var(--red)", marginRight: 6 }} />
                          This doesn't look like a <strong>{d.label}</strong>. It may be the
                          wrong file, or the photo may be too unclear to read. Please upload
                          a clear photo of your {d.label}.
                        </div>
                        <button
                          className="btn btn-primary"
                          style={{ fontSize: 11, padding: "5px 12px" }}
                          onClick={() => handleRemove(d.id)}
                        >
                          <Icon d={Icons.upload} size={11} /> Upload Correct Document
                        </button>
                      </div>
                    )}

                    {u && u.status === "review" && u.extracted && (
                      <div style={{ padding: "10px 12px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                          <FileThumb upload={u} size={40} />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text1)" }}>
                              Here's what we found. Please check it's correct.
                            </div>
                            {u.fileName && (
                              <div style={{ fontSize: 9.5, color: "var(--text3)" }}>{u.fileName}</div>
                            )}
                          </div>
                        </div>
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                            marginBottom: 10,
                            marginTop: 10,
                          }}
                        >
                          {renderExtractedFields(u.extracted)}
                        </div>
                        <div style={{ display: "flex", gap: 8 }}>
                          <button
                            className="btn btn-primary"
                            style={{ fontSize: 11, padding: "5px 12px" }}
                            onClick={() => handleConfirmExtracted(d.id)}
                          >
                            <Icon d={Icons.check} size={11} /> Yes, this is correct
                          </button>
                          <button
                            className="btn btn-ghost"
                            style={{ fontSize: 11, padding: "5px 12px" }}
                            onClick={() => handleRemove(d.id)}
                          >
                            <Icon d={Icons.x} size={11} /> No, upload again
                          </button>
                        </div>
                      </div>
                    )}

                    {u && u.status === "completed" && u.extracted && (
                      <div style={{ padding: "10px 12px" }}>
                        <div
                          style={{
                            fontSize: 11,
                            fontWeight: 600,
                            color: "var(--green)",
                            marginBottom: 8,
                          }}
                        >
                          ✓ You confirmed this is correct
                        </div>
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                            marginBottom: 8,
                          }}
                        >
                          {renderExtractedFields(u.extracted)}
                        </div>
                        <button
                          className="btn btn-ghost"
                          style={{ fontSize: 10, padding: "2px 8px" }}
                          onClick={() => handleRemove(d.id)}
                        >
                          <Icon d={Icons.x} size={10} /> Remove & Upload Again
                        </button>
                      </div>
                    )}

                    {!u && (
                      <div
                        style={{
                          padding: 16,
                          textAlign: "center",
                          background: "var(--surface)",
                        }}
                      >
                        <label
                          style={{
                            cursor: "pointer",
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "center",
                            gap: 6,
                          }}
                        >
                          <Icon
                            d={Icons.upload}
                            size={20}
                            style={{ color: "var(--text3)" }}
                          />
                          <span
                            style={{ fontSize: 12, color: "var(--text3)" }}
                          >
                            Click to upload{" "}
                            <span
                              style={{
                                color: "var(--blue)",
                                fontWeight: 500,
                              }}
                            >
                              {d.label}
                            </span>
                          </span>
                          <input
                            type="file"
                            style={{ display: "none" }}
                            accept="image/*,.pdf"
                            onChange={(e) =>
                              handleUpload(d.id, e.target.files[0])
                            }
                          />
                        </label>
                      </div>
                    )}
                  </div>
                );
              })}

              {crossChecks.length > 0 && (
                <div
                  className="card-sm"
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    padding: "12px 14px",
                    marginBottom: 12,
                    background: "var(--surface)",
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      color: "var(--text1)",
                      marginBottom: 8,
                    }}
                  >
                    Checking your documents against each other
                  </div>
                  {crossChecks.map((c, i) => (
                    <div
                      key={i}
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 8,
                        fontSize: 11,
                        padding: "5px 0",
                        borderBottom: i < crossChecks.length - 1 ? "1px solid var(--border)" : "none",
                      }}
                    >
                      {c.level === "ok" ? (
                        <Icon d={Icons.check} size={13} style={{ color: "var(--green)", flexShrink: 0, marginTop: 1 }} />
                      ) : c.level === "warn" ? (
                        <Icon d={Icons.alert} size={13} style={{ color: "var(--amber)", flexShrink: 0, marginTop: 1 }} />
                      ) : (
                        <Icon d={Icons.x} size={13} style={{ color: "var(--red)", flexShrink: 0, marginTop: 1 }} />
                      )}
                      <div>
                        <span
                          style={{
                            color: c.level === "error" ? "var(--red)" : c.level === "warn" ? "var(--amber)" : "var(--text1)",
                            fontWeight: 500,
                          }}
                        >
                          {c.title}
                        </span>
                        {c.detail && (
                          <div style={{ color: "var(--text3)", marginTop: 2, lineHeight: 1.4 }}>{c.detail}</div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {product.complexity === "High" && (
                <div
                  style={{
                    padding: "10px 14px",
                    background: "var(--amber-tint)",
                    border: "1px solid var(--amber-tint-strong)",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "var(--amber)",
                    marginBottom: 12,
                  }}
                >
                  Processing may take 3-5 business days for this loan type.
                </div>
              )}

              <div
                style={{
                  marginTop: 20,
                  display: "flex",
                  gap: 8,
                  justifyContent: "flex-end",
                }}
              >
                <button className="btn btn-ghost" onClick={() => setStep(1)}>
                  Back
                </button>
                <button
                  className="btn btn-primary"
                  disabled={!allRequiredUploaded}
                  onClick={() => setStep(3)}
                >
                  Next: Your Details <Icon d={Icons.chevronR} size={13} />
                </button>
              </div>
            </div>
          )}

          {step === 4 && (
            <>
              {/* ── Key facts hero ─────────────────────────────── */}
              <div
                className="card"
                style={{
                  background: `linear-gradient(135deg, ${product.color}14, var(--surface) 62%)`,
                  borderLeft: `4px solid ${product.color}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18, flexWrap: "wrap" }}>
                  <div
                    style={{
                      width: 42, height: 42, borderRadius: 11, background: product.color,
                      display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                    }}
                  >
                    <Icon d={product.icon} size={21} style={{ color: "#fff" }} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text1)", letterSpacing: "-0.2px" }}>
                      {product.label}
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text3)" }}>
                      One last look — check everything below, then submit.
                    </div>
                  </div>
                  <span
                    className={`badge ${product.secured ? "badge-blue" : "badge-green"}`}
                    style={{ marginLeft: "auto" }}
                  >
                    {product.secured ? "Secured" : "Unsecured"}
                  </span>
                </div>
                <div className="grid4">
                  {[
                    { k: "Requested Amount", v: formatCurrency(loanAmount) || "—", c: "var(--text1)" },
                    { k: "Estimated EMI", v: formatCurrency(emi) || "—", c: "var(--blue)" },
                    { k: "Tenure", v: tenure ? `${tenure} months` : "—", c: "var(--text1)" },
                    { k: "Monthly Income", v: formatCurrency(monthlyIncome) || "—", c: "var(--text1)" },
                  ].map((m) => (
                    <div key={m.k}>
                      <div
                        style={{
                          fontSize: 10, color: "var(--text3)", fontFamily: "var(--font-mono)",
                          textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4,
                        }}
                      >
                        {m.k}
                      </div>
                      <div style={{ fontSize: 18, fontWeight: 700, color: m.c, fontFamily: "var(--font-mono)", letterSpacing: "-0.5px" }}>
                        {m.v}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── Submission readiness ───────────────────────── */}
              <div className="card">
                <div className="section-head">
                  <Icon d={Icons.clipboardList} size={14} /> Submission Readiness
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {[
                    {
                      label: "Required documents",
                      done: allRequiredUploaded,
                      detail: `${required.filter((d) => uploads[d.id]?.status === "completed").length} of ${required.length} uploaded`,
                    },
                    {
                      label: "Document integrity",
                      done: (() => {
                        const pending = product.documents.filter((d) => {
                          const u = uploads[d.id];
                          return u?.file && u.automated && !u.integrityDone;
                        }).length;
                        const f = product.documents.filter((d) => uploads[d.id]?.tampering && uploads[d.id].tampering.risk_tier !== "LOW").length;
                        return pending === 0 && f === 0;
                      })(),
                      detail: (() => {
                        const pending = product.documents.filter((d) => {
                          const u = uploads[d.id];
                          return u?.file && u.automated && !u.integrityDone;
                        }).length;
                        if (pending > 0) return `Checking ${pending} document${pending > 1 ? "s" : ""}…`;
                        const f = product.documents.filter((d) => uploads[d.id]?.tampering && uploads[d.id].tampering.risk_tier !== "LOW").length;
                        return f === 0 ? "No issues flagged" : `${f} document${f > 1 ? "s" : ""} flagged for review`;
                      })(),
                    },
                    ...(faceCheck?.data ? [{
                      label: "Identity (face) match",
                      done: faceCheck.data.overall === "match",
                      detail: {
                        match: "Same person on all documents",
                        mismatch: "Face mismatch — must be reviewed",
                        uncertain: "Inconclusive — needs a human look",
                        insufficient: "Not enough faces to compare",
                      }[faceCheck.data.overall] || faceCheck.data.overall,
                    }] : []),
                    {
                      label: "Consent & declaration",
                      done: allConsents,
                      detail: `${Object.values(consent).filter(Boolean).length} of 5 accepted`,
                    },
                  ].map((r) => (
                    <div
                      key={r.label}
                      style={{
                        display: "flex", alignItems: "center", gap: 10, padding: "10px 12px",
                        background: r.done ? "var(--green-tint)" : "var(--amber-tint)",
                        border: `1px solid ${r.done ? "var(--green-tint-strong)" : "var(--amber)"}`,
                        borderRadius: 8,
                      }}
                    >
                      <Icon d={r.done ? Icons.check : Icons.alert} size={15}
                            style={{ color: r.done ? "var(--green)" : "var(--amber)", flexShrink: 0 }} />
                      <span style={{ fontSize: 12.5, fontWeight: 500, color: "var(--text1)" }}>{r.label}</span>
                      <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 600, color: r.done ? "var(--green)" : "var(--amber)" }}>
                        {r.detail}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── Applicant details ──────────────────────────── */}
              <div className="card">
                <div className="section-head">Applicant Details</div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: 8,
                  marginBottom: 16,
                }}
              >
                {[
                  { label: "Full Name", val: formData.name },
                  { label: "Mobile", val: formData.mobile },
                  { label: "Email", val: formData.email },
                  { label: "DOB", val: formData.dob },
                  { label: "City", val: formData.city },
                  { label: "State", val: formData.state },
                  { label: "Employment", val: formData.employmentType },
                  { label: "Monthly Income", val: formatCurrency(formData.monthlyIncome) },
                ].map(
                  (f) =>
                    f.val && (
                      <div
                        key={f.label}
                        style={{ fontSize: 11, padding: "4px 0" }}
                      >
                        <span
                          style={{
                            color: "var(--text3)",
                            display: "block",
                            fontSize: 10,
                          }}
                        >
                          {f.label}
                        </span>
                        <span
                          style={{
                            color: "var(--text1)",
                            fontWeight: 500,
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                          }}
                        >
                          {f.val}
                        </span>
                      </div>
                    )
                )}
              </div>

              </div>

              {/* ── Documents ──────────────────────────────────── */}
              <div className="card">
                <div className="section-head">Documents</div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  marginBottom: 16,
                }}
              >
                {product.documents.map((d) => {
                  const u = uploads[d.id];
                  const uploaded = u && u.status === "completed";
                  return (
                    <div
                      key={d.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "8px 10px",
                        background: uploaded
                          ? "var(--green-tint)"
                          : "transparent",
                        borderRadius: 6,
                        border: uploaded
                          ? "1px solid var(--green-tint-strong)"
                          : "1px solid var(--border)",
                        opacity: uploaded ? 1 : 0.5,
                      }}
                    >
                      <span
                        style={{
                          color: uploaded ? "var(--green)" : "var(--text3)",
                          fontSize: 14,
                        }}
                      >
                        {uploaded ? <Icon d={Icons.check} size={12} style={{color:"var(--green)"}} /> : <Icon d={Icons.circle} size={12} style={{color:"var(--text3)"}} />}
                      </span>
                      <span
                        style={{
                          fontSize: 12,
                          color: uploaded ? "var(--text1)" : "var(--text3)",
                        }}
                      >
                        {d.label}
                      </span>
                      {uploaded && (
                        <span
                          style={{
                            marginLeft: "auto",
                            fontSize: 10,
                            color: "var(--text3)",
                          }}
                        >
                          {u.fileName}
                        </span>
                      )}
                      {!uploaded && d.required && (
                        <span
                          style={{
                            marginLeft: "auto",
                            fontSize: 10,
                            color: "var(--red)",
                          }}
                        >
                          Pending
                        </span>
                      )}
                      {!uploaded && !d.required && (
                        <span
                          style={{
                            marginLeft: "auto",
                            fontSize: 10,
                            color: "var(--text3)",
                          }}
                        >
                          Not uploaded
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>

              {optional.some((d) => !uploads[d.id]) && (
                <div
                  style={{
                    padding: "8px 12px",
                    background: "var(--blue-tint)",
                    border: "1px solid var(--blue-tint-strong)",
                    borderRadius: 6,
                    fontSize: 11,
                    color: "var(--text3)",
                    marginBottom: 16,
                  }}
                >
                  Some optional documents have not been uploaded. You can submit
                  the application now and upload them later if needed.
                </div>
              )}

              </div>

              {/* ── Document integrity ─────────────────────────── */}
              {(() => {
                const entries = product.documents
                  .map((d) => ({ d, u: uploads[d.id] }))
                  .filter(({ u }) => u?.file && u.automated);
                const checked = entries.filter(({ u }) => u.integrityDone && u.tampering);
                const pending = entries.filter(({ u }) => !u.integrityDone);
                if (entries.length === 0) return null;
                const flagged = checked.filter(({ u }) => u.tampering.risk_tier !== "LOW");
                return (
                  <div className="card">
                    <div className="section-head">
                      <Icon d={Icons.scanText} size={14} /> Document Integrity
                    </div>
                    {pending.length > 0 && (
                      <div className="alert-banner" style={{ color: "var(--text2)" }}>
                        <span className="spinner" style={{ width: 13, height: 13, flexShrink: 0 }} />
                        <span>
                          Running integrity checks on {pending.length} document{pending.length > 1 ? "s" : ""}…
                          This runs once, in the background — you can keep reviewing meanwhile.
                        </span>
                      </div>
                    )}
                    {pending.length === 0 && (
                    <div className={`alert-banner ${flagged.length ? "alert-amber" : "alert-green"}`}>
                      <Icon
                        d={flagged.length ? Icons.alert : Icons.check}
                        size={15}
                        style={{ color: flagged.length ? "var(--amber)" : "var(--green)", flexShrink: 0, marginTop: 1 }}
                      />
                      <span>
                        {flagged.length === 0
                          ? `All ${checked.length} checked document${checked.length > 1 ? "s" : ""} passed the integrity checks.`
                          : `${flagged.length} of ${checked.length} checked document${checked.length > 1 ? "s" : ""} need${flagged.length === 1 ? "s" : ""} a closer look before approval — expand ${flagged.length === 1 ? "it" : "each"} below to see exactly what and where.`}
                      </span>
                    </div>
                    )}
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      {checked.map(({ d, u }) => {
                        const t = u.tampering;
                        const st = TAMPER_STYLE[t.risk_tier] || TAMPER_STYLE.MEDIUM;
                        const flag = t.risk_tier !== "LOW";
                        return (
                          <div
                            key={d.id}
                            style={{
                              padding: "10px 12px",
                              borderRadius: 8,
                              background: flag ? st.bg : "var(--surface2)",
                              border: `1px solid ${flag ? st.color : "var(--border)"}`,
                              display: "flex",
                              gap: 10,
                            }}
                          >
                            <FileThumb
                              upload={u} size={46}
                              secure
                              secureSrc={masked[d.id]?.src || null}
                              secureLoading={!!masked[d.id]?.loading}
                              secureError={!!masked[d.id]?.error}
                              onSecureHover={() => ensureMasked(d.id)}
                            />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                                <span style={{ fontWeight: 600, color: "var(--text1)" }}>{d.label}</span>
                                <span
                                  style={{
                                    marginLeft: "auto",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: 4,
                                    fontSize: 10,
                                    fontWeight: 600,
                                    color: st.color,
                                    background: "var(--surface)",
                                    border: `1px solid ${st.color}`,
                                    borderRadius: 999,
                                    padding: "2px 9px",
                                  }}
                                >
                                  <Icon d={st.icon} size={11} /> {t.risk_tier === "LOW" ? "Integrity OK" : t.risk_tier}
                                </span>
                              </div>
                              {u.fileName && (
                                <div style={{ fontSize: 9.5, color: "var(--text3)", marginTop: 1 }}>{u.fileName}</div>
                              )}
                              {flag ? (
                                <IntegrityDetail upload={u} st={st} />
                              ) : (
                                <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 3 }}>
                                  No integrity concerns found in this document.
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}

              {/* ── Identity verification (face match) ──────────── */}
              {faceCheck && (() => {
                const FACE_STYLE = {
                  match: { ...TIER.good, bg: "alert-green", text: "Same person on all documents" },
                  mismatch: { ...TIER.bad, bg: "alert-red", text: "Face mismatch between documents" },
                  uncertain: { ...TIER.warn, bg: "alert-amber", text: "Face match inconclusive — needs a human look" },
                  insufficient: { ...TIER.info, bg: "alert-blue", text: "Not enough faces to compare" },
                };
                const lbl = (id) => product.documents.find((x) => x.id === id)?.label || id;
                const rotText = (r) => r === 0 ? "upright" : r === 180 ? "upside down in the document" : "sideways in the document";
                const data = faceCheck.data;
                const fs = data ? (FACE_STYLE[data.overall] || FACE_STYLE.insufficient) : null;
                return (
                  <div className="card">
                    <div className="section-head">
                      <Icon d={Icons.userSearch} size={14} /> Identity Verification — Face Match
                    </div>
                    {faceCheck.loading && (
                      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text3)" }}>
                        <span className="spinner" style={{ width: 13, height: 13 }} />
                        Comparing the faces across your documents…
                      </div>
                    )}
                    {faceCheck.error && (
                      <div style={{ fontSize: 12, color: "var(--red)" }}>{faceCheck.error}</div>
                    )}
                    {data && (
                      <>
                        <div className={`alert-banner ${fs.bg}`}>
                          <Icon d={fs.icon} size={15} style={{ color: fs.color, flexShrink: 0, marginTop: 1 }} />
                          <span><strong>{fs.text}.</strong> {data.summary}</span>
                        </div>

                        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
                          {data.documents.map((doc) => {
                            const face = doc.faces[0];
                            return (
                              <div key={doc.doc_type}
                                style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px",
                                  background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: 8 }}>
                                {face?.thumb ? (
                                  <img src={face.thumb} alt={`Face on ${lbl(doc.doc_type)}`}
                                    style={{ width: 44, height: 44, objectFit: "cover", borderRadius: 6,
                                      border: `2px solid ${doc.faces.length ? "var(--green)" : "var(--border)"}` }} />
                                ) : (
                                  <div style={{ width: 44, height: 44, borderRadius: 6, background: "var(--surface3)",
                                    display: "flex", alignItems: "center", justifyContent: "center" }}>
                                    <Icon d={Icons.userRound} size={20} style={{ color: "var(--text-muted)" }} />
                                  </div>
                                )}
                                <div style={{ fontSize: 11 }}>
                                  <div style={{ fontWeight: 600, color: "var(--text1)" }}>{lbl(doc.doc_type)}</div>
                                  {face ? (
                                    <div style={{ color: "var(--text3)" }}>
                                      Face found · {rotText(face.rotation_to_upright)}
                                      {face.quality === "low" && <span style={{ color: "var(--amber)" }}> · low photo quality</span>}
                                    </div>
                                  ) : (
                                    <div style={{ color: "var(--text3)" }}>
                                      {doc.error ? doc.error : "No face on this document (normal for financial documents)"}
                                    </div>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>

                        {data.pairs.length > 0 && (
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {data.pairs.map((p, i) => {
                              const pc = p.verdict === "match" ? "var(--green)"
                                : p.verdict === "different" ? "var(--red)" : "var(--amber)";
                              const pct = Math.round(Math.min(1, Math.max(0, p.similarity)) * 100);
                              return (
                                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 11.5 }}>
                                  <span style={{ color: "var(--text2)", minWidth: 0, flexShrink: 1 }}>
                                    {lbl(p.a)} ↔ {lbl(p.b)}
                                  </span>
                                  <div style={{ flex: 1, maxWidth: 160, height: 6, background: "var(--surface3)",
                                    borderRadius: 999, overflow: "hidden" }}>
                                    <div style={{ width: `${pct}%`, height: "100%", background: pc }} />
                                  </div>
                                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text2)", fontSize: 10.5 }}>
                                    {p.similarity.toFixed(2)}
                                  </span>
                                  <span style={{ fontSize: 10, fontWeight: 600, color: pc, textTransform: "uppercase" }}>
                                    {p.verdict === "match" ? "Same person" : p.verdict === "different" ? "Different" : "Unclear"}
                                  </span>
                                </div>
                              );
                            })}
                            {data.pairs.some((p) => p.note) && (
                              <div style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 2 }}>
                                {data.pairs.find((p) => p.note).note}
                              </div>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                );
              })()}

              {/* ── Selfie liveness (live person + same as the ID photo) ── */}
              <SelfieLivenessCard isStaff={isStaff} />

              {/* ── Case-level agents: cross-check, dedupe, fraud fusion, policy ── */}
              {isStaff && <CaseAssessmentCard caseId={caseId} role={role} loanType={product?.label} formData={formData} />}

              {/* ── Agent Activity: every agent that worked on this application ── */}
              {(showAgentActivity || !isStaff) && <AgentActivityPanel caseId={caseId} role={role} defaultOpen={isStaff} />}

              {/* ── Information from documents ──────────────────── */}
              <div className="card">
                <div className="section-head">Information From Your Documents</div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 6,
                  marginBottom: 16,
                }}
              >
                {dedupedDocFields.map(({ field, data, docLabel }) => (
                  <div
                    key={field}
                    style={{
                      fontSize: 11,
                      padding: "6px 8px",
                      background: "var(--surface2)",
                      borderRadius: 4,
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <span style={{ color: "var(--text3)", flex: 1, minWidth: 0 }}>
                      {field}
                      <span style={{ color: "var(--text-muted, var(--text3))", fontWeight: 400 }}>
                        {" "}· {docLabel}
                      </span>
                    </span>
                    <span
                      style={{
                        color: data.value == null || data.value === "" ? "var(--red)" : "var(--text1)",
                        fontWeight: 500,
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        fontStyle: data.value == null || data.value === "" ? "italic" : "normal",
                      }}
                    >
                      {data.value == null || data.value === "" ? "Not found" : String(data.value)}
                    </span>
                    <span>
                      {data.value == null || data.value === "" || data.flag === "error" ? (
                        <Icon d={Icons.x} size={11} style={{color:"var(--red)"}} />
                      ) : data.flag === "warn" ? (
                        <Icon d={Icons.alert} size={11} style={{color:"var(--amber)"}} />
                      ) : (
                        <Icon d={Icons.check} size={11} style={{color:"var(--green)"}} />
                      )}
                    </span>
                  </div>
                ))}
              </div>
              </div>

              {/* ── Consent ────────────────────────────────────── */}
              <div className="card">
                <div className="section-head">Consent &amp; Declaration</div>
                {(isStaff ? [
                  "The customer confirms all information provided is correct and complete.",
                  "The customer consents to verification of documents and information provided.",
                  "The customer authorizes us to obtain their credit bureau report.",
                  "The customer consents to receive communication via SMS, email, and WhatsApp.",
                  "The customer understands this is an application and does not guarantee loan approval.",
                ] : [
                  "I confirm all information provided is correct and complete.",
                  "I consent to verification of documents and information provided.",
                  "I authorize you to obtain my credit bureau report.",
                  "I consent to receive communication via SMS, email, and WhatsApp.",
                  "I understand this is an application and does not guarantee loan approval.",
                ]).map((text, i) => (
                  <label
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      cursor: "pointer",
                      padding: "6px 0",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={!!consent[i]}
                      onChange={() => toggleConsent(i)}
                      style={{ accentColor: "var(--blue)" }}
                    />
                    <span style={{ fontSize: 12, color: "var(--text2)" }}>
                      {text}
                    </span>
                  </label>
                ))}
              </div>

              {isStaff && (
                <div className="card">
                  <div className="section-head">
                    <Icon d={Icons.userCheck} size={14} /> {role === "head" ? "Final Decision (HQ)" : "Officer Recommendation"}
                  </div>
                  <textarea
                    className="form-input"
                    rows={2}
                    placeholder={role === "head" ? "Add decision note..." : "Add remarks..."}
                    value={decisionNote}
                    onChange={(e) => setDecisionNote(e.target.value)}
                    style={{ marginBottom: 8, fontSize: 12, resize: "vertical" }}
                  />
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {role === "head" ? (
                      <>
                        <button className="btn btn-primary" style={{ background: "var(--green)", fontSize: 12 }}><Icon d={Icons.check} size={12} /> Final Approve</button>
                        <button className="btn btn-danger" style={{ fontSize: 12 }}><Icon d={Icons.x} size={12} /> Final Reject</button>
                        <button className="btn btn-secondary" style={{ fontSize: 12 }}><Icon d={Icons.chevronL} size={12} /> Send Back to Officer</button>
                      </>
                    ) : (
                      <>
                        <button className="btn btn-primary" style={{ background: "var(--green)", fontSize: 12 }}><Icon d={Icons.check} size={12} /> Recommend Approve</button>
                        <button className="btn btn-danger" style={{ fontSize: 12 }}><Icon d={Icons.x} size={12} /> Recommend Reject</button>
                        <button className="btn btn-secondary" style={{ fontSize: 12 }}><Icon d={Icons.policy} size={12} /> Request More Documents</button>
                      </>
                    )}
                  </div>
                </div>
              )}

              {/* ── Actions ────────────────────────────────────── */}
              <div className="card" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                {!allConsents && (
                  <span style={{ fontSize: 11.5, color: "var(--amber)", display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Icon d={Icons.alert} size={13} /> Accept all declarations above to submit.
                  </span>
                )}
                <div style={{ display: "flex", gap: 8, marginLeft: "auto", flexWrap: "wrap" }}>
                  <button className="btn btn-ghost" onClick={() => setStep(3)}>
                    Back
                  </button>
                  <button
                    className="btn btn-ghost"
                    onClick={() => {
                      alert("Application saved as draft.");
                      setPage(listPage);
                    }}
                  >
                    <Icon d={Icons.download} size={13} /> Save Draft
                  </button>
                  <button
                    className="btn btn-primary"
                    disabled={!allConsents}
                    onClick={handleSubmit}
                  >
                    <Icon d={Icons.send} size={13} /> {isStaff ? "Submit for Underwriting" : "Submit Application"}
                  </button>
                </div>
              </div>
            </>
          )}
        </>
      )}

      {submitted && (
        <div
          style={{
            textAlign: "center",
            padding: "60px 20px",
          }}
        >
          <Icon d={storeStatus?.state === "error" ? Icons.alert : Icons.sparkles} size={48}
                style={{color: storeStatus?.state === "error" ? "var(--red)" : "var(--blue)", marginBottom:16}} />
          <div
            style={{
              fontSize: 20,
              fontWeight: 700,
              color: "var(--text1)",
              fontFamily: "var(--font-sans)",
              marginBottom: 8,
            }}
          >
            {storeStatus?.state === "ok" ? "Application Submitted Successfully!"
              : storeStatus?.state === "error" ? "Your application was not submitted"
              : "Submitting your application…"}
          </div>
          <div
            style={{
              fontSize: 13,
              color: "var(--text2)",
              marginBottom: 16,
            }}
          >
            Your application ID is{" "}
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontWeight: 700,
                color: "var(--blue)",
                fontSize: 14,
              }}
            >
              {appId}
            </span>
          </div>
          {/* the save's outcome — visible, so a failure is never silent */}
          {storeStatus && (
            <div data-submit-state={storeStatus.state} style={{ fontSize: 12, marginBottom: 14, lineHeight: 1.6,
              color: storeStatus.state === "error" ? "var(--red)"
                   : storeStatus.state === "ok" ? "var(--green)" : "var(--text3)" }}>
              {storeStatus.state === "saving" && (
                <><span className="spinner" style={{ width: 11, height: 11, marginRight: 6 }} />
                  Uploading your documents and saving the application…</>
              )}
              {storeStatus.state === "saving" && slowSave && (
                <div style={{ color: "var(--text3)", marginTop: 6 }}>
                  Still working — large files take a little longer. Please keep this page open.
                </div>
              )}
              {storeStatus.state === "ok" && (
                <>✓ Saved with {storeStatus.rows} document{storeStatus.rows === 1 ? "" : "s"}. It is now with an underwriting officer.
                  {storeStatus.masking > 0 && (
                    <div style={{ color: "var(--text3)" }}>
                      Privacy-masked copies of {storeStatus.masking} document{storeStatus.masking === 1 ? "" : "s"} are being prepared for the officer.
                    </div>
                  )}
                </>
              )}
              {storeStatus.state === "error" && `⚠ ${storeStatus.error}`}
            </div>
          )}
          {storeStatus?.state === "error" ? (
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button className="btn btn-primary" onClick={handleSubmit}>
                <Icon d={Icons.refresh} size={13} /> Try again
              </button>
              <button className="btn btn-ghost" onClick={() => { setSubmitted(false); setStoreStatus(null); }}>
                Back to review
              </button>
            </div>
          ) : storeStatus?.state === "ok" ? (
            <div
              style={{
                padding: "12px 16px",
                background: "var(--blue-tint)",
                border: "1px solid var(--blue-tint-strong)",
                borderRadius: 8,
                fontSize: 12,
                color: "var(--text3)",
                maxWidth: 400,
                margin: "0 auto",
              }}
            >
              Redirecting to {isStaff ? "Applications" : "My Applications"}...{" "}
              <a href="#" onClick={(e) => { e.preventDefault(); clearTimeout(submitTimer.current); setPage(listPage); }}
                 style={{ color: "var(--blue)" }}>Go now</a>
            </div>
          ) : null}
        </div>
      )}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
