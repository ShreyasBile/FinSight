// Calls the local FinSight OCR service (api/) instead of the browser hitting
// Anthropic directly — the key/PII now stay server-side and there is no CORS issue.
// Set VITE_OCR_API_URL to point at a deployed service; defaults to localhost:8000.
const OCR_API = import.meta.env?.VITE_OCR_API_URL || "http://localhost:8000";

// Agent context: the wizard's case id (minted once per application draft) and
// the acting role. Sent as headers on every document call so all agent steps
// for one application land in one case file / one process-mining case.
let agentCtx = {};
export function setAgentContext(ctx) { agentCtx = { ...agentCtx, ...ctx }; }
// `ctx` overrides the shared context for one call. The shared one changes whenever another
// wizard mounts (switching role opens the officer's New Application), so a request that outlives
// its screen - the submit, which masks every document before storing - must carry its own.
function agentHeaders(extra = {}, ctx = null) {
  const c = ctx || agentCtx;
  const h = { ...extra };
  if (c.caseId) h["X-Case-Id"] = c.caseId;
  if (c.role) h["X-Actor-Role"] = c.role;
  if (c.actorId) h["X-Actor-Id"] = c.actorId;
  return h;
}

export async function runOCR(file, docType) {
  const form = new FormData();
  form.append("doc_type", docType);
  form.append("file", file);

  const resp = await fetch(`${OCR_API}/extract`, { method: "POST", body: form, headers: agentHeaders() });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `OCR service error ${resp.status}`);
  }

  const data = await resp.json();
  // /extract does preprocessing + field extraction (fast now — ~6-9s prep, see
  // preprocess.py) and returns the cleaned/auto-oriented render. The slow
  // tampering cascade is deferred to analyzeIntegrity() on the Review & Submit
  // step, so a plain upload doesn't wait on it.
  return {
    raw: data.raw || "",
    parsed: data.parsed || null,
    fields: data.fields || [],
    confidence: data.confidence ?? null,
    needsReview: data.needs_review ?? false,
    provider: data.provider,
    // Auto-oriented/warped render the VLM actually read (data URI). Null for
    // PDFs or if preprocessing was off/failed — callers fall back to the raw
    // upload. Basis for the hover preview and the deferred PII masking.
    cleanedPreview: data.cleaned_preview || null,
    // {preprocess_ms, extract_ms} — folded into the application audit trail.
    timings: data.timings || {},
  };
}

// Deferred tampering analysis for the Review & Submit page — run on the
// ORIGINAL file bytes, once per document when the borrower reaches the step
// (the upload step already got the fields + cleaned preview from runOCR).
export async function analyzeIntegrity(file, docType, parsed) {
  const form = new FormData();
  form.append("doc_type", docType);
  form.append("file", file);
  form.append("parsed_json", JSON.stringify(parsed || {}));

  const resp = await fetch(`${OCR_API}/document/integrity`, { method: "POST", body: form, headers: agentHeaders() });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Integrity service error ${resp.status}`);
  }
  const data = await resp.json();
  return { tampering: data.tampering || null, timings: data.timings || {} };
}

// On-demand plain-language + visually-grounded explanation for a MEDIUM/HIGH
// document. Separate from runOCR() so a plain upload never pays this call's
// latency (~3-4s) — the frontend only calls this when the user explicitly
// asks to see the detailed breakdown (a toggle shown on flagged documents).
export async function explainTampering(file, docType, tampering) {
  const form = new FormData();
  form.append("doc_type", docType);
  form.append("tampering_json", JSON.stringify(tampering));
  form.append("file", file);

  const resp = await fetch(`${OCR_API}/tampering/explain`, { method: "POST", body: form, headers: agentHeaders() });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Explanation service error ${resp.status}`);
  }
  return resp.json(); // { summary, issues }
}

// On-demand OCR-grounded localization for a MEDIUM/HIGH document: returns the
// document rendered with numbered boxes over each detected issue (found on the
// page by OCR), plus a legend mapping each box number to its finding. Shown on
// the Review & Submit page so a reviewer can see WHERE each issue is.
export async function locateTampering(file, docType, tampering) {
  const form = new FormData();
  form.append("doc_type", docType);
  form.append("tampering_json", JSON.stringify(tampering));
  form.append("file", file);

  const resp = await fetch(`${OCR_API}/tampering/locate`, { method: "POST", body: form, headers: agentHeaders() });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Localization service error ${resp.status}`);
  }
  return resp.json(); // { annotations:[{n,text,located}], annotated_image, located_count, ocr_tokens }
}

// Cross-document face verification for the Review & Submit page: sends every
// uploaded document in one call; the backend finds the face on each (any
// orientation), embeds it, and reports pairwise similarity + whether the same
// person appears on all documents. entries: [{ docType, file }]
export async function verifyFaces(entries) {
  const form = new FormData();
  form.append("doc_types", entries.map((e) => e.docType).join(","));
  for (const e of entries) form.append("files", e.file);

  const resp = await fetch(`${OCR_API}/face/verify`, { method: "POST", body: form, headers: agentHeaders() });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Face verification error ${resp.status}`);
  }
  return resp.json(); // { documents, pairs, overall, summary }
}

// Selfie liveness (Liveness agent): live person vs a photo of a printout or a
// screen, and a match against the ID photo already on this case (X-Case-Id).
export async function checkLiveness(file) {
  const form = new FormData();
  form.append("file", file, file.name || "selfie.jpg");
  const resp = await fetch(`${OCR_API}/liveness/check`, { method: "POST", body: form, headers: agentHeaders() });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Selfie check error ${resp.status}`);
  }
  return resp.json(); // { verdict, live, live_score, match, quality, notes }
}

// Privacy-masked render of a document for the hover preview: Aadhaar / PAN /
// account numbers redacted to their last 4 digits. Fetched lazily on first
// hover (the OCR pass is a couple of seconds), so a document nobody looks at
// never pays for it. `file` is usually the cleaned (preprocessed) image as a
// Blob — see ensureMasked() in UserNewApplication.jsx — but any File/Blob works.
export async function maskDocument(file, parsed, docType = "generic", ctx = null) {
  const form = new FormData();
  form.append("file", file);
  form.append("parsed_json", JSON.stringify(parsed || {}));
  form.append("doc_type", docType);

  const resp = await fetch(`${OCR_API}/document/mask`, { method: "POST", body: form, headers: agentHeaders({}, ctx) });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Masking service error ${resp.status}`);
  }
  return resp.json(); // { masked_image, masked_count, labels, timings:{mask_ms} }
}

// Persist a submitted application to the backend audit store (storage/ folder +
// applications.xlsx, with Manager/Officer sheets). `payload` matches
// ApplicationStoreRequest — see buildStorePayload() in UserNewApplication.jsx.
// Best-effort: a storage failure must not block the user's submission.
export async function storeApplication(payload, ctx = null) {
  const resp = await fetch(`${OCR_API}/application/store`, {
    method: "POST",
    headers: agentHeaders({ "Content-Type": "application/json" }, ctx),
    body: JSON.stringify({ ...payload, case_id: payload.case_id || (ctx || agentCtx).caseId || null }),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}));
    throw new Error(e?.detail || `Storage service error ${resp.status}`);
  }
  return resp.json(); // { ok, dir, xlsx, rows, error }
}
